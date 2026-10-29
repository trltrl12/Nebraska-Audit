"""Build output/cashflow.xlsx: a detailed, filterable workbook of every extraction.

Sheets:
  Summary          one row per report: balances, totals, reconciliation, flag status
  Review           items to look at even when the numbers check out (split/unlabeled taxes, etc.)
  Line Items       every receipt/disbursement line from the basic statements
  Detail Lines     every line item from the supplementary schedules (budget-to-actual,
                   departmental, combining, fund schedules): fund, department, category,
                   detailed category, actual and final budget
  Revenue by Source       one row per report and fund type, one column per detailed revenue category
  Spending by Type        one row per report and fund type, one column per detailed expenditure category
  Spending by Department  one row per report and department/function, with its share of spending
  Receipts         one row per report and fund type, one column per receipt category
  Disbursements    one row per report and fund type, one column per disbursement category
  Financing & Balances    balances, transfers, debt proceeds, other financing, adjustments
  Proprietary      GAAP proprietary-fund cash flows by activity
  Balance Sheet    every asset, liability, deferral and fund balance/net position line
  Flagged          validation issues (output/flagged.csv)
  Progress         status of every report (output/progress.csv)
  Notes            the extractor's notes per report
Lines carry fund_type: governmental or business-type (utility funds), reported separately.
Every sheet ends with two reference columns: `pages` (the page or pages of the original audit the
row comes from, e.g. "7" or "7-10, 23-31") and `page_link` (opens the Auditor's PDF at the first
of those pages).
Run after validate.py and build_csv.py (it reads their outputs).
"""
import json
import re
import sys
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (BS_SECTIONS, DETAIL_EXPENSE_CATEGORIES, DETAIL_REVENUE_CATEGORIES,  # noqa: E402
                    DISBURSEMENT_CATEGORIES, FILE_LIST, FLAGGED, FUND_TYPES, MANIFEST, OUTPUT, PROGRESS_CSV,
                    PROPRIETARY_FIELDS, RECEIPT_CATEGORIES, REVIEW_CSV, SUMMARY_CSV, amt, fund_type,
                    JSON_DIR, load_reports, page, page_ranges, read_csv, reported_total, write_csv)

from build_csv import filing_label  # noqa: E402

CATEGORY_NAMES = set(RECEIPT_CATEGORIES) | set(DISBURSEMENT_CATEGORIES) | set(DETAIL_REVENUE_CATEGORIES) | \
    set(DETAIL_EXPENSE_CATEGORIES)

XLSX = OUTPUT / "cashflow.xlsx"
# Principal, interest and unsplit principal-and-interest lines, summed into a "debt service total" column
DEBT_CATS = ["Interest", "Fiscal Charges", "Bond Retirement", "Leases and SBITA", "debt service principal", "debt service interest", "debt service (principal and interest combined)",
             "Debt Service"]
DEBT_DETAIL = ["debt principal", "debt interest and fees", "debt principal and interest (combined)"]
MONEY = '#,##0.00;[Red](#,##0.00)'
HEAD_FILL = PatternFill("solid", fgColor="1F4E78")
BAD_FILL = PatternFill("solid", fgColor="F8CBAD")
GOOD_FILL = PatternFill("solid", fgColor="C6EFCE")
REF = ["pages", "page_link"]          # the two reference columns, last on every sheet
# CSV columns replaced by the reference columns
HIDE = {"source_url", "report_pages", "pages_kept", "trimmed_file", "source_page", "pages"}


def num(v):
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return v


# The focused workbook (output/cashflow_core.xlsx): only the subdivision types the user reviews and only
# the cash flow, balance sheet and revenue/expense detail sheets. Types are listed in config/review_scope.txt.
CORE_XLSX = OUTPUT / "cashflow_core.xlsx"
CORE_SHEETS = ["Summary", "Review", "Line Items", "Detail Lines", "Revenue by Source", "Spending by Type",
               "Spending by Department", "Receipts", "Disbursements", "Financing & Balances", "Proprietary",
               "Balance Sheet"]
