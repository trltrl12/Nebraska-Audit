#!/usr/bin/env python3
"""List every receipt/detail line that ended up in other / other taxes / taxes (unlabeled) / miscellaneous,
grouped by (section, normalized label), so the user can decide each label's category once.

Read-only on output/json. Writes config/other_lines_to_decide.xlsx and .csv.
Keys match config/category_decisions.csv (same norm() as scripts/user_rules.py), so filled-in rows can be
appended there and scripts/user_rules.py applies them to every report.

Usage: python3 scripts/other_lines_report.py
"""
import csv
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from user_rules import norm, label_of, DECISIONS  # noqa: E402

JSON_DIR = ROOT / "output" / "json"
OUT_XLSX = ROOT / "config" / "other_lines_to_decide.xlsx"
OUT_CSV = ROOT / "config" / "other_lines_to_decide.csv"

LOOSE_CATS = {"other", "other taxes", "taxes (unlabeled)", "miscellaneous"}
PRINTED_OTHER = re.compile(r"\b(other|misc|miscellaneous|sundry)\b", re.I)
# combined/generic tax labels: a label rule can't type these, only the report's own breakdown can
GENERIC_TAX = re.compile(r"\btax(es)?\b", re.I)
SPECIFIC_TAX = re.compile(r"property|sales|motor|vehicle|occupation|franchise|lodging|inheritance|carline|in lieu|"
                          r"nameplate|wheel|keno|lottery|telephone|911|e911|cable|gas|electric|power|homestead|credit|"
                          r"penalt|interest|pro.?rate|turnback|tif|liquor|cigarette", re.I)
YEAR = re.compile(r"_(?:FY|B)(\d{2,4})")

RECEIPT_CATS = [
    "property tax", "Penalties and Interest on Taxes", "sales tax", "motor vehicle tax", "Motor Vehicle Pro-Rate",
    "occupation/franchise tax", "lodging tax", "inheritance tax", "special assessments", "keno/lottery",
    "Private Sector Share of Projects", "Carline Tax", "Public Power District Sales Tax", "In Lieu of Tax",
    "Nameplate Capacity Tax", "other taxes", "taxes (unlabeled)", "intergovernmental/state aid", "Turnback",
    "Reimbursement for Services", "Loan Repayments", "Homestead Exemption", "Property Tax Credit",
    "Tuition Received", "ESU Receipts", "federal grants", "charges for services/fees", "School Lunch Sales",
    "Rentals", "School Facility Rentals", "Utility Charges", "Drivers Education Fees", "Recreation Fees",
    "Cemetery Fees", "Ambulance Fees", "Fees of Office", "Program Revenue", "licenses and permits",
    "School License Fees", "Fines and Licenses", "Interest Income", "investment income",
    "Extracurricular Receipts", "donations/contributions", "reimbursements/refunds", "Sale of Property",
    "School-Related Receipts", "Other Non-Revenue Receipts", "other",
]
SPENDING_CATS = [
    "general government", "public safety", "streets/highways", "culture/recreation", "health/welfare",
    "economic development", "utilities", "Debt Service", "capital outlay", "instruction", "support services",
    "student transportation", "food service", "student activities", "Drivers Education", "Community Betterment",
    "Operating", "School-Related Spending", "hospital/health care", "housing", "natural resources/conservation",
    "agriculture/fair", "Mass Transit", "Bond Retirement", "Leases and SBITA", "other",
]


def section_of(where, x):
    if where == "receipts":
        return "revenue"
    return (x.get("section") or "").lower()


def amount_of(x):
    a = x.get("amount")
    if isinstance(a, dict):
        a = a.get("amount")
    try:
        return float(a or 0)
    except (TypeError, ValueError):
        return 0.0


