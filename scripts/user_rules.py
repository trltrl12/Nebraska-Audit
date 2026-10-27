"""Apply the user's naming and categorizing decisions to extracted JSON (output/json/<stem>.json).

Run after every extraction, before validating:
    python3 scripts/user_rules.py <stem> [<stem> ...]      # or --all
    python3 scripts/user_rules.py --check --all             # report what would change, write nothing

The rules are the ones that apply to all reports (CLAUDE.md "User ... decisions"). One-report rulings
(Lincoln, Lincoln Public Schools, Douglas County) are not here. Every rule is idempotent: running it on
data that already follows the rules changes nothing.
"""
import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JSON_DIR = ROOT / "output" / "json"

# --- revenue labels (receipts[] and detail[] revenue lines); printed text kept in printed_label ---
REV_LABELS = [
    (r"^(taxes:\s*)?sales and use( tax)?$", "Sales and Use Tax", None),
    (r"^(taxes:\s*)?(property|property tax|real property and personal property tax|property and real estate( taxes)?)$",
     "Property and Real Estate Taxes", None),
    (r"^(taxes:\s*)?occupation( tax)?$", "Occupation Tax", None),
    (r"^investment (income|earnings)$", "Investment Earnings", None),
    (r"^(taxes:\s*)?turnback( tax)?$", "Turnback", "Turnback"),
    (r"^(taxes:\s*)?sundry and in lieu( tax)?$", "Sundry and in Lieu", None),
]
REIMB_SERVICES = re.compile(r"^(\d+ )?reimbursements? for services$", re.I)
TRANSFER_LABEL = re.compile(r"^transfers? (in|out)$", re.I)
CATEGORICAL_PRIVATE = re.compile(r"categorical.*(corporat|private)", re.I)
CAPITAL_OUTLAY = re.compile(r"capital outlay", re.I)
DEBT = re.compile(r"debt|bond|lease|principal|interest", re.I)
FUNCTION_NAMES = {
    "general government": "General Government", "public safety": "Public Safety",
    "streets/highways": "Streets and Highways", "culture/recreation": "Culture and Recreation",
    "health/welfare": "Health and Welfare", "economic development": "Economic Development",
    "utilities": "Utilities", "natural resources/conservation": "Natural Resources and Conservation",
    "housing": "Housing", "agriculture/fair": "Agriculture and Fair", "hospital/health care": "Hospital and Health Care",
    "instruction": "Instruction", "support services": "Support Services", "food service": "Food Service",
    "student transportation": "Student Transportation", "student activities": "Student Activities", "other": "Other",
}

# --- balance sheet ---
DEFERRED = {
    "deferred_outflows": [(r"refund", "Deferred Loss on Refunding"),
                          (r"\bOPEB\b|postemployment", "Deferred Outflows for Other Postemployment Benefits"),
                          (r"pension|cash balance plan", "Deferred Outflows for Pensions")],
    "deferred_inflows": [(r"liabilit", None),
                         (r"refund", "Deferred Gain on Refunding"),
                         (r"leas|lessor", "Deferred Inflows for Leases"),
                         (r"\bOPEB\b|postemployment", "Deferred Inflows for Other Postemployment Benefits"),
                         (r"pension", "Deferred Inflows for Pensions")],
}
DUE_SPLIT = ["Bonds Payable", "Leases Payable", "Subscriptions", "Compensated Absences", "Landfill Postclosure Costs",
             "Workers' Compensation Claims", "Other Postemployment Benefit Liability"]
DUE_SPLIT_RE = re.compile(r"^(" + "|".join(re.escape(x) for x in DUE_SPLIT) + r")\s*-\s*(due within one year|due in more than one year)$", re.I)
DUE_PARTS = {"due within one year": "Due Within One Year", "due in more than one year": "Due in More Than One Year"}
NICA = re.compile(r"invest\w* (\(deficit\) )?in capital", re.I)
STATEMENT_SCHEDULE = re.compile(r"^(statement|exhibit|governmental funds statement|proprietary funds? statement)", re.I)


def label_of(x):
    return (x.get("printed_label") or x.get("original_label") or "").strip()


def relabel(x, new, why, log):
    if x.get("original_label") != new:
        x.setdefault("printed_label", x.get("original_label"))
        x["original_label"] = new
        log[why] += 1


def setcat(x, key, new, why, log):
    if x.get(key) != new:
        x[key] = new
        log[why] += 1


def revenue_rules(x, log):
    lab = label_of(x)
    for pat, new, cat in REV_LABELS:
        if re.match(pat, lab, re.I):
            relabel(x, new, "label: " + new, log)
            if cat:
                setcat(x, "category", cat, "category: " + cat, log)
            return
    if REIMB_SERVICES.match(lab):
        setcat(x, "category", "Reimbursement for Services", "category: Reimbursement for Services", log)