_scope = None   # set of report stems to keep, while building the focused workbook
_collect = None  # {sheet title: (header, rows)} gathered for the one-tab workbook
ONE_XLSX = OUTPUT / "cashflow_one_tab.xlsx"
# Sheets the one-tab workbook is built from
ONE_SOURCES = ["Summary", "Line Items", "Detail Lines", "Proprietary", "Balance Sheet"]


def core_stems():
    """Report stems whose subdivision type is listed in config/review_scope.txt."""
    f = OUTPUT.parent / "config" / "review_scope.txt"
    types = {ln.strip() for ln in f.read_text().splitlines() if ln.strip() and not ln.startswith("#")} \
        if f.exists() else set()
    return {Path(r["filename"]).stem for r in read_csv(FILE_LIST) if r.get("subdivision_type") in types}


# User 2026-10-26: categories and labels are shown capitalized in the workbooks ("capital outlay" -> "Capital Outlay").
# Words that already contain a capital (SBITA, TEEOSA, LB840) are kept; small words stay lowercase.
CAP_COLS = {"category", "detail_category", "original_label", "level", "fund_type", "source"}
_SMALL = {"and", "or", "of", "the", "in", "for", "to", "a", "an", "on", "at", "by", "with", "from", "per", "vs", "as"}


def cap(v):
    if not isinstance(v, str):
        return v
    n = [0]

    def word(m):
        w = m.group(0)
        n[0] += 1
        if any(ch.isupper() for ch in w) or (n[0] > 1 and w in _SMALL):
            return w
        return w[0].upper() + w[1:]
    return re.sub(r"[A-Za-z][A-Za-z']*", word, v)


def add_sheet(wb, title, header, rows, money_cols=(), status_col=None):
    if _scope is not None:
        if title not in CORE_SHEETS:
            return None
        if "filename" in header:
            i = header.index("filename")
            rows = [r for r in rows if Path(str(r[i] or "")).stem in _scope]
    if _collect is not None and title in ONE_SOURCES:
        _collect[title] = (header, rows)
    ws = wb.create_sheet(title)
    ws.append([cap(h) if h in CATEGORY_NAMES else cap(h.replace("_", " ")) for h in header])
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = HEAD_FILL
        c.alignment = Alignment(wrap_text=True, vertical="top")
    for r in rows:
        ws.append([num(v) if header[i] in money_cols else cap(v) if header[i] in CAP_COLS
                   else v[:1].upper() + v[1:].replace("_", " ") if header[i] == "section" and isinstance(v, str) else v
                   for i, v in enumerate(r)])
    idx = {h: i + 1 for i, h in enumerate(header)}
    for h in money_cols:
        if h in idx:
            for (cell,) in ws.iter_rows(min_row=2, min_col=idx[h], max_col=idx[h]):
                cell.number_format = MONEY
    if status_col and status_col in idx:
        for (cell,) in ws.iter_rows(min_row=2, min_col=idx[status_col], max_col=idx[status_col]):
            if cell.value in ("no", "yes") and status_col == "reconciles":
                cell.fill = GOOD_FILL if cell.value == "yes" else BAD_FILL
            elif cell.value in ("flagged", "download_failed"):
                cell.fill = BAD_FILL
            elif cell.value == "done":
                cell.fill = GOOD_FILL
    for i, h in enumerate(header, 1):
        width = max([len(str(h))] + [len(str(r[i - 1])) for r in rows[:500] if i - 1 < len(r) and r[i - 1] is not None])
        ws.column_dimensions[get_column_letter(i)].width = min(max(width + 2, 10), 60)
    if "page_link" in idx:  # clickable link to the PDF on the Auditor's website
        # HYPERLINK formulas, not cell hyperlinks: Excel allows only 65,530 of those per sheet and
        # reports the file as damaged beyond that.
        ws.column_dimensions[get_column_letter(idx["page_link"])].width = 14
        for (cell,) in ws.iter_rows(min_row=2, min_col=idx["page_link"], max_col=idx["page_link"]):
            v = str(cell.value or "")
            if v.startswith("http"):
                label = f"Open p.{v.rsplit('#page=', 1)[1]}" if "#page=" in v else "Open PDF"
                url = v.replace('"', '""')
                cell.value = f'=HYPERLINK("{url}","{label}")'
                cell.font = Font(color="0563C1", underline="single")
    ws.freeze_panes = "B2" if title != "Notes" else "A2"
    if rows:
        ws.auto_filter.ref = ws.dimensions
    return ws