def main():
    groups = defaultdict(lambda: {"lines": 0, "reports": set(), "total": 0.0, "labels": defaultdict(float),
                                  "cats": set(), "types": set(), "years": set(), "where": set(), "decided": 0})
    n_lines = 0
    for f in sorted(JSON_DIR.glob("*.json")):
        try:
            d = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        stem = f.stem
        m = YEAR.search(stem)
        year = m.group(1) if m else str(d.get("fiscal_year_end") or "")[:4]
        etype = d.get("entity_type") or ""
        for where in ("receipts", "detail"):
            for x in d.get(where) or []:
                cat = (x.get("category") or "").strip()
                if cat.lower() not in LOOSE_CATS:
                    continue
                sec = section_of(where, x)
                if sec == "other financing":
                    continue
                lab = label_of(x)
                g = groups[(sec, norm(lab))]
                a = amount_of(x)
                g["lines"] += 1
                g["reports"].add(stem)
                g["total"] += a
                g["labels"][lab] += abs(a)
                g["cats"].add(cat)
                g["types"].add(etype)
                g["years"].add(year)
                g["where"].add(where)
                if x.get("category_decision"):
                    g["decided"] += 1
                n_lines += 1

    rows = []
    for (sec, key), g in groups.items():
        examples = sorted(g["labels"], key=lambda k: -g["labels"][k])[:3]
        flags = []
        if PRINTED_OTHER.search(key) or not key:
            flags.append("printed as other/misc")
        if g["decided"] or (sec, key) in DECISIONS:
            flags.append("already decided (kept in other)")
        if "printed as other/misc" in flags:
            todo = "probably fine: report prints it as other/misc"
        elif GENERIC_TAX.search(key) and not SPECIFIC_TAX.search(key):
            todo = "combined tax line: needs the report's breakdown, not a label rule"
        elif not key or key in {"local", "state", "county", "federal", "receipts", "revenue", "revenues"}:
            todo = "too generic: needs the report's context"
        else:
            todo = "DECIDE: specific label, set your_category"
        rows.append({
            "what_to_do": todo,
            "section": sec,
            "label_normalized": key,
            "example_labels": " | ".join(examples),
            "current_category": ", ".join(sorted(g["cats"])),
            "lines": g["lines"],
            "reports": len(g["reports"]),
            "total_amount": round(g["total"], 2),
            "entity_types": ", ".join(sorted(t for t in g["types"] if t)),
            "years": ", ".join(sorted(y for y in g["years"] if y)),
            "found_in": ", ".join(sorted(g["where"])),
            "example_reports": ", ".join(sorted(g["reports"])[:3]),
            "flag": "; ".join(flags),
            "your_category": "",
            "your_detail_category": "",
        })
    rows.sort(key=lambda r: -abs(r["total_amount"]))

    cols = list(rows[0].keys()) if rows else []
    with OUT_CSV.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)

    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    from openpyxl.worksheet.datavalidation import DataValidation

    wb = Workbook()
    ws = wb.active
    ws.title = "Decide"
    ws.append(cols)
    for c in ws[1]:
        c.font = Font(bold=True)
    for r in rows:
        ws.append([r[c] for c in cols])
    ws.freeze_panes = "C2"
    ws.auto_filter.ref = ws.dimensions
    widths = {"what_to_do": 34, "section": 12, "label_normalized": 38, "example_labels": 55, "current_category": 18, "lines": 7,
              "reports": 8, "total_amount": 15, "entity_types": 22, "years": 10, "found_in": 16,
              "example_reports": 45, "flag": 30, "your_category": 30, "your_detail_category": 26}
    for i, c in enumerate(cols, 1):
        ws.column_dimensions[ws.cell(1, i).column_letter].width = widths.get(c, 15)
    money_col = cols.index("total_amount") + 1
    yellow = PatternFill("solid", fgColor="FFF2CC")
    your_col = cols.index("your_category") + 1
    for row in ws.iter_rows(min_row=2):
        row[money_col - 1].number_format = "#,##0"
        row[your_col - 1].fill = yellow
        row[your_col].fill = yellow

    cs = wb.create_sheet("Categories")
    cs.append(["Receipt categories", "Spending categories"])
    for c in cs[1]:
        c.font = Font(bold=True)
    for i in range(max(len(RECEIPT_CATS), len(SPENDING_CATS))):
        cs.append([RECEIPT_CATS[i] if i < len(RECEIPT_CATS) else None,
                   SPENDING_CATS[i] if i < len(SPENDING_CATS) else None])
    cs.column_dimensions["A"].width = 36
    cs.column_dimensions["B"].width = 32

    # dropdown of allowed categories (free text still allowed)
    letter = ws.cell(1, your_col).column_letter
    n = len(rows) + 1
    for sec, col, size in (("revenue", "A", len(RECEIPT_CATS)), ("expenditure", "B", len(SPENDING_CATS))):
        dv = DataValidation(type="list", formula1=f"=Categories!${col}$2:${col}${size + 1}", allow_blank=True,
                            showErrorMessage=False)
        ws.add_data_validation(dv)
        for i, r in enumerate(rows, 2):
            if r["section"] == sec:
                dv.add(f"{letter}{i}")
    wb.save(OUT_XLSX)

    total = sum(abs(r["total_amount"]) for r in rows) or 1
    run, k80 = 0.0, 0
    for r in rows:
        run += abs(r["total_amount"])
        k80 += 1
        if run >= 0.8 * total:
            break
    by_sec = defaultdict(int)
    for r in rows:
        by_sec[r["section"]] += 1
    printed = sum(1 for r in rows if "printed as other" in r["flag"])
    print(f"{n_lines} lines -> {len(rows)} labels ({dict(by_sec)}); {k80} labels cover 80% of dollars; "
          f"{printed} labels are printed as other/misc")
    todo = defaultdict(lambda: [0, 0, 0.0])
    for r in rows:
        t = todo[r["what_to_do"]]
        t[0] += 1
        t[1] += r["lines"]
        t[2] += abs(r["total_amount"])
    for k, (nl, nn, amt) in sorted(todo.items()):
        print(f"  {k}: {nl} labels, {nn} lines, ${amt:,.0f}")
    print(f"wrote {OUT_XLSX.relative_to(ROOT)} and {OUT_CSV.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
