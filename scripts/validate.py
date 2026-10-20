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
  on_page     - every extracted amount appears on the page it cites (text layer or OCR); see
                pagecheck.py
  verifier    - discrepancies found by the independent verifier agent (output/json/verify/)

Every comparison is exact: a difference over $1 is flagged; any difference from 1 cent to $1 goes
to the review list (possible report rounding). Also writes output/review.csv: items a person should
look at even when the numbers check out (lines marked "review" by the extractor, split or unlabeled
tax lines, computed values, amounts not found on their page, verifier notes).
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (DETAIL_EXPENSE_CATEGORIES, DETAIL_REVENUE_CATEGORIES, DETAIL_SECTIONS,  # noqa: E402
                    ASSET_CATEGORIES, BS_SECTIONS, LIABILITY_CATEGORIES, balance_sheet_check,
                    DISBURSEMENT_CATEGORIES, FLAGGED, FUND_TYPES, PROPRIETARY_FIELDS, REVIEW_CSV,
                    RECEIPT_CATEGORIES, amt, fund_type, load_reports, page, reconcile, reconcile_business,
                    reconcile_proprietary, reported_total, sum_items, write_csv, JSON_DIR)
import pagecheck  # noqa: E402

FLAG_OVER = 1.0      # dollars: larger differences are flagged
REVIEW_OVER = 0.005  # dollars: any smaller nonzero difference goes to the review list

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
    flags, reviews = [], []

    def f(chk, reason, expected="", actual="", pages=""):
        diff = round(actual - expected, 2) if isinstance(expected, float) and isinstance(actual, float) else ""
        flags.append(dict(filename=stem, check=chk, reason=reason, expected=expected,
                          actual=actual, difference=diff, pages=pages))

    def rv(where, reason, label="", amount="", src_page="", ftype=""):
        reviews.append(dict(filename=stem, entity_name=d.get("entity_name"), where=where, fund_type=ftype,
                            original_label=label, amount=amount, reason=reason, source_page=src_page))

    def cmp(chk, reason, expected, actual, pages=""):
        """Exact comparison: flag over $1, review any smaller nonzero difference."""
        if expected is None or actual is None:
            return
        diff = float(actual) - float(expected)
        if abs(diff) > FLAG_OVER:
            f(chk, reason, expected=round(float(expected), 2), actual=round(float(actual), 2), pages=pages)
        elif abs(diff) >= REVIEW_OVER:
            first = str(pages).split(";")[0] if pages else ""
            rv(chk, f"{reason}: off by {diff:+,.2f} (report rounding?)", amount=round(float(actual), 2),
               src_page=first)

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

    # Entities that are entirely business-type (hospitals, power/housing authorities, community
    # colleges...) have no governmental funds: skip the governmental checks for them.
    has_gov = any(fund_type(i) == "governmental" for k in ("receipts", "disbursements") for i in d.get(k) or [])
    has_bt = any(fund_type(i) == "business-type" for k in ("receipts", "disbursements") for i in d.get(k) or [])
    gov_only_checks = has_gov or not has_bt

    # Balance roll-forward
    r = reconcile(d) if gov_only_checks else dict(beginning=0, ending=0, reconciles=True, balance_type="none")
    bkeys = (["beginning_fund_balance", "ending_fund_balance"] if r["balance_type"] == "fund_balance"
             else ["beginning_cash", "ending_cash"])
    if r["beginning"] is None or r["ending"] is None:
        f("reconcile", f"missing {bkeys[0]} or {bkeys[1]}", pages=pages_of(d, bkeys))
    elif gov_only_checks:
        cmp("reconcile", f"governmental {bkeys[0]} + receipts - disbursements + transfers/debt/other vs {bkeys[1]}",
            r["computed_ending"], r["ending"], pages=pages_of(d, bkeys))

    # Category amounts vs. totals stated in the report, per fund type
    bt = d.get("business_type") or {}
    for fund in FUND_TYPES:
        for key in ("receipts", "disbursements"):
            lines = [i for i in d.get(key) or [] if fund_type(i) == fund]
            rt = reported_total(d, fund, key)
            stated = amt(rt)
            if stated is None:
                if lines or (fund == "governmental" and gov_only_checks):
                    f("totals", f"no stated {fund} total for {key} recorded in reported_totals")
                continue
            computed = sum_items(lines)
            src = d if fund == "governmental" else bt
            for inc in (rt.get("includes") or []) if isinstance(rt, dict) else []:
                if inc in ("transfers_in", "transfers_out", "debt_proceeds", "other_financing", "transfers_net"):
                    computed += amt(src.get(inc)) or 0
            cmp("totals", f"sum of {fund} {key} lines vs printed total", float(stated), computed,
                pages=f"{page(rt) or ''}")

    # Business-type (utility) funds roll forward on their own
    if any(fund_type(i) == "business-type" for k in ("receipts", "disbursements") for i in d.get(k) or []):
        rb = reconcile_business(d)
        if rb["beginning"] is None or rb["ending"] is None:
            f("business", "business-type lines present but business_type.beginning/ending missing")
        else:
            cmp("business", "business-type beginning + receipts - disbursements + transfers/debt/other vs ending",
                rb["computed_ending"], rb["ending"], pages=str(page(bt.get("ending")) or ""))

    # Proprietary cash flows (GAAP)
    if basis == "gaap" and d.get("proprietary"):
        p = d["proprietary"]
        missing = [k for k in PROPRIETARY_FIELDS if amt(p.get(k)) is None]
        if missing and len(missing) < len(PROPRIETARY_FIELDS):
            f("proprietary", f"missing {', '.join(missing)}")
        pr = reconcile_proprietary(d)
        if pr:
            pages = ";".join(str(page(v)) for v in p.values() if isinstance(v, dict) and page(v) is not None)
            cmp("proprietary", "cash flows: beginning cash + activities vs ending cash", pr["computed"], pr["ending"],
                pages=pages)
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
                cmp("balance", f"sum of balance_sheet.{sec} lines vs printed {tot}", float(stated),
                    sum_items(bs.get(sec)), pages=pages_of(bs, [sec]))
        bc = balance_sheet_check(d)
        if bc is None:
            f("balance", "balance_sheet missing assets or fund balance/net position total")
        else:
            cmp("balance", "assets + deferred outflows vs liabilities + deferred inflows + equity",
                bc["right"], bc["left"], pages=pages_of(bs, list(BS_SECTIONS)))
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
        cmp("detail", f"detail lines vs printed total for {key[2]} / {key[0] or 'all funds'} / {key[1]}",
            float(stated), sums[key], pages=str(tot.get("source_page", "")))

    # Every amount must appear on the page it cites
    for miss in pagecheck.check_report(stem, d):
        rv("on_page", miss["reason"], miss["label"], miss["amount"], miss["source_page"], miss.get("fund_type", ""))

    # Independent verifier
    vf = JSON_DIR / "verify" / f"{stem}.json"
    if vf.exists():
        try:
            ver = json.loads(vf.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            ver = {"issues": [{"severity": "error", "issue": f"unreadable verifier file: {e}"}]}
        for i in ver.get("issues") or []:
            text = f"verifier: {i.get('issue', '')}" + (f" (page shows {i['on_page']})" if i.get("on_page") not in
                                                        (None, "") else "")
            if i.get("severity") == "error":
                f("verifier", f"{i.get('path', '')}: {text}", pages=str(i.get("page", "")))
            else:
                rv("verifier", text, i.get("path", ""), i.get("extracted", ""), i.get("page", ""))
    else:
        rv("verifier", "not yet independently verified")
    return flags, reviews


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
            if it.get("computed"):
                reasons.append(f"computed, not printed: {it['computed']}")
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
        fl, rvs = check(stem, d)
        rows.extend(fl)
        review.extend(review_items(stem, d))
        review.extend(rvs)
    write_csv(FLAGGED, rows, FLAG_FIELDS)
    write_csv(REVIEW_CSV, review, REVIEW_FIELDS)
    print(f"validated {n} reports; {len({r['filename'] for r in rows})} flagged ({len(rows)} issues); "
          f"{len(review)} review items")


if __name__ == "__main__":
    main()
