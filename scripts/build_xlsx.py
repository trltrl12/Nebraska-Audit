"""Build output/cashflow.xlsx: a detailed, filterable workbook of every extraction.

Sheets:
  Summary          one row per report: balances, totals, reconciliation, flag status
  Line Items       every receipt/disbursement line as printed (original label, category,
                   amount, trimmed and original page)
  Detail Lines     every line item from the supplementary schedules (budget-to-actual,
                   departmental, combining, fund schedules): fund, department, category,
                   detailed category, actual and final budget
  Revenue by Source       one row per report, one column per detailed revenue category
  Spending by Type        one row per report, one column per detailed expenditure category
  Spending by Department  one row per report and department/function, with its share of spending
  Receipts         one row per report, one column per receipt category
  Disbursements    one row per report, one column per disbursement category
  Financing        transfers, debt proceeds, other financing, adjustments, investments
  Proprietary      GAAP proprietary-fund cash flows by activity
  Balance Sheet    every asset, liability, deferral and fund balance/net position line
  Flagged          validation issues (output/flagged.csv)
  Progress         status of every report (output/progress.csv)
  Notes            the extractor's notes per report
  Review           items to look at even when the numbers check out (split/unlabeled taxes, etc.)
Lines carry fund_type: governmental or business-type (utility funds), reported separately.
Every sheet has a source_url column linking to the report PDF on the Auditor's website.
Run after validate.py and build_csv.py (it reads their outputs).
"""
import sys
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (page_ranges, DETAIL_EXPENSE_CATEGORIES,  # noqa: E402
                     DETAIL_REVENUE_CATEGORIES,  # noqa: E402
                    FUND_TYPES, REVIEW_CSV, fund_type, reported_total,
                    BS_SECTIONS, DISBURSEMENT_CATEGORIES, FILE_LIST, FLAGGED, MANIFEST, OUTPUT, PROGRESS_CSV,  # noqa: E402
                    PROPRIETARY_FIELDS, RECEIPT_CATEGORIES, SUMMARY_CSV, amt, load_reports, page,
                    read_csv)

XLSX = OUTPUT / "cashflow.xlsx"
MONEY = '#,##0.00;[Red](#,##0.00)'
HEAD_FILL = PatternFill("solid", fgColor="1F4E78")
BAD_FILL = PatternFill("solid", fgColor="F8CBAD")
GOOD_FILL = PatternFill("solid", fgColor="C6EFCE")


def num(v):
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return v


def add_sheet(wb, title, header, rows, money_cols=(), status_col=None):
    ws = wb.create_sheet(title)
    ws.append(header)
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = HEAD_FILL
        c.alignment = Alignment(wrap_text=True, vertical="top")
    for r in rows:
        ws.append([num(v) if header[i] in money_cols else v for i, v in enumerate(r)])
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
    for col in ("source_url", "page_link"):  # clickable links to the PDF on the Auditor's website
        if col not in idx:
            continue
        ws.column_dimensions[get_column_letter(idx[col])].width = 14
        for (cell,) in ws.iter_rows(min_row=2, min_col=idx[col], max_col=idx[col]):
            v = str(cell.value or "")
            if v.startswith("http"):
                cell.hyperlink = v
                cell.value = f"Open p.{v.rsplit('#page=', 1)[1]}" if "#page=" in v else "Open PDF"
                cell.font = Font(color="0563C1", underline="single")
    ws.freeze_panes = "B2" if title != "Notes" else "A2"
    if rows:
        ws.auto_filter.ref = ws.dimensions
    return ws


