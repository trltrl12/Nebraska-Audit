"""Apply the user's naming and categorizing decisions to extracted JSON (output/json/<stem>.json).

Run after every extraction, before validating:
    python3 scripts/user_rules.py <stem> [<stem> ...]      # or --all
    python3 scripts/user_rules.py --check --all             # report what would change, write nothing

The rules are the ones that apply to all reports (CLAUDE.md "User ... decisions"), plus the entity
rulings for Lincoln, Clay Center, Lincoln Public Schools and Douglas County, matched by entity (the stem
without _FY<year>), so they apply to every year. Every rule is idempotent: running it on data that
already follows the rules changes nothing.
"""
import argparse
import csv
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
     "Property Tax", None),
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

# --- utility (business-type) spending listed by cost type: category = the cost type (user 2026-10-28) ---
# The fund column (Water Fund, Sewer Fund...) still says which utility; function lines (Water Distribution,
# Sewer Treatment, not itemized) keep "utilities". The original function is kept in `function`.
OBJECT_FROM_DETAIL = {
    "salaries and wages": "Salaries and Wages", "employee benefits and payroll taxes": "Employee Benefits and Payroll Taxes",
    "professional/contract services": "Professional/Contract Services", "supplies and materials": "Supplies and Materials",
    "utilities (power, gas, phone)": "utilities", "fuel": "Fuel", "Fuel / Vehicle": "Fuel",
    "repairs and maintenance": "Repairs and Maintenance", "insurance": "Insurance", "depreciation": "Depreciation",
    "dues, training and travel": "Dues, Training and Travel", "printing, publishing and postage": "Printing, Publishing and Postage",
    "rent/leases": "Rent/Leases", "purchased power/cost of sales": "Purchased Power/Cost of Sales",
    "grants/payments to other entities": "Grants/Payments to Other Entities",
    "tax refunds/treasurer fees": "Tax Refunds/Treasurer Fees", "equipment purchases": "Equipment Purchases",
    "miscellaneous expenditure": "other", "Other Taxes Paid": "Other Taxes Paid", "Other / Miscellaneous Expenses": "other",
}
OBJECT_LABELS = [  # label (whole line is one cost type) -> category; first match wins
    (r"purchased power|power purchase|cost of (sales|goods|power)|wholesale power", "Purchased Power/Cost of Sales"),
    (r"depreciation|amortization", "Depreciation"),
    (r"benefit|payroll tax|retirement|pension|fica|social security|health insurance", "Employee Benefits and Payroll Taxes"),
    (r"salar|wages|personal services|personnel|payroll|compensation", "Salaries and Wages"),
    (r"insurance", "Insurance"),
    (r"repair|maint", "Repairs and Maintenance"),
    (r"professional|contract|consult|legal|audit|engineering|accounting", "Professional/Contract Services"),
    (r"dues|training|travel|meeting|education|mileage", "Dues, Training and Travel"),
    (r"print|publish|postage|advertis", "Printing, Publishing and Postage"),
    (r"^fuel|gas and oil|gasoline|vehicle", "Fuel"),
    (r"purchased services", "Professional/Contract Services"),
    (r"purchased water", "Purchased Power/Cost of Sales"),
    (r"bad debt", "other"),
    (r"suppl|material|office|chemical|commodit", "Supplies and Materials"),
    (r"^rent|lease", "Rent/Leases"),
    (r"^(utilities|utility expense|telephone|electricity)$", "utilities"),
    (r"^(misc\w*|other|other expenses?|miscellaneous expenses?)$", "other"),
]
UTILITY_FUNCTION_LABEL = re.compile(r"water|sewer|electric|light|gas system|refuse|sanitation|garbage|solid waste|trash|landfill"
                                    r"|distribution|treatment|pumping|transmission|production|generation|operating|operations"
                                    r"|debt|bond|interest|capital|transfer", re.I)


TAX_PAID = re.compile(r"^(?!.*(payroll|salar|wage|benefit)).*(\btax(es)?\b|franchise fee|in lieu of tax)", re.I)