def main():
    """Build the full workbook, then the focused one (see CORE_SHEETS / config/review_scope.txt)."""
    global _scope
    _scope = None
    build(XLSX)
    _scope = core_stems()
    if _scope:
        global _collect
        _collect = {}
        build(CORE_XLSX)
        one_tab(_collect)
        _collect = None
    _scope = None


ONE_COLS = ["entity_name", "entity_type", "county", "filename", "filing_type", "fiscal_year_end", "basis", "level",
            "fund_type", "section", "source", "line", "schedule", "fund", "department", "category",
            "detail_category", "original_label", "amount", "pages", "page_link"]
# Cash flow roll-forward lines (Summary columns): (column, label, sign)
GOV_FLOW = [("beginning_balance", "beginning {}", 1), ("total_receipts", "receipts", 1),
            ("total_disbursements", "disbursements", -1), ("transfers_in", "transfers in", 1),
            ("transfers_out", "transfers out", -1), ("debt_proceeds", "debt proceeds", 1),
            ("other_financing", "other financing", 1), ("adjustments", "adjustments", 1),
            ("ending_balance", "ending {}", 1)]
PROP_FLOW = [("beginning_cash", "beginning cash"), ("operating", "operating activities"),
             ("noncapital_financing", "noncapital financing activities"),
             ("capital_and_related_financing", "capital and related financing activities"),
             ("investing", "investing activities"), ("ending_cash", "ending cash")]


def page_amount(v):
    return v.get("amount") if isinstance(v, dict) else v