def spending_rules(x, log):
    cat = x.get("category") or ""
    if cat in ("debt service principal", "debt service interest"):
        x.setdefault("debt_service_part", cat.split()[-1])
        setcat(x, "category", "Debt Service", "category: Debt Service", log)
        return
    lab = x.get("original_label") or ""
    if cat in ("Instruction", "instruction") and CATEGORICAL_PRIVATE.search(lab):
        setcat(x, "category", "Categorical Corporation and Private", "category: Categorical Corporation and Private", log)
        return
    # capital outlay inside a function/department -> category capital outlay, "Capital Outlay: <function>"
    if CAPITAL_OUTLAY.search(lab) and not DEBT.search(cat + " " + lab) and cat != "capital outlay":
        fn = FUNCTION_NAMES.get(cat.lower(), cat)
        x["computed"] = (x["computed"] + "; " if x.get("computed") else "") + f"capital outlay under function {cat}"
        setcat(x, "category", "capital outlay", "category: capital outlay", log)
        relabel(x, f"Capital Outlay: {fn}", "label: Capital Outlay: <function>", log)


def other_financing_rules(x, log):
    m = TRANSFER_LABEL.match((x.get("original_label") or "").strip())
    if m:
        relabel(x, "Transfers In" if m.group(1).lower() == "in" else "Transfers Out", "label: Transfers In/Out", log)


def fund_name_rule(x, log, per_fund=True):
    """Per-fund lines taken from a statement's fund columns: fund names end in "Fund"."""
    f = (x.get("fund") or "").strip()
    if f and not re.search(r"fund", f, re.I) and per_fund and STATEMENT_SCHEDULE.match(x.get("schedule") or ""):
        x["fund"] = f + " Fund"
        log["fund name + Fund"] += 1


def balance_sheet_rules(bs, log):
    for sec, rules in DEFERRED.items():
        for x in bs.get(sec) or []:
            if not isinstance(x, dict):
                continue
            for pat, new in rules:
                if re.search(pat, label_of(x), re.I):
                    if new:
                        setcat(x, "category", new, "category: " + new, log)
                    break
    for x in bs.get("liabilities") or []:
        if not isinstance(x, dict):
            continue
        lab = label_of(x)
        m = DUE_SPLIT_RE.match(lab)
        if m:
            name = next(n for n in DUE_SPLIT if n.lower() == m.group(1).lower())
            setcat(x, "category", name, "category: " + name, log)
            relabel(x, DUE_PARTS[m.group(2).lower()], "label: Due ... part", log)
        elif re.match(r"^due to pension trust funds?$", lab, re.I):
            setcat(x, "category", "Due to Pension Trust Fund", "category: Due to Pension Trust Fund", log)
    for x in bs.get("equity") or []:
        if isinstance(x, dict) and NICA.search(label_of(x)):
            setcat(x, "category", "Net Investment in Capital Assets", "category: Net Investment in Capital Assets", log)


def apply(d):
    log = Counter()
    # fund-name rule: only reports whose detail was built per fund from the statements (cities and villages)
    per_fund = "per-fund lines from the fund statements" in (d.get("notes") or "") or bool(d.get("_per_fund"))
    for x in d.get("receipts") or []:
        revenue_rules(x, log)
    for x in d.get("disbursements") or []:
        spending_rules(x, log)
    for x in d.get("detail") or []:
        sec = x.get("section")
        if sec == "revenue":
            revenue_rules(x, log)
        elif sec == "expenditure":
            spending_rules(x, log)
        elif sec == "other financing":
            other_financing_rules(x, log)
        fund_name_rule(x, log, per_fund)
    for x in d.get("detail_totals") or []:
        fund_name_rule(x, log, per_fund)
    if isinstance(d.get("balance_sheet"), dict):
        balance_sheet_rules(d["balance_sheet"], log)
    return log


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("stems", nargs="*")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--check", action="store_true", help="report changes without writing")
    a = ap.parse_args()
    files = sorted(JSON_DIR.glob("*.json")) if a.all else [JSON_DIR / f"{s.removesuffix('.json')}.json" for s in a.stems]
    total, changed = Counter(), 0
    for f in files:
        try:
            d = json.loads(f.read_text())
        except Exception:
            continue
        if not isinstance(d, dict):
            continue
        log = apply(d)
        if log:
            changed += 1
            total.update(log)
            if a.check:
                print(f"{f.stem}: {dict(log)}")
            else:
                f.write_text(json.dumps(d, indent=2, ensure_ascii=False) + "\n")
    for k, v in total.most_common():
        print(f"{v:6} {k}")
    print(f"{'would change' if a.check else 'changed'} {changed} of {len(files)} reports")


if __name__ == "__main__":
    sys.exit(main())
