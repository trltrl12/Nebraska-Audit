"""Shared paths, CSV helpers, schema constants and reconciliation logic."""
import csv
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AUDITS = ROOT / "audits"          # full PDFs (gitignored, never committed)
TRIMMED = ROOT / "trimmed"        # small PDFs with only the statement pages
OUTPUT = ROOT / "output"
JSON_DIR = OUTPUT / "json"        # written by Claude sessions only

FILE_LIST = OUTPUT / "file_list.csv"
MANIFEST = OUTPUT / "manifest.csv"
DOWNLOAD_ERRORS = OUTPUT / "download_errors.csv"
FLAGGED = OUTPUT / "flagged.csv"
VALIDATED = OUTPUT / "validated.csv"   # stem + hash of each JSON as last validated
LONG_CSV = OUTPUT / "cashflow_long.csv"
SUMMARY_CSV = OUTPUT / "cashflow_summary.csv"
PROGRESS_CSV = OUTPUT / "progress.csv"
DETAIL_CSV = OUTPUT / "cashflow_detail.csv"
REVIEW_CSV = OUTPUT / "review.csv"

# Every receipt/disbursement/detail line belongs to one of these (fiduciary funds are excluded).
FUND_TYPES = ["governmental", "business-type"]

MANIFEST_FIELDS = [
    "filename", "url", "entity_name", "entity_type", "county", "subdivision_type", "fiscal_year",
    "filing_type", "basis_guess",
    "total_pages", "pages_kept", "page_types", "trimmed_file", "flag", "processed_at", "finder_version",
]

RECEIPT_CATEGORIES = [
    "property tax", "sales tax", "motor vehicle tax", "occupation/franchise tax", "lodging tax",
    "inheritance tax", "special assessments", "keno/lottery", "other taxes", "taxes (unlabeled)",
    "intergovernmental/state aid", "federal grants", "charges for services/fees",
    "licenses and permits", "Fines and Licenses", "investment income", "donations/contributions",
    "reimbursements/refunds", "Sale of Property", "other",
    # user decision 2026-10-23: these taxes are reported on their own, not under other taxes
    "Carline Tax",
    "Public Power District Sales Tax",
    "In Lieu of Tax",
    "Nameplate Capacity Tax",
    "Private Sector Share of Projects",  # user decision 2026-10-23 (developer/private payments toward public projects)
    "Penalties and Interest on Taxes",  # user decision 2026-10-23: kept apart from property tax
    "Homestead Exemption",  # user decision 2026-10-23: out of intergovernmental/state aid
    "Property Tax Credit",  # user decision 2026-10-23: out of intergovernmental/state aid
    "ESU Receipts",  # user decision 2026-10-23: out of intergovernmental/state aid
    "Motor Vehicle Pro-Rate",  # user decision 2026-10-23: out of intergovernmental/state aid
    "Tuition Received",  # user decision 2026-10-23: all tuition incl. preschool and summer school
    "Interest Income",  # user decision 2026-10-23: interest (investment income keeps dividends/gains)
    "Extracurricular Receipts",  # user decision 2026-10-23: school extracurricular/activity receipts
    "Turnback",  # user decision 2026-10-26: turnback tax, out of intergovernmental/state aid
    "Miscellaneous Tax",  # user decision 2026-10-27: Douglas County miscellaneous taxes (Tourism fund), was lodging tax
    "Categorical Grants",  # user decision 2026-10-27: Lincoln Public Schools 1925, out of donations/contributions
    "Other Non-Revenue Receipts",  # user decision 2026-10-27: Lincoln Public Schools 5300 / 5690
    "Reimbursement for Services",  # user decision 2026-10-26: own category
    "School Lunch Sales",  # user decision 2026-10-28: out of charges for services
    "Rentals",  # user decision 2026-10-28: out of charges for services
    "Utility Charges",  # user decision 2026-10-28: out of charges for services
    "Drivers Education Fees",  # user decision 2026-10-28: out of charges for services
    "Recreation Fees",  # user decision 2026-10-28: out of charges for services
    "Cemetery Fees",  # user decision 2026-10-28: out of charges for services
    "Ambulance Fees",  # user decision 2026-10-28: out of charges for services
    "Fees of Office",  # user decision 2026-10-28: out of charges for services
]
DISBURSEMENT_CATEGORIES = [
    "general government", "public safety", "streets/highways", "culture/recreation",
    "health/welfare", "economic development", "utilities", "debt service principal",
    "debt service interest", "debt service (principal and interest combined)", "capital outlay",
    "instruction", "support services", "student transportation", "food service",   # school districts
    "student activities",                                                           # school activity funds
    "hospital/health care", "housing", "natural resources/conservation", "agriculture/fair",
    "other",
    "Equipment Maintenance",  # user decision 2026-10-26: Lincoln function line, out of general government
    "Mass Transit",  # user decision 2026-10-26: own category
    "Bond Retirement",  # user decision 2026-10-26: own category
    "Leases and SBITA",  # user decision 2026-10-26: lease and SBITA principal and interest
    "Interest",  # user decision 2026-10-26: Lincoln debt interest line, own category
    "Fiscal Charges",  # user decision 2026-10-26: own category
    "Developer Purchased TIF Payments",  # user decision 2026-10-26: own category
    "Categorical Corporation and Private",  # user decision 2026-10-27: out of Instruction (school districts)
    # user decision 2026-10-28: utility (business-type) spending listed by cost type takes the cost type
    "Salaries and Wages", "Employee Benefits and Payroll Taxes", "Professional/Contract Services",
    "Supplies and Materials", "Fuel", "Repairs and Maintenance", "Insurance", "Depreciation",
    "Dues, Training and Travel", "Printing, Publishing and Postage", "Rent/Leases", "Purchased Power/Cost of Sales",
    "Grants/Payments to Other Entities", "Tax Refunds/Treasurer Fees", "Equipment Purchases", "Other Taxes Paid",
    # school district functions (user decision 2026-10-23); support services = Pupils and Staff only
    "Instruction",
    "Special Education Programs",
    "General Administration",
    "School Administration",
    "Business",
    "Building and Grounds",
    "Community Service Operations",
    "State Categorical Programs",
    "Federal Programs",
    "Debt Service",
    "Drivers Education",  # user decision 2026-10-28: out of instruction
]
PROPRIETARY_FIELDS = ["operating", "noncapital_financing", "capital_and_related_financing", "investing"]

