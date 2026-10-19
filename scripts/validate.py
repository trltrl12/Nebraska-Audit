"""Validate every extraction in output/json/ and write output/flagged.csv.

Checks per report:
  schema      - required fields, basis, known categories, a source_page on every number
  reconcile   - beginning balance + receipts - disbursements + transfers/debt/other = ending
  totals      - category amounts sum to the totals stated in the report
  proprietary - (GAAP) beginning cash + four activity totals = ending cash
  balance     - balance-sheet lines sum to stated totals; assets + deferred outflows =
                liabilities + deferred inflows + fund balance/net position
  detail      - detail line items use known categories, and per fund/schedule/section they sum
                to the totals printed on the schedule (detail_totals)
  business    - business-type (utility) funds roll forward on their own

Also writes output/review.csv: items a person should look at even when the numbers check out
(lines marked "review" by the extractor, split or unlabeled tax lines).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (DETAIL_EXPENSE_CATEGORIES, DETAIL_REVENUE_CATEGORIES, DETAIL_SECTIONS,  # noqa: E402
                    ASSET_CATEGORIES, BS_SECTIONS, LIABILITY_CATEGORIES, balance_sheet_check,
                    DISBURSEMENT_CATEGORIES, FLAGGED, FUND_TYPES, PROPRIETARY_FIELDS, REVIEW_CSV,
                    RECEIPT_CATEGORIES, amt, fund_type, load_reports, page, reconcile, reconcile_business,
                    reconcile_proprietary, reported_total, sum_items, tol, write_csv)

REVIEW_FIELDS = ["filename", "entity_name", "where", "fund_type", "original_label", "amount", "reason",
                 "source_page"]

FLAG_FIELDS = ["filename", "check", "reason", "expected", "actual", "difference", "pages"]
REQUIRED = ["entity_name", "entity_type", "fiscal_year_end", "basis", "receipts", "disbursements"]
MONEY_FIELDS = ["beginning_cash", "beginning_investments", "ending_cash", "ending_investments",
                "beginning_fund_balance", "ending_fund_balance", "transfers_in", "transfers_out",
                "debt_proceeds", "other_financing"]


def pages_of(d, keys):
    ps = set()
    for k in keys:
        v = d.get(k)
        if isinstance(v, list):
            ps |= {i.get("source_page") for i in v if isinstance(i, dict)}
        elif isinstance(v, dict):
            ps.add(v.get("source_page"))
    return ";".join(str(p) for p in sorted(p for p in ps if p is not None))


def check(stem, d):
    flags = []

    def f(chk, reason, expected="", actual="", pages=""):
        diff = round(actual - expected, 2) if isinstance(expected, float) and isinstance(actual, float) else ""
        flags.append(dict(filename=stem, check=chk, reason=reason, expected=expected,
                          actual=actual, difference=diff, pages=pages))

    for k in REQUIRED:
        if d.get(k) in (None, ""):
            f("schema", f"missing {k}")
    basis = (d.get("basis") or "").lower()
    if basis not in ("cash", "gaap"):
        f("schema", f"basis must be 'cash' or 'GAAP', got {d.get('basis')!r}")
    for key, cats in (("receipts", RECEIPT_CATEGORIES), ("disbursements", DISBURSEMENT_CATEGORIES)):
        for item in d.get(key) or []:
            if item.get("category") not in cats:
                f("schema", f"{key}: unknown category {item.get('category')!r} "
                            f"({item.get('original_label')!r})", pages=str(item.get("source_page", "")))
            if item.get("source_page") in (None, ""):
                f("schema", f"{key}: no source_page for {item.get('original_label')!r}")
            if item.get("amount") is None:
                f("schema", f"{key}: no amount for {item.get('original_label')!r}")
            if item.get("fund_type") not in FUND_TYPES:
                f("schema", f"{key}: fund_type must be one of {FUND_TYPES}, got {item.get('fund_type')!r} "
                            f"({item.get('original_label')!r})")
    for k in MONEY_FIELDS:
        v = d.get(k)
        if isinstance(v, dict) and v.get("amount") is not None and v.get("source_page") in (None, ""):
            f("schema", f"no source_page for {k}")
    for item in d.get("adjustments") or []:
        if item.get("source_page") in (None, ""):
            f("schema", f"adjustments: no source_page for {item.get('original_label')!r}")

    # Balance roll-forward
    r = reconcile(d)
    bkeys = (["beginning_fund_balance", "ending_fund_balance"] if r["balance_type"] == "fund_balance"
             else ["beginning_cash", "ending_cash"])
    if r["beginning"] is None or r["ending"] is None:
        f("reconcile", f"missing {bkeys[0]} or {bkeys[1]}", pages=pages_of(d, bkeys))
    elif not r["reconciles"]:
        f("reconcile", f"{bkeys[0]} + receipts - disbursements + transfers/debt/other != {bkeys[1]}",
          expected=r["computed_ending"], actual=r["ending"], pages=pages_of(d, bkeys + ["receipts", "disbursements"]))

    # Category amounts vs. totals stated in the report, per fund type
    bt = d.get("business_type") or {}
    for fund in FUND_TYPES:
        for key in ("receipts", "disbursements"):
            lines = [i for i in d.get(key) or [] if fund_type(i) == fund]
            rt = reported_total(d, fund, key)
            stated = amt(rt)
            if stated is None:
                if lines or fund == "governmental":
                    f("totals", f"no stated {fund} total for {key} recorded in reported_totals")
                continue
            computed = sum_items(lines)
            src = d if fund == "governmental" else bt
            for inc in (rt.get("includes") or []) if isinstance(rt, dict) else []:
                if inc in ("transfers_in", "transfers_out", "debt_proceeds", "other_financing", "transfers_net"):
                    computed += amt(src.get(inc)) or 0
            if abs(computed - stated) > tol(stated):
                f("totals", f"sum of {fund} {key} lines != stated total", expected=float(stated),
                  actual=round(float(computed), 2), pages=f"{page(rt) or ''}")

    # Business-type (utility) funds roll forward on their own
    if any(fund_type(i) == "business-type" for k in ("receipts", "disbursements") for i in d.get(k) or []):
        rb = reconcile_business(d)
        if rb["beginning"] is None or rb["ending"] is None:
            f("business", "business-type lines present but business_type.beginning/ending missing")
        elif not rb["reconciles"]:
            f("business", "business-type beginning + receipts - disbursements + transfers/debt/other != ending",
              expected=rb["computed_ending"], actual=rb["ending"])

    # Proprietary cash flows (GAAP)
    if basis == "gaap" and d.get("proprietary"):
        p = d["proprietary"]
        missing = [k for k in PROPRIETARY_FIELDS if amt(p.get(k)) is None]
        if missing and len(missing) < len(PROPRIETARY_FIELDS):
            f("proprietary", f"missing {', '.join(missing)}")
        pr = reconcile_proprietary(d)
        if pr and not pr["reconciles"]:
            pages = ";".join(str(page(v)) for v in p.values() if page(v) is not None)
            f("proprietary", "beginning cash + activities != ending cash",
              expected=pr["computed"], actual=pr["ending"], pages=pages)
    # Balance sheet / statement of net position
    bs = d.get("balance_sheet")
    if not bs:
        f("balance", "no balance_sheet recorded")
    else:
        for sec, cats in (("assets", ASSET_CATEGORIES), ("liabilities", LIABILITY_CATEGORIES)):
            for it in bs.get(sec) or []:
                if it.get("category") not in cats:
                    f("schema", f"balance_sheet.{sec}: unknown category {it.get('category')!r} "
                                f"({it.get('original_label')!r})", pages=str(it.get("source_page", "")))
        for sec, tot in BS_SECTIONS.items():
            for it in bs.get(sec) or []:
                if it.get("source_page") in (None, ""):
                    f("schema", f"balance_sheet.{sec}: no source_page for {it.get('original_label')!r}")
            stated = amt(bs.get(tot))
            if stated is not None and bs.get(sec):
                s = sum_items(bs.get(sec))
                if abs(s - stated) > tol(stated):
                    f("balance", f"sum of balance_sheet.{sec} != {tot}", expected=float(stated),
                      actual=round(float(s), 2), pages=pages_of(bs, [sec]))
        bc = balance_sheet_check(d)
        if bc is None:
            f("balance", "balance_sheet missing assets or fund balance/net position total")
        elif not bc["balances"]:
            f("balance", "assets + deferred outflows != liabilities + deferred inflows + equity",
              expected=bc["right"], actual=bc["left"], pages=pages_of(bs, list(BS_SECTIONS)))
    # Detail schedules (line items)
    detail = d.get("detail") or []
    fine = {"revenue": DETAIL_REVENUE_CATEGORIES, "expenditure": DETAIL_EXPENSE_CATEGORIES}
    sums = {}
    for it in detail:
        sec = it.get("section")
        lab = it.get("original_label")
        if sec not in DETAIL_SECTIONS:
            f("schema", f"detail: section must be one of {DETAIL_SECTIONS}, got {sec!r} ({lab!r})")
            continue
        if sec in fine and it.get("detail_category") not in fine[sec]:
            f("schema", f"detail: unknown {sec} detail_category {it.get('detail_category')!r} ({lab!r})",
              pages=str(it.get("source_page", "")))
        if sec == "revenue" and it.get("category") not in RECEIPT_CATEGORIES:
            f("schema", f"detail: unknown revenue category {it.get('category')!r} ({lab!r})")
        if sec == "expenditure" and it.get("category") not in DISBURSEMENT_CATEGORIES:
            f("schema", f"detail: unknown expenditure category {it.get('category')!r} ({lab!r})")
        if it.get("source_page") in (None, "") or it.get("amount") is None:
            f("schema", f"detail: missing amount or source_page for {lab!r}")
        if it.get("fund_type") not in FUND_TYPES:
            f("schema", f"detail: fund_type must be one of {FUND_TYPES}, got {it.get('fund_type')!r} ({lab!r})")
        key = (it.get("fund") or "", it.get("schedule") or "", sec)
        sums[key] = sums.get(key, 0.0) + float(it.get("amount") or 0)
    for tot in d.get("detail_totals") or []:
        key = (tot.get("fund") or "", tot.get("schedule") or "", tot.get("section"))
        stated = amt(tot)
        if stated is None or key not in sums:
            continue
        if abs(sums[key] - stated) > tol(stated):
            f("detail", f"detail lines != printed total for {key[2]} / {key[0] or 'all funds'} / {key[1]}",
              expected=float(stated), actual=round(sums[key], 2), pages=str(tot.get("source_page", "")))
    return flags


def review_items(stem, d):
    """Things a person should know about, even when everything reconciles."""
    out = []
    for where in ("receipts", "disbursements", "detail"):
        for it in d.get(where) or []:
            reasons = []
            if it.get("review"):
                reasons.append(str(it["review"]))
            if it.get("split_from"):
                reasons.append(f"split from combined line: {it['split_from']}")
            cats = (it.get("category"), it.get("detail_category"))
            if "taxes (unlabeled)" in cats:
                reasons.append("tax line with no tax type; could not be split from the report")
            if reasons:
                out.append(dict(filename=stem, entity_name=d.get("entity_name"), where=where,
                                fund_type=it.get("fund_type"), original_label=it.get("original_label"),
                                amount=it.get("amount"), reason="; ".join(dict.fromkeys(reasons)),
                                source_page=it.get("source_page")))
    return out


def main():
    rows, review, n = [], [], 0
    for stem, d, err in load_reports():
        n += 1
        if err:
            rows.append(dict(filename=stem, check="schema", reason=err))
            continue
        rows.extend(check(stem, d))
        review.extend(review_items(stem, d))
    write_csv(FLAGGED, rows, FLAG_FIELDS)
    write_csv(REVIEW_CSV, review, REVIEW_FIELDS)
    print(f"validated {n} reports; {len({r['filename'] for r in rows})} flagged ({len(rows)} issues); "
          f"{len(review)} review items")


if __name__ == "__main__":
    main()