def utility_object(x, borrowed=None):
    lab = re.sub(r"^\d+\s+", "", label_of(x)).strip()
    if TAX_PAID.search(lab):
        return "Other Taxes Paid"
    dc = x.get("detail_category")
    if dc in OBJECT_FROM_DETAIL:
        return OBJECT_FROM_DETAIL[dc]
    if borrowed:
        return borrowed
    if re.search(r"insurance", lab, re.I) and not re.search(r"health|group|benefit", lab, re.I):
        return "Insurance"
    if UTILITY_FUNCTION_LABEL.search(lab) and not re.match(r"^(utilities|utility expense)$", lab, re.I):
        return None
    for pat, cat in OBJECT_LABELS:
        if re.search(pat, lab, re.I):
            return cat
    return None


def utility_object_rule(d, log):
    det = [x for x in d.get("detail") or [] if x.get("section") == "expenditure" and x.get("fund_type") == "business-type"]
    by_label = {}
    for x in det:
        if x.get("category") == "utilities" or x.get("function") == "utilities":
            c = utility_object(x)
            if c:
                by_label.setdefault(label_of(x).lower(), set()).add(c)
    for x in det + [x for x in d.get("disbursements") or [] if x.get("fund_type") == "business-type"]:
        if x.get("category") != "utilities":
            continue
        b = by_label.get(label_of(x).lower())
        c = utility_object(x, next(iter(b)) if b and len(b) == 1 else None)
        if c and c != "utilities":
            x["function"] = "utilities"
            setcat(x, "category", c, "utility cost type: " + c, log)


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
# --- the user's category decisions for other/miscellaneous lines (config/category_decisions.csv) ---
DECISIONS_CSV = ROOT / "config" / "category_decisions.csv"
OTHER_CATS = {"other", "other taxes"}
OTHER_DCATS = {"other local taxes", "miscellaneous revenue", "miscellaneous expenditure"}
norm = lambda s: re.sub(r"\s+", " ", re.sub(r"[^a-z ]", " ", s.lower())).strip()


def load_decisions():
    if not DECISIONS_CSV.exists():
        return {}
    with DECISIONS_CSV.open(newline="") as fh:
        return {(r["section"], r["label_normalized"]): (r["detail_category"], r["category (revenue)"])
                for r in csv.DictReader(fh)}


DECISIONS = load_decisions()
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


def decision_rule(x, sec, log):
    """Lines still in other/miscellaneous get the user's decision for the same (normalized) label."""
    if x.get("category_decision") or not (x.get("category") in OTHER_CATS or x.get("detail_category") in OTHER_DCATS):
        return
    hit = DECISIONS.get((sec, norm(label_of(x))))
    if not hit:
        return
    dcat, cat = hit
    setcat(x, "detail_category", dcat, "decision: detail_category", log)
    if sec == "revenue" and cat:
        setcat(x, "category", cat, "decision: category", log)
    x["category_decision"] = "user (category_decisions.csv)"


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


# --- entity rulings (any year): entity = stem without _FY<year> / _B<yyyy> ---
def entity_of(stem):
    return re.sub(r"_(FY\d{4}|B\d{4})$", "", stem or "")


OWN_SPENDING = {  # label (after "Current: ") -> own disbursement category
    "Lincoln": {"equipment maintenance": "Equipment Maintenance", "interest": "Interest", "fiscal charges": "Fiscal Charges"},
    "*": {"mass transit": "Mass Transit", "bond retirement": "Bond Retirement",
          "leases and sbita principal": "Leases and SBITA", "leases and sbita interest": "Leases and SBITA",
          "developer purchased tif payments": "Developer Purchased TIF Payments"},
}
OWN_SPENDING_ENTITIES = {"Lincoln", "Clay_Center"}


def amt(x):
    a = x.get("amount")
    return a.get("amount") if isinstance(a, dict) else a