ASSET_CATEGORIES = [
    "cash and investments", "Cash and Cash Equivalents", "Investments", "receivables", "due from other governments", "inventories/prepaids", "Inventories", "Prepaid Items", "Plant Operation Assets", "Internal Balances",
    "restricted assets", "capital assets", "other assets", "Other Assets",
]
LIABILITY_CATEGORIES = [
    "accounts payable", "accrued liabilities", "due to other governments", "unearned revenue",
    "debt - due within one year", "debt - due in more than one year", "net pension/OPEB liability",
    "other liabilities",
    "Internal Balances",
    "Accrued Interest Payable", "Accrued Liabilities", "Commercial Paper",
    # user decisions 2026-10-26: "<X> - Due ..." lines use X as category; due to pension trust fund
    "Bonds Payable", "Leases Payable", "Subscriptions", "Compensated Absences", "Landfill Postclosure Costs",
    "Workers' Compensation Claims", "Other Postemployment Benefit Liability", "Due to Pension Trust Fund",
]
# Fine-grained categories for line items in the supplementary detail schedules.
# Revenue: where the money comes from.
DETAIL_REVENUE_CATEGORIES = [
    "property tax", "sales tax", "motor vehicle tax", "occupation/franchise tax", "lodging tax",
    "inheritance tax", "special assessments", "keno/lottery", "other local taxes", "taxes (unlabeled)",
    "highway allocation", "motor vehicle pro-rate", "municipal equalization", "homestead/tax credits",
    "state school aid (TEEOSA)", "other state aid",
    "county/other local governments", "federal grants",
    "water charges", "sewer charges", "electric charges", "gas charges", "refuse/sanitation charges",
    "other utility charges",
    "licenses and permits", "Fines and Licenses", "fees of office", "rentals",
    "recreation/pool/park fees", "cemetery fees", "tuition and fees", "school lunch sales",
    "ambulance/rescue fees", "patient service revenue", "tenant rent", "fair/event revenue",
    "student activity receipts", "other charges for services",
    "investment income", "donations/contributions", "reimbursements/refunds", "Sale of Property",
    "miscellaneous revenue",
    # user decisions 2026-10-23 for lines that were in other/miscellaneous (config/category_decisions.csv)
    "Annexation Income",
    "Carline Tax",
    "Charges for Services / Fees",
    "Collection in District",
    "Community Services",
    "Customer Deposits (refundable)",
    "Debt Services / Miscellaneous",
    "Deposits",
    "Donations / Miscellaneous Aid",
    "E911 Payments",
    "Economic Development",
    "Farm Income",
    "Grant Repayments",
    "Grants",
    "Improvement Fund",
    "In Lieu of Tax",
    "Interest Income",
    "Licenses, Fees and Taxes",
    "Loan Collections",
    "Loss on Disposal of Assets (non-cash)",
    "Miscellaneous Sales",
    "Nameplate Capacity Tax",
    "Non-cash / adjustment (not a cash flow)",
    "Oil Income",
    "Other / Miscellaneous Receipts",
    "Other / Miscellaneous Taxes",
    "Other Nonrevenue Receipts",
    "Patronage / Capital Credits",
    "Public Power District Sales Tax",
    "Rate Stabilization (non-cash)",
    "Receipts - Special Revenue",
    "Reimbursements / Refunds",
    "Rental Deposits (refundable)",
    "Rental Income",
    "Sale of Merchandise",
    "Security Deposits",
    "Senior Center",
    "Settlements",
    "Sidewalk Program",
    "Special Revenue - Housing",
    "State Receipts",
    "TIF Revenue",
    "Telephone Patronage",
    "Private Sector Share of Projects",  # user decision 2026-10-23 (developer/private payments toward public projects)
    "Penalties and Interest on Taxes",  # user decision 2026-10-23: kept apart from property tax
    "Homestead Exemption",  # user decision 2026-10-23: out of intergovernmental/state aid
    "Turnback",  # user decision 2026-10-26
    "Motor Vehicle Pro-Rate",  # user decision 2026-10-23 (validator LABEL_RULES assign this name)
    "Categorical Grants",  # user decision 2026-10-27: Lincoln Public Schools 1925, out of donations/contributions
    "Reimbursement for Services",  # user decision 2026-10-26
    "Property Tax Credit",  # user decision 2026-10-23: out of intergovernmental/state aid
    "ESU Receipts",  # user decision 2026-10-23: out of intergovernmental/state aid
    "Tuition Received",  # user decision 2026-10-23: all tuition incl. preschool and summer school
    "Interest Income",  # user decision 2026-10-23: interest (investment income keeps dividends/gains)
    "Extracurricular Receipts",  # user decision 2026-10-23: school extracurricular/activity receipts
    "School Lunch Sales",  # user decision 2026-10-28: out of charges for services
    "Rentals",  # user decision 2026-10-28: out of charges for services
    "Utility Charges",  # user decision 2026-10-28: out of charges for services
    "Drivers Education Fees",  # user decision 2026-10-28: out of charges for services
    "Recreation Fees",  # user decision 2026-10-28: out of charges for services
    "Cemetery Fees",  # user decision 2026-10-28: out of charges for services
    "Ambulance Fees",  # user decision 2026-10-28: out of charges for services
    "Fees of Office",  # user decision 2026-10-28: out of charges for services
]
# Expenditure: what the money is spent on (object of expenditure).
DETAIL_EXPENSE_CATEGORIES = [
    "salaries and wages", "employee benefits and payroll taxes", "professional/contract services",
    "supplies and materials", "utilities (power, gas, phone)", "fuel", "repairs and maintenance",
    "insurance", "rent/leases", "dues, training and travel", "printing, publishing and postage",
    "grants/payments to other entities", "law enforcement contract", "equipment purchases",
    "construction/infrastructure", "land and buildings", "debt principal", "debt interest and fees",
    "debt principal and interest (combined)",
    "tax refunds/treasurer fees", "purchased power/cost of sales", "depreciation",
    "medical supplies and services", "student activity spending",
    "not itemized",   # a function/department total printed with no object breakdown
    "miscellaneous expenditure",
    # user decisions 2026-10-23 for lines that were in other/miscellaneous (config/category_decisions.csv)
    "2900 Reappropriated Funds",
    "ARPA",
    "Advertising",
    "Airport",
    "Assessments Paid",
    "Awards and Recognition",
    "Bank Service Charges",
    "Buildings",
    "Business Expenses",
    "Capital Outlay",
    "Cares Act Repayment",
    "Commissions",
    "Community Services",
    "Contracted Services",
    "Cost of Goods Sold",
    "County Commissions",
    "County Support",
    "Credit loss expense",
    "Customer Deposits (refundable)",
    "Debt Services / Miscellaneous",
    "Decorations",
    "Deposits Refunded",
    "Depreciation & Amortization",
    "Dividends",
    "Donations / Miscellaneous Aid",
    "Dues and Subscriptions",
    "Election Expense",
    "Equipment Rental",
    "Ethanol Plant",
    "Events / Fair",
    "Farming lease",
    "Fuel / Vehicle",
    "Fundraising",
    "Grant Repayments / Refunds",
    "Grants",
    "Health & Life Insurance",
    "Inheritance Tax",
    "Institutional Administration",
    "Insurance",
    "Interlocal Payments",
    "Licenses, Fees and Taxes",
    "Loan Forgiveness",
    "Loss on Disposal of Assets (non-cash)",
    "Management & General",
    "Non-cash / adjustment (not a cash flow)",
    "Office Supplies",
    "Operating Expenses",
    "Other / Miscellaneous Expenses",
    "Other Taxes Paid",
    "Printing, Publishing and Postage",
    "Professional Services",
    "Program / Function expense",
    "Purchased Property Services",
    "Real Estate Taxes",
    "Remittance to County Treasurer",
    "Rental Deposits (refundable)",
    "Repairs & Maintenance",
    "Salaries / Benefits",
    "Sales Tax",
    "Scholarships / Student Aid",
    "Security",
    "Special Events",
    "Special Gifts",
    "State Early Childhood",
    "State Taxes",
    "Student Fees",
    "Supplies and Materials",
    "Technology Expense",
    "Total Disbursements",
    "Training and Travel",
    "Travel",
    "Weather observation service",
    "Wildlife",
    "keno/lottery",
]
DETAIL_SECTIONS = ["revenue", "expenditure", "other financing"]