def main():
    manifest = {Path(r["filename"]).stem: r for r in read_csv(MANIFEST)}
    reports = [(s, d) for s, d, e in load_reports() if d and not e]
    file_urls = {Path(r["filename"]).stem: r.get("url", "") for r in read_csv(FILE_LIST)}

    def url(stem):
        return manifest.get(stem, {}).get("url") or file_urls.get(stem, "")

    def orig(stem, p):
        """Page number in the original audit for page p of the trimmed PDF."""
        kept = (manifest.get(stem, {}).get("pages_kept") or "").split(";")
        try:
            return int(kept[int(p) - 1])
        except (ValueError, IndexError, TypeError):
            return None

    def link(stem, p):
        """Link that opens the original PDF on the Auditor's site at the page trimmed page p came from."""
        u, rp = url(stem), orig(stem, p)
        return f"{u}#page={rp}" if u and rp else u

    def where(stem, p):
        """[trimmed_page, report_page, page_link] for one number."""
        return [p, orig(stem, p), link(stem, p)]

    def page_list(stem, items):
        """Original-report pages of a group of lines, as ranges ("6-9, 23")."""
        return page_ranges([orig(stem, i.get("source_page")) for i in items if orig(stem, i.get("source_page"))])

    WHERE = ["trimmed_page", "report_page", "page_link"]

    wb = Workbook()
    wb.remove(wb.active)

    srows = read_csv(SUMMARY_CSV)
    sh = list(srows[0].keys()) if srows else ["entity_name"]
    money = [h for h in sh if h not in ("entity_name", "entity_type", "county", "filename", "fiscal_year_end",
                                        "basis", "filing_type", "source_url", "balance_type", "reconciles",
                                        "prop_reconciles", "balance_sheet_statement", "balance_sheet_balances",
                                        "flagged", "fiscal_year", "bt_measure", "bt_reconciles", "review_items")]
    money = [h for h in money if h != "report_pages"]
    add_sheet(wb, "Summary", sh, [[r[h] for h in sh] for r in srows], money, "reconciles")

    rv = read_csv(REVIEW_CSV)
    rh = ["entity_name", "filename", "where", "fund_type", "original_label", "amount", "reason"]
    add_sheet(wb, "Review", rh + WHERE + ["source_url"],
              [[r.get(h) for h in rh] + where(r["filename"], r.get("source_page")) + [url(r["filename"])] for r in rv],
              ["amount"])

    ident = ["entity_name", "entity_type", "filename", "fiscal_year_end", "basis", "source_url"]
    lines = []
    for s, d in reports:
        base = [d.get("entity_name"), d.get("entity_type"), s, d.get("fiscal_year_end"), d.get("basis"), url(s)]
        for section in ("receipts", "disbursements"):
            for it in d.get(section) or []:
                lines.append(base + [fund_type(it), section, it.get("category"), it.get("original_label"),
                                     it.get("amount")] + where(s, it.get("source_page")))
    add_sheet(wb, "Line Items", ident + ["fund_type", "section", "category", "original_label", "amount"] + WHERE,
              lines, ["amount"])

    # ---- detail schedules
    det = []
    for s, d in reports:
        base = [d.get("entity_name"), d.get("entity_type"), s, d.get("fiscal_year_end"), d.get("basis"), url(s)]
        for it in d.get("detail") or []:
            det.append((s, d, it, base + [it.get("schedule"), fund_type(it), it.get("fund"), it.get("department"), it.get("section"),
                                          it.get("category"), it.get("detail_category"), it.get("original_label"),
                                          it.get("amount"), it.get("budget_final")] + where(s, it.get("source_page"))))
    add_sheet(wb, "Detail Lines", ident + ["schedule", "fund_type", "fund", "department", "section", "category",
                                           "detail_category", "original_label", "amount", "budget_final"] + WHERE,
              [r for *_, r in det], ["amount", "budget_final"])

    for title, sec, cats in (("Revenue by Source", "revenue", DETAIL_REVENUE_CATEGORIES),
                             ("Spending by Type", "expenditure", DETAIL_EXPENSE_CATEGORIES)):
        rows = []
        for s, d in reports:
            for ft in FUND_TYPES:
                tot = dict.fromkeys(cats, 0.0)
                used = []
                for it in d.get("detail") or []:
                    if it.get("section") == sec and fund_type(it) == ft and it.get("detail_category") in tot:
                        tot[it["detail_category"]] += float(it.get("amount") or 0)
                        used.append(it)
                if any(tot.values()):
                    rows.append([d.get("entity_name"), d.get("entity_type"), s, d.get("fiscal_year_end"),
                                 d.get("basis"), url(s), page_list(s, used), ft] + [round(tot[c], 2) for c in cats]
                                + [round(sum(tot.values()), 2)])
        add_sheet(wb, title, ident + ["report_pages", "fund_type"] + cats + ["total"], rows, cats + ["total"])

    rows = []
    for s, d in reports:
        by, used = {}, {}
        for it in d.get("detail") or []:
            if it.get("section") == "expenditure":
                k = (fund_type(it), it.get("fund") or "", it.get("department") or it.get("category") or "")
                by[k] = by.get(k, 0.0) + float(it.get("amount") or 0)
                used.setdefault(k, []).append(it)
        totals = {ft: sum(v for k, v in by.items() if k[0] == ft) or 1 for ft in FUND_TYPES}
        for (ft, fund, dept), v in sorted(by.items(), key=lambda kv: (kv[0][0], -kv[1])):
            first = used[(ft, fund, dept)][0].get("source_page")
            rows.append([d.get("entity_name"), d.get("entity_type"), s, d.get("fiscal_year_end"), d.get("basis"),
                         url(s), ft, fund, dept, round(v, 2), round(v / totals[ft], 4),
                         page_list(s, used[(ft, fund, dept)]), link(s, first)])
    ws = add_sheet(wb, "Spending by Department", ident + ["fund_type", "fund", "department", "amount",
                                                          "share_of_fund_type_spending", "report_pages", "page_link"],
                   rows, ["amount"])
    for (cell,) in ws.iter_rows(min_row=2, min_col=len(ident) + 5, max_col=len(ident) + 5):
        cell.number_format = "0.0%"

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
                rows.append([d.get("entity_name"), d.get("entity_type"), s, d.get("fiscal_year_end"), d.get("basis"),
                             url(s), page_list(s, its), ft] + [round(tot[c], 2) for c in cats]
                            + [round(sum(tot.values()), 2), stated])
        add_sheet(wb, title, ident + ["report_pages", "fund_type"] + cats + ["total (sum of lines)", "total (stated in report)"], rows,
                  cats + ["total (sum of lines)", "total (stated in report)"])

    fin_keys = ["beginning_cash", "beginning_investments", "beginning_fund_balance", "transfers_in",
                "transfers_out", "debt_proceeds", "other_financing", "ending_cash", "ending_investments",
                "ending_fund_balance"]
    rows = []
    for s, d in reports:
        base = [d.get("entity_name"), d.get("entity_type"), s, d.get("fiscal_year_end"), d.get("basis"), url(s)]
        for k in fin_keys:
            v = d.get(k)
            if amt(v) is not None:
                rows.append(base + ["governmental", k, k, amt(v)] + where(s, page(v)))
        for it in d.get("adjustments") or []:
            rows.append(base + ["governmental", "adjustment", it.get("original_label"), it.get("amount")]
                        + where(s, it.get("source_page")))
        bt = d.get("business_type") or {}
        for k in ("beginning", "ending", "transfers_net", "debt_proceeds", "other_financing"):
            if amt(bt.get(k)) is not None:
                rows.append(base + ["business-type", k, f"{k} ({bt.get('measure') or ''})".strip(), amt(bt.get(k))]
                            + where(s, page(bt.get(k))))
        for it in bt.get("adjustments") or []:
            rows.append(base + ["business-type", "adjustment", it.get("original_label"), it.get("amount")]
                        + where(s, it.get("source_page")))
    add_sheet(wb, "Financing & Balances", ident + ["fund_type", "item", "original_label", "amount"] + WHERE,
              rows, ["amount"])

    rows = []
    for s, d in reports:
        p = d.get("proprietary") or {}
        if not p:
            continue
        vals = [p.get(k) for k in ["beginning_cash"] + PROPRIETARY_FIELDS + ["ending_cash"]]
        rows.append([d.get("entity_name"), d.get("entity_type"), s, d.get("fiscal_year_end"), d.get("basis"), url(s),
                     page_list(s, [v for v in vals if isinstance(v, dict)])] + [amt(v) for v in vals])
    pk = ["beginning_cash"] + PROPRIETARY_FIELDS + ["ending_cash"]
    add_sheet(wb, "Proprietary", ident + ["report_pages"] + pk, rows, pk)

    rows = []
    for s, d in reports:
        bs = d.get("balance_sheet") or {}
        base = [d.get("entity_name"), d.get("entity_type"), s, d.get("fiscal_year_end"), d.get("basis"), url(s)]
        for sec, tot in BS_SECTIONS.items():
            for it in bs.get(sec) or []:
                rows.append(base + [bs.get("statement", ""), sec, it.get("category", ""), it.get("original_label"),
                                    it.get("amount")] + where(s, it.get("source_page")))
            if amt(bs.get(tot)) is not None:
                rows.append(base + [bs.get("statement", ""), sec, tot, "(total as stated)", amt(bs.get(tot))]
                            + where(s, page(bs.get(tot))))
    add_sheet(wb, "Balance Sheet", ident + ["statement", "section", "category", "original_label", "amount"] + WHERE,
              rows, ["amount"])

    fr = read_csv(FLAGGED)
    fh = ["filename", "check", "reason", "expected", "actual", "difference"]
    add_sheet(wb, "Flagged", fh + ["report_pages", "source_url"],
              [[r.get(h) for h in fh] + [page_ranges([orig(r["filename"], p) for p in (r.get("pages") or "").split(";")
                                                      if orig(r["filename"], p)]), url(r["filename"])] for r in fr],
              ["expected", "actual", "difference"])

    pr = read_csv(PROGRESS_CSV)
    ph = list(pr[0].keys()) if pr else ["filename", "status"]
    add_sheet(wb, "Progress", ph, [[r[h] for h in ph] for r in pr], status_col="status")

    add_sheet(wb, "Notes", ["filename", "entity_name", "notes"],
              [[s, d.get("entity_name"), d.get("notes")] for s, d in reports if d.get("notes")])

    wb.save(XLSX)
    print(f"wrote {XLSX.relative_to(OUTPUT.parent)} ({len(reports)} reports, {len(lines)} line items)")


if __name__ == "__main__":
    main()