def entity_rules(d, ent, log):
    spend = [x for x in d.get("disbursements") or []] + [x for x in d.get("detail") or [] if x.get("section") == "expenditure"]
    rev_detail = [x for x in d.get("detail") or [] if x.get("section") == "revenue"]
    if ent in OWN_SPENDING_ENTITIES:
        table = {**OWN_SPENDING["*"], **OWN_SPENDING.get(ent, {})}
        for x in spend:
            key = re.sub(r"^current:\s*", "", (x.get("original_label") or "").strip(), flags=re.I).lower()
            if key in table:
                setcat(x, "category", table[key], "entity: " + table[key], log)
    if ent == "Lincoln":
        for x in spend:
            if (x.get("original_label") or "").strip().lower() == "exp_miscellaneous":
                relabel(x, "Miscellaneous", "entity: Exp_Miscellaneous -> Miscellaneous", log)
    if ent == "Lincoln_Public_Schools":
        for x in d.get("disbursements") or []:
            lab = (x.get("original_label") or "").strip().lower()
            if lab == "staff":
                setcat(x, "category", "Instruction", "entity: LPS Staff -> Instruction", log)
            elif lab == "other" and x.get("category") in ("Debt Service", "debt service", "debt service principal",
                                                          "debt service interest"):
                setcat(x, "category", "other", "entity: LPS Other out of Debt Service", log)
        cat_grants = None
        for x in rev_detail:
            lab = label_of(x).lower()
            general = (x.get("fund") or "").lower().startswith("general")
            if general and re.match(r"^3180 pro-?rate motor vehicle$", lab):
                setcat(x, "category", "intergovernmental/state aid", "entity: LPS 3180 Prorate -> state aid", log)
            elif re.match(r"^1925 categorical grants$", lab):
                setcat(x, "category", "Categorical Grants", "entity: LPS 1925 -> Categorical Grants", log)
                setcat(x, "detail_category", "Categorical Grants", "entity: LPS 1925 -> Categorical Grants", log)
                if general:
                    cat_grants = x
            elif general and re.match(r"^(5690 other receipts|5300 sale of property)$", lab):
                setcat(x, "category", "Other Non-Revenue Receipts", "entity: LPS 5690/5300 -> Other Non-Revenue Receipts", log)
        # statement "Grants and donations" -> Contributions and Donations from Private Sources + Categorical Grants
        rec = d.get("receipts") or []
        for i, x in enumerate(rec):
            if x.get("split_from") or not re.match(r"^grants and donations$", label_of(x), re.I) or not cat_grants:
                continue
            total, part = amt(x), amt(cat_grants)
            if not isinstance(total, (int, float)) or not isinstance(part, (int, float)) or not 0 < part < total:
                continue
            how = f"split of p{x.get('source_page')} Grants and donations {total:,.0f} using the General Fund schedule: "
            a = dict(x, category="donations/contributions", original_label="Contributions and Donations from Private Sources",
                     amount=total - part, split_from="Grants and donations",
                     computed=how + f"Contributions and Donations from Private Sources {total - part:,.0f}")
            b = dict(x, category="Categorical Grants", original_label="Categorical Grants", amount=part,
                     split_from="Grants and donations", computed=how + f"1925 Categorical Grants {part:,.0f}")
            if isinstance(x.get("amount"), dict):
                a["amount"] = dict(x["amount"], amount=total - part)
                b["amount"] = dict(x["amount"], amount=part)
            for y in (a, b):
                y.pop("printed_label", None)
            rec[i:i + 1] = [a, b]
            log["entity: LPS Grants and donations split"] += 1
            break
    if ent == "Douglas_County":
        for x in rev_detail:
            if re.match(r"^miscellaneous \(taxes\)$", label_of(x), re.I) and re.search(r"nonmajor|tourism", x.get("fund") or "", re.I):
                setcat(x, "category", "Miscellaneous Tax", "entity: Douglas nonmajor Miscellaneous (taxes) -> Miscellaneous Tax", log)


def apply(d, stem=""):
    log = Counter()
    # fund-name rule: only reports whose detail was built per fund from the statements (cities and villages)
    per_fund = "per-fund lines from the fund statements" in (d.get("notes") or "") or bool(d.get("_per_fund"))
    for x in d.get("receipts") or []:
        decision_rule(x, "revenue", log)
        revenue_rules(x, log)
    for x in d.get("disbursements") or []:
        decision_rule(x, "expenditure", log)
        spending_rules(x, log)
    for x in d.get("detail") or []:
        sec = x.get("section")
        if sec == "revenue":
            decision_rule(x, "revenue", log)
            revenue_rules(x, log)
        elif sec == "expenditure":
            decision_rule(x, "expenditure", log)
            spending_rules(x, log)
        elif sec == "other financing":
            other_financing_rules(x, log)
        fund_name_rule(x, log, per_fund)
    for x in d.get("detail_totals") or []:
        fund_name_rule(x, log, per_fund)
    if isinstance(d.get("balance_sheet"), dict):
        balance_sheet_rules(d["balance_sheet"], log)
    utility_object_rule(d, log)
    entity_rules(d, entity_of(stem), log)
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
        log = apply(d, f.stem)
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