# Balance-sheet line lists and their stated-total fields
BS_SECTIONS = {
    "assets": "total_assets", "deferred_outflows": "total_deferred_outflows",
    "liabilities": "total_liabilities", "deferred_inflows": "total_deferred_inflows",
    "equity": "total_equity",
}

# Absolute tolerance in dollars, plus a relative tolerance on the largest figure involved.
# $1 and no relative slack, matching validate.py's rule (over $1 is flagged).
ABS_TOL = float(os.environ.get("RECONCILE_ABS_TOL", "1"))
REL_TOL = float(os.environ.get("RECONCILE_REL_TOL", "0"))


def read_csv(path):
    path = Path(path)
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_csv(path, rows, fields):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def append_csv(path, row, fields):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        if new:
            w.writeheader()
        w.writerow(row)


SCALED_KEYS = ("amount", "budget_final", "difference")


def _scale(node, factor):
    """Multiply every amount-like value in a report by factor (in place)."""
    if isinstance(node, dict):
        for k, v in node.items():
            if k in SCALED_KEYS and isinstance(v, (int, float)) and not isinstance(v, bool):
                node[k] = round(v * factor, 2)
            else:
                _scale(v, factor)
    elif isinstance(node, list):
        for v in node:
            _scale(v, factor)


def load_reports(scaled=False):
    """Yield (stem, data_or_None, error_or_None) for every JSON in output/json/.

    Reports printed in thousands carry top-level "units": 1000 and store amounts as printed (so
    validate.py can match them to the page text). scaled=True multiplies them to whole dollars for
    the CSV/XLSX outputs."""
    for p in sorted(JSON_DIR.glob("*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            yield p.stem, None, f"invalid JSON: {e}"
            continue
        units = d.get("units") if isinstance(d, dict) else None
        if scaled and isinstance(units, (int, float)) and units not in (0, 1):
            _scale(d, units)
        yield p.stem, d, None


def amt(field):
    """Amount from a {"amount": x, "source_page": p} object (or a bare number). None if absent."""
    if field is None:
        return None
    if isinstance(field, dict):
        field = field.get("amount")
    if field is None or field == "" or isinstance(field, (list, dict)):
        return None  # malformed values are flagged by validate.py, never crash the build
    try:
        return float(field)
    except (TypeError, ValueError):
        return None


def page(field):
    return field.get("source_page") if isinstance(field, dict) else None


def tol(*values):
    scale = max([abs(v) for v in values if v is not None] or [0])
    return max(ABS_TOL, REL_TOL * scale)


def sum_items(items):
    return sum(float(i.get("amount") or 0) for i in (items or []))


def reconcile(d):
    """Return dict with beginning, inflows, outflows, net_other, computed_ending, ending,
    difference, reconciles (True/False/None) and balance_type.

    Cash basis: beginning_cash(+investments) + receipts - disbursements + transfers_in
                - transfers_out + debt_proceeds + other_financing + adjustments = ending_cash(+inv).
    GAAP:       same identity on governmental fund balances (beginning/ending_fund_balance).
    """
    basis = (d.get("basis") or "").lower()
    if basis == "gaap":
        btype = "fund_balance"
        beg = amt(d.get("beginning_fund_balance"))
        end = amt(d.get("ending_fund_balance"))
    else:
        btype = "cash"
        beg = amt(d.get("beginning_cash"))
        end = amt(d.get("ending_cash"))
    receipts = sum_items(gov_items(d.get("receipts")))
    disb = sum_items(gov_items(d.get("disbursements")))
    net_other = ((amt(d.get("transfers_in")) or 0) - (amt(d.get("transfers_out")) or 0)
                 + (amt(d.get("debt_proceeds")) or 0) + (amt(d.get("other_financing")) or 0)
                 + sum_items(d.get("adjustments")))
    out = dict(balance_type=btype, beginning=beg, receipts=receipts, disbursements=disb,
               net_other=net_other, ending=end, computed_ending=None, difference=None, reconciles=None)
    if beg is None or end is None:
        return out
    comp = beg + receipts - disb + net_other
    out["computed_ending"] = round(comp, 2)
    out["difference"] = round(end - comp, 2)
    out["reconciles"] = abs(end - comp) <= tol(beg, end, receipts, disb)
    return out


def page_ranges(pages):
    """[6, 7, 8, 9, 23] -> "6-9, 23" (original-report page numbers used)."""
    ps = sorted({int(p) for p in pages if str(p).strip().isdigit()})
    out, start = [], None
    for a, b in zip(ps, ps[1:] + [None]):
        start = a if start is None else start
        if b != a + 1:
            out.append(f"{start}-{a}" if start != a else str(a))
            start = None
    return ", ".join(out)


def fund_type(item):
    return (item or {}).get("fund_type") or "governmental"


def gov_items(items):
    return [i for i in items or [] if fund_type(i) == "governmental"]


def bt_items(items):
    return [i for i in items or [] if fund_type(i) == "business-type"]


def reconcile_business(d):
    """Business-type (utility) funds: beginning + receipts - disbursements + transfers_net +
    debt_proceeds + other_financing + adjustments = ending (cash or net position, as reported)."""
    bt = d.get("business_type") or {}
    rec, dis = sum_items(bt_items(d.get("receipts"))), sum_items(bt_items(d.get("disbursements")))
    beg, end = amt(bt.get("beginning")), amt(bt.get("ending"))
    out = dict(beginning=beg, receipts=rec, disbursements=dis, ending=end, computed_ending=None,
               difference=None, reconciles=None, measure=bt.get("measure"))
    if beg is None or end is None:
        return out
    comp = (beg + rec - dis + (amt(bt.get("transfers_net")) or 0) + (amt(bt.get("debt_proceeds")) or 0)
            + (amt(bt.get("other_financing")) or 0) + sum_items(bt.get("adjustments")))
    out.update(computed_ending=round(comp, 2), difference=round(end - comp, 2),
               reconciles=abs(end - comp) <= tol(beg, end, rec, dis))
    return out


def reported_total(d, fund, key):
    """Printed total for receipts/disbursements of one fund type (older flat form = governmental)."""
    rt = d.get("reported_totals") or {}
    if fund in rt:
        return (rt.get(fund) or {}).get(key)
    return rt.get(key) if fund == "governmental" else None


def bs_total(bs, section):
    """Stated total if given, else the sum of the lines (None if neither)."""
    stated = amt(bs.get(BS_SECTIONS[section]))
    if stated is not None:
        return stated
    return sum_items(bs.get(section)) if bs.get(section) else None


def balance_sheet_check(d):
    """assets + deferred outflows = liabilities + deferred inflows + equity (fund balance / net position)."""
    bs = d.get("balance_sheet") or {}
    a, e = bs_total(bs, "assets"), bs_total(bs, "equity")
    if a is None or e is None:
        return None
    left = a + (bs_total(bs, "deferred_outflows") or 0)
    right = (bs_total(bs, "liabilities") or 0) + (bs_total(bs, "deferred_inflows") or 0) + e
    return dict(left=round(left, 2), right=round(right, 2), difference=round(left - right, 2),
                balances=abs(left - right) <= tol(left, right))


def reconcile_proprietary(d):
    """GAAP proprietary cash flows: beginning_cash + four activities = ending_cash."""
    p = d.get("proprietary") or {}
    beg, end = amt(p.get("beginning_cash")), amt(p.get("ending_cash"))
    if beg is None or end is None:
        return None
    comp = beg + sum(amt(p.get(k)) or 0 for k in PROPRIETARY_FIELDS)
    return dict(computed=round(comp, 2), ending=end, difference=round(end - comp, 2),
                reconciles=abs(end - comp) <= tol(beg, end))


def json_hash(stem):
    """Short content hash of output/json/<stem>.json and its verify file (None if absent)."""
    import hashlib
    p = JSON_DIR / f"{stem}.json"
    if not p.exists():
        return None
    h = hashlib.sha1(p.read_bytes())
    v = JSON_DIR / "verify" / f"{stem}.json"
    if v.exists():
        h.update(v.read_bytes())
    return h.hexdigest()[:16]
