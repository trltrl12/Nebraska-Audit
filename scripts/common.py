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
LONG_CSV = OUTPUT / "cashflow_long.csv"
SUMMARY_CSV = OUTPUT / "cashflow_summary.csv"
PROGRESS_CSV = OUTPUT / "progress.csv"

MANIFEST_FIELDS = [
    "filename", "url", "entity_name", "entity_type", "county", "subdivision_type", "fiscal_year",
    "filing_type", "basis_guess",
    "total_pages", "pages_kept", "page_types", "trimmed_file", "flag", "processed_at",
]

RECEIPT_CATEGORIES = [
    "property tax", "sales tax", "other taxes", "intergovernmental/state aid",
    "federal grants", "charges for services/fees", "investment income", "other",
]
DISBURSEMENT_CATEGORIES = [
    "general government", "public safety", "streets/highways", "culture/recreation",
    "health/welfare", "economic development", "utilities", "debt service principal",
    "debt service interest", "capital outlay", "other",
]
PROPRIETARY_FIELDS = ["operating", "noncapital_financing", "capital_and_related_financing", "investing"]

# Absolute tolerance in dollars, plus a relative tolerance on the largest figure involved.
ABS_TOL = float(os.environ.get("RECONCILE_ABS_TOL", "10"))
REL_TOL = float(os.environ.get("RECONCILE_REL_TOL", "0.0005"))


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


def load_reports():
    """Yield (stem, data_or_None, error_or_None) for every JSON in output/json/."""
    for p in sorted(JSON_DIR.glob("*.json")):
        try:
            yield p.stem, json.loads(p.read_text(encoding="utf-8")), None
        except Exception as e:  # noqa: BLE001
            yield p.stem, None, f"invalid JSON: {e}"


def amt(field):
    """Amount from a {"amount": x, "source_page": p} object (or a bare number). None if absent."""
    if field is None:
        return None
    if isinstance(field, dict):
        field = field.get("amount")
    if field is None or field == "":
        return None
    return float(field)


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
    receipts = sum_items(d.get("receipts"))
    disb = sum_items(d.get("disbursements"))
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


def reconcile_proprietary(d):
    """GAAP proprietary cash flows: beginning_cash + four activities = ending_cash."""
    p = d.get("proprietary") or {}
    beg, end = amt(p.get("beginning_cash")), amt(p.get("ending_cash"))
    if beg is None or end is None:
        return None
    comp = beg + sum(amt(p.get(k)) or 0 for k in PROPRIETARY_FIELDS)
    return dict(computed=round(comp, 2), ending=end, difference=round(end - comp, 2),
                reconciles=abs(end - comp) <= tol(beg, end))