def one_tab(collected):
    """output/cashflow_one_tab.xlsx: one sheet, one amount column, three levels:
    cash flow (governmental and business-type roll-forwards; utility cash flow statements),
    balance sheet, and revenue and expenses (statement lines and schedule detail, told apart by `source`:
    both describe the same money, so sum one source at a time). Cash-flow outflows are negative."""
    county = {Path(r["filename"]).stem: r.get("county", "") for r in read_csv(FILE_LIST)}
    out = []

    def rows_of(title):
        header, rows = collected.get(title, ([], []))
        ix = {h: i for i, h in enumerate(header)}
        return [{h: r[i] for h, i in ix.items()} for r in rows]

    def num_(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    for r in rows_of("Summary"):
        ident_ = {c: r.get(c) for c in ("entity_name", "entity_type", "filename", "filing_type", "fiscal_year_end",
                                        "basis", "pages", "page_link")}
        bt = (r.get("balance_type") or "balance").replace("_", " ")
        for n, (col, label, sign) in enumerate(GOV_FLOW, 1):
            v = num_(r.get(col))
            if v is not None and (v or col in ("beginning_balance", "ending_balance")):
                out.append({**ident_, "level": "cash flow", "fund_type": "governmental", "section": "roll-forward",
                            "source": "statement", "line": n, "original_label": label.format(bt), "amount": sign * v})
        # governmental activities net position from the Statement of Activities (user 2026-10-29); not part of the
        # fund-balance roll-forward above
        try:
            gnp = json.loads((JSON_DIR / f"{r.get('filename')}.json").read_text()).get("governmental_net_position") or {}
        except (OSError, ValueError):
            gnp = {}
        for n, (k, label) in enumerate([("beginning", "beginning net position"), ("restatement", "restatement"),
                                        ("change", "change in net position"), ("ending", "ending net position")], 1):
            v = num_(page_amount(gnp.get(k)))
            if v is not None:
                out.append({**ident_, "level": "cash flow", "fund_type": "governmental",
                            "section": "net position (statement of activities)", "source": "statement", "line": n,
                            "original_label": label, "amount": v})
        b, rc, ds, e = (num_(r.get(k)) for k in ("bt_beginning", "bt_receipts", "bt_disbursements", "bt_ending"))
        m = r.get("bt_measure") or "balance"
        lines = [(1, f"beginning {m}", b, "statement"), (2, "receipts", rc, "statement"),
                 (3, "disbursements", -ds if ds is not None else None, "statement")]
        if None not in (b, e):
            other = round(e - b - (rc or 0) + (ds or 0), 2)
            if other:
                lines.append((4, "transfers, financing and adjustments (net)", other, "computed"))
        lines.append((5, f"ending {m}", e, "statement"))
        for n, label, v, src in lines:
            if v is not None and (b is not None or e is not None):
                out.append({**ident_, "level": "cash flow", "fund_type": "business-type", "section": "roll-forward",
                            "source": src, "line": n, "original_label": label, "amount": v})
    for r in rows_of("Proprietary"):
        for n, (col, label) in enumerate(PROP_FLOW, 1):
            v = num_(r.get(col))
            if v is not None:
                out.append({**{c: r.get(c) for c in ONE_COLS if c in r}, "level": "cash flow",
                            "fund_type": "business-type", "section": "utility cash flow statement",
                            "source": "statement", "line": n, "original_label": label, "amount": v})
    for r in rows_of("Line Items"):
        out.append({**{c: r.get(c) for c in ONE_COLS if c in r}, "level": "revenue and expenses",
                    "section": r.get("section"),   # receipts / disbursements
                    "source": "statement"})
    for r in rows_of("Detail Lines"):
        out.append({**{c: r.get(c) for c in ONE_COLS if c in r}, "level": "revenue and expenses",
                    "section": {"revenue": "receipts", "expenditure": "disbursements"}.get(r.get("section"),
                                                                                         r.get("section")),
                    "source": "schedule"})
        if r.get("section") == "other financing" and not out[-1].get("category"):
            out[-1]["category"] = "Total Other Financing Sources (Uses)"   # user 2026-10-26: statement heading
    for r in rows_of("Balance Sheet"):
        out.append({**{c: r.get(c) for c in ONE_COLS if c in r}, "level": "balance sheet",
                    "schedule": r.get("statement"), "source": "statement"})
    for row in out:
        row["county"] = county.get(Path(str(row.get("filename") or "")).stem, "")
    out.sort(key=lambda x: (str(x.get("entity_name") or ""), ["cash flow", "revenue and expenses", "balance sheet"]
                            .index(x["level"])))
    global _scope
    keep, _scope = _scope, None   # rows are already limited to the focused reports
    wb = Workbook()
    wb.remove(wb.active)
    add_sheet(wb, "All Data", ONE_COLS, [[r.get(c) for c in ONE_COLS] for r in out], ["amount"])
    _scope = keep
    wb.save(ONE_XLSX)
    print(f"wrote {ONE_XLSX.relative_to(OUTPUT.parent)} ({len(out)} rows)")


def build(xlsx):
    manifest = {Path(r["filename"]).stem: r for r in read_csv(MANIFEST)}
    reports = [(s, d) for s, d, e in load_reports(scaled=True) if d and not e]
    file_urls = {Path(r["filename"]).stem: r.get("url", "") for r in read_csv(FILE_LIST)}

    def url(stem):
        return manifest.get(Path(stem).stem, {}).get("url") or file_urls.get(Path(stem).stem, "")

    def kept(stem):
        return [p for p in (manifest.get(Path(stem).stem, {}).get("pages_kept") or "").split(";") if p]

    def orig(stem, p):
        """Page number in the original audit for page p of the trimmed PDF."""
        try:
            return int(kept(stem)[int(p) - 1])
        except (ValueError, IndexError, TypeError):
            return None

    def ref_pages(stem, pages):
        """[pages, page_link] for original-report page numbers."""
        pages = sorted({int(p) for p in pages if p})
        u = url(stem)
        return [page_ranges(pages), f"{u}#page={pages[0]}" if u and pages else u]

    def ref(stem, items):
        """[pages, page_link] for one item / a list of items carrying trimmed-PDF source_page."""
        items = items if isinstance(items, list) else [items]
        return ref_pages(stem, [orig(stem, page(i) if isinstance(i, dict) else i) for i in items])

    def ref_all(stem):
        """[pages, page_link] covering every page kept for the report."""
        return ref_pages(stem, kept(stem))

    wb = Workbook()
    wb.calculation.fullCalcOnLoad = True  # openpyxl stores no cached values for the link formulas
    wb.remove(wb.active)
    ident = ["entity_name", "entity_type", "filename", "filing_type", "fiscal_year_end", "basis"]

    def base(stem, d):
        """Identifying columns; filing_type separates audits from waiver budget forms."""
        return [d.get("entity_name"), d.get("entity_type"), stem,
                filing_label(manifest.get(Path(stem).stem, {"filename": stem})), d.get("fiscal_year_end"),
                d.get("basis")]

    # ---- Summary (from cashflow_summary.csv)
    srows = read_csv(SUMMARY_CSV)
    sh = [h for h in (list(srows[0].keys()) if srows else ["entity_name"]) if h not in HIDE]
    money = [h for h in sh if h not in ("entity_name", "entity_type", "county", "filename", "fiscal_year_end",
                                        "basis", "filing_type", "balance_type", "reconciles", "prop_reconciles",
                                        "balance_sheet_statement", "balance_sheet_balances", "flagged",
                                        "fiscal_year", "bt_measure", "bt_reconciles", "review_items")]
    add_sheet(wb, "Summary", sh + REF, [[r[h] for h in sh] + ref_all(r["filename"]) for r in srows],
              money, "reconciles")

    # ---- Needs Decision: questions only a person can settle (JSON `decision_needed[]`)
    dh = ["entity_name", "entity_type", "filename", "filing_type", "question", "amount", "how_recorded_now",
          "your_decision"]
    add_sheet(wb, "Needs Decision", dh + REF,
              [base(s, d)[:4] + [q.get("question"), amt(q.get("amount")),
                q.get("recorded_as"), ""] + ref(s, q) for s, d in reports for q in d.get("decision_needed") or []],
              ["amount"])

    # ---- Review
    rv = read_csv(REVIEW_CSV)
    rh = ["entity_name", "filename", "where", "fund_type", "original_label", "amount", "reason"]
    add_sheet(wb, "Review", rh[:2] + ["filing_type"] + rh[2:] + REF,
              [[r.get(h) for h in rh[:2]] + [filing_label(manifest.get(r["filename"], {"filename": r["filename"]}))]
               + [r.get(h) for h in rh[2:]] + ref(r["filename"], r.get("source_page")) for r in rv], ["amount"])

    # ---- Line Items (basic statements)
    lines = []
    for s, d in reports:
        b0 = base(s, d)
        for section in ("receipts", "disbursements"):
            for it in d.get(section) or []:
                lines.append(b0 + [fund_type(it), section, it.get("category"), it.get("detail_category"),
                                     it.get("original_label"), it.get("amount")] + ref(s, it))
    add_sheet(wb, "Line Items", ident + ["fund_type", "section", "category", "detail_category", "original_label",
                                         "amount"] + REF,
              lines, ["amount"])

    # ---- Detail Lines (supplementary schedules)
    det = []
    for s, d in reports:
        b0 = base(s, d)
        for it in d.get("detail") or []:
            # lines for an entity outside the basic statements (e.g. Coleridge's nursing home) stay visible but
            # out of the governmental / business-type totals
            ft = "outside statements" if it.get("outside_statement") else fund_type(it)
            det.append(b0 + [it.get("schedule"), ft, it.get("fund"), it.get("department"),
                               it.get("section"), it.get("category"), it.get("detail_category"),
                               it.get("original_label"), it.get("amount"), it.get("budget_final")] + ref(s, it))
    add_sheet(wb, "Detail Lines", ident + ["schedule", "fund_type", "fund", "department", "section", "category",
                                           "detail_category", "original_label", "amount", "budget_final"] + REF,
              det, ["amount", "budget_final"])

    # ---- Revenue by Source / Spending by Type
    for title, sec, cats in (("Revenue by Source", "revenue", DETAIL_REVENUE_CATEGORIES),
                             ("Spending by Type", "expenditure", DETAIL_EXPENSE_CATEGORIES)):
        rows = []
        for s, d in reports:
            for ft in FUND_TYPES:
                used = [it for it in d.get("detail") or []
                        if it.get("section") == sec and fund_type(it) == ft and it.get("detail_category") in cats]
                if not used:
                    continue
                tot = dict.fromkeys(cats, 0.0)
                for it in used:
                    tot[it["detail_category"]] += float(it.get("amount") or 0)
                extra = [round(sum(tot[c] for c in DEBT_DETAIL), 2)] if sec == "expenditure" else []
                rows.append(base(s, d) + [ft] + [round(tot[c], 2) for c in cats] + extra
                            + [round(sum(tot.values()), 2)] + ref(s, used))
        extra = ["debt service total"] if sec == "expenditure" else []
        add_sheet(wb, title, ident + ["fund_type"] + cats + extra + ["total"] + REF, rows, cats + extra + ["total"])

    # ---- Spending by Department
    rows = []
    for s, d in reports:
        by = {}
        for it in d.get("detail") or []:
            if it.get("section") == "expenditure":
                k = (fund_type(it), it.get("fund") or "", it.get("department") or it.get("category") or "")
                by.setdefault(k, []).append(it)
        totals = {ft: sum(float(i.get("amount") or 0) for k, its in by.items() if k[0] == ft for i in its) or 1
                  for ft in FUND_TYPES}
        for (ft, fund, dept), its in sorted(by.items(), key=lambda kv: (kv[0][0], -sum(float(i.get("amount") or 0)
                                                                                       for i in kv[1]))):
            v = sum(float(i.get("amount") or 0) for i in its)
            rows.append(base(s, d) + [ft, fund, dept, round(v, 2), round(v / totals[ft], 4)] + ref(s, its))
    ws = add_sheet(wb, "Spending by Department", ident + ["fund_type", "fund", "department", "amount",
                                                          "share_of_fund_type_spending"] + REF, rows, ["amount"])
    for (cell,) in ws.iter_rows(min_row=2, min_col=len(ident) + 5, max_col=len(ident) + 5):
        cell.number_format = "0.0%"

    # ---- Receipts / Disbursements by high-level category
    for title, key, cats in (("Receipts", "receipts", RECEIPT_CATEGORIES),
                             ("Disbursements", "disbursements", DISBURSEMENT_CATEGORIES)):
        rows = []
        for s, d in reports:
            for ft in FUND_TYPES:
                its = [it for it in d.get(key) or [] if fund_type(it) == ft]
                if not its:
                    continue
                tot = dict.fromkeys(cats, 0.0)
                for it in its:
                    if it.get("category") in tot:
                        tot[it["category"]] += float(it.get("amount") or 0)
                stated = amt(reported_total(d, ft, key))
                extra = [round(sum(tot[c] for c in DEBT_CATS), 2)] if key == "disbursements" else []
                rows.append(base(s, d) + [ft] + [round(tot[c], 2) for c in cats] + extra
                            + [round(sum(tot.values()), 2), stated] + ref(s, its))
        extra = ["debt service total"] if key == "disbursements" else []
        add_sheet(wb, title, ident + ["fund_type"] + cats + extra + ["total (sum of lines)", "total (stated in report)"]
                  + REF, rows, cats + extra + ["total (sum of lines)", "total (stated in report)"])

    # ---- Financing & Balances
    fin_keys = ["beginning_cash", "beginning_investments", "beginning_fund_balance", "transfers_in",
                "transfers_out", "debt_proceeds", "other_financing", "ending_cash", "ending_investments",
                "ending_fund_balance"]
    rows = []
    for s, d in reports:
        b0 = base(s, d)
        for k in fin_keys:
            v = d.get(k)
            if amt(v) is not None:
                rows.append(b0 + ["governmental", k, k, amt(v)] + ref(s, v))
        for it in d.get("adjustments") or []:
            rows.append(b0 + ["governmental", "adjustment", it.get("original_label"), it.get("amount")] + ref(s, it))
        bt = d.get("business_type") or {}
        for k in ("beginning", "ending", "transfers_net", "debt_proceeds", "other_financing"):
            if amt(bt.get(k)) is not None:
                rows.append(b0 + ["business-type", k, f"{k} ({bt.get('measure') or ''})", amt(bt.get(k))]
                            + ref(s, bt.get(k)))
        for it in bt.get("adjustments") or []:
            rows.append(b0 + ["business-type", "adjustment", it.get("original_label"), it.get("amount")]
                        + ref(s, it))
    add_sheet(wb, "Financing & Balances", ident + ["fund_type", "item", "original_label", "amount"] + REF,
              rows, ["amount"])

    # ---- Proprietary (GAAP cash flows)
    rows = []
    pk = ["beginning_cash"] + PROPRIETARY_FIELDS + ["ending_cash"]
    for s, d in reports:
        p = d.get("proprietary") or {}
        if not p:
            continue
        vals = [p.get(k) for k in pk]
        rows.append(base(s, d)
                    + [amt(v) for v in vals] + ref(s, [v for v in vals if isinstance(v, dict)]))
    add_sheet(wb, "Proprietary", ident + pk + REF, rows, pk)

    # ---- Balance Sheet
    rows = []
    for s, d in reports:
        bs = d.get("balance_sheet") or {}
        b0 = base(s, d)
        main = bs.get("statement", "")
        # lines taken from another statement (e.g. business-type lines from the Statement of Net Position on a
        # balance sheet built from the governmental funds statement) carry their own `statement`
        st_of = {}
        for k in BS_SECTIONS:
            for it in bs.get(k) or []:
                if it.get("statement"):
                    st_of.setdefault(it.get("fund_type"), it["statement"])
        combined = " + ".join(dict.fromkeys([main] + list(st_of.values()))) if st_of else main
        for sec, tot in BS_SECTIONS.items():
            for it in bs.get(sec) or []:
                rows.append(b0 + [it.get("statement") or main, bs.get("column", ""), it.get("fund_type", ""), sec,
                                  it.get("category", ""), it.get("original_label"), it.get("amount")] + ref(s, it))
            # a balance sheet split by fund type: one subtotal row per fund type (sum of its lines)
            fts = sorted({it.get("fund_type") for it in bs.get(sec) or [] if it.get("fund_type")})
            if len(fts) > 1 or (fts and any(it.get("fund_type") != fts[0] for k in BS_SECTIONS for it in bs.get(k) or [])):
                for ft in fts:
                    its = [it for it in bs.get(sec) or [] if it.get("fund_type") == ft]
                    rows.append(b0 + [st_of.get(ft, main), bs.get("column", ""), ft, sec, tot,
                                      f"(total {ft}, sum of lines)", round(sum(float(i.get("amount") or 0) for i in its), 2)]
                                + ref(s, its))
            if amt(bs.get(tot)) is not None:
                rows.append(b0 + [combined, bs.get("column", ""), "total", sec, tot, "(total as stated)",
                                  amt(bs.get(tot))] + ref(s, bs.get(tot)))
    add_sheet(wb, "Balance Sheet", ident + ["statement", "column", "fund_type", "section", "category", "original_label",
                                            "amount"] + REF,
              rows, ["amount"])

    # ---- Flagged
    fr = read_csv(FLAGGED)
    fh = ["filename", "check", "reason", "expected", "actual", "difference"]
    add_sheet(wb, "Flagged", fh + REF,
              [[r.get(h) for h in fh] + (ref(r["filename"], [p for p in (r.get("pages") or "").split(";") if p])
                                         if r.get("pages") else ref_all(r["filename"])) for r in fr],
              ["expected", "actual", "difference"])

    # ---- Progress
    pr = read_csv(PROGRESS_CSV)
    ph = [h for h in (list(pr[0].keys()) if pr else ["filename", "status"]) if h not in HIDE]
    add_sheet(wb, "Progress", ph + REF,
              [[r[h] for h in ph] + (ref_all(r["filename"]) if kept(r["filename"]) else ["", url(r["filename"])])
               for r in pr], status_col="status")

    # ---- Mistakes: errors printed in the reports themselves, and extraction errors the verifier caught
    mrows = []
    for s_, d in reports:
        m = manifest.get(Path(s_).stem, {})
        who = dict(entity_name=d.get("entity_name"), subdivision_type=m.get("subdivision_type", ""),
                   county=m.get("county", ""), filename=s_)
        for x in d.get("report_discrepancies") or []:
            if isinstance(x, dict):
                mrows.append(dict(who, kind="error in the printed report", check=x.get("check"),
                                  difference=x.get("difference"), description=x.get("note"),
                                  source_page=x.get("source_page")))
        vf = JSON_DIR / "verify" / f"{s_}.json"
        if vf.exists():
            try:
                issues = json.loads(vf.read_text(encoding="utf-8")).get("issues") or []
            except Exception:  # noqa: BLE001
                issues = []
            for i in issues:
                txt = str(i.get("issue") or "")
                if i.get("severity") == "error" or txt.startswith("[fixed"):
                    mrows.append(dict(who, kind="extraction error caught by the verifier"
                                      + (" (fixed)" if txt.startswith("[fixed") else " (open)"),
                                      check=i.get("path"), difference="", description=txt,
                                      source_page=i.get("page")))
    mh = ["entity_name", "subdivision_type", "county", "filename", "kind", "check", "difference", "description"]
    write_csv(OUTPUT / "report_mistakes.csv", mrows, mh + ["source_page"])
    add_sheet(wb, "Mistakes", mh + REF, [[r.get(h) for h in mh] + ref(r["filename"], r.get("source_page"))
                                         for r in mrows], ["difference"])
    tally = {}
    for r in mrows:
        k = (r["subdivision_type"] or "?", r["kind"].split(" (")[0])
        t = tally.setdefault(k, [set(), 0])
        t[0].add(r["filename"]); t[1] += 1
    add_sheet(wb, "Mistakes by Type", ["subdivision_type", "kind", "reports", "mistakes"],
              [[k[0], k[1], len(v[0]), v[1]] for k, v in sorted(tally.items())])

    # ---- Notes
    add_sheet(wb, "Notes", ["filename", "entity_name", "notes"] + REF,
              [[s, d.get("entity_name"), d.get("notes")] + ref_all(s) for s, d in reports if d.get("notes")])

    wb.save(xlsx)
    n = len(reports) if _scope is None else len([s for s, _ in reports if s in _scope])
    print(f"wrote {xlsx.relative_to(OUTPUT.parent)} ({n} reports)")


if __name__ == "__main__":
    main()
