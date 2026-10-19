"""Validate every extraction in output/json/ and write output/flagged.csv.

Checks per report:
  schema      - required fields, basis, known categories, a source_page on every number
  reconcile   - beginning balance + receipts - disbursements + transfers/debt/other = ending
  totals      - category amounts sum to the totals stated in the report
  proprietary - (GAAP) beginning cash + four activity totals = ending cash
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (DISBURSEMENT_CATEGORIES, FLAGGED, PROPRIETARY_FIELDS,  # noqa: E402
                    RECEIPT_CATEGORIES, amt, load_reports, page, reconcile,
                    reconcile_proprietary, sum_items, tol, write_csv)

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

    # Category amounts vs. totals stated in the report
    reported = d.get("reported_totals") or {}
    for key in ("receipts", "disbursements"):
        rt = reported.get(key)
        stated = amt(rt)
        if stated is None:
            f("totals", f"no stated total for {key} recorded in reported_totals")
            continue
        computed = sum_items(d.get(key))
        for inc in (rt.get("includes") or []) if isinstance(rt, dict) else []:
            if inc in ("transfers_in", "transfers_out", "debt_proceeds", "other_financing"):
                computed += amt(d.get(inc)) or 0
        if abs(computed - stated) > tol(stated):
            f("totals", f"sum of {key} categories != stated total", expected=float(stated),
              actual=round(float(computed), 2), pages=f"{page(rt) or ''};{pages_of(d, [key])}".strip(";"))

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
    return flags


def main():
    rows, n = [], 0
    for stem, d, err in load_reports():
        n += 1
        if err:
            rows.append(dict(filename=stem, check="schema", reason=err))
            continue
        rows.extend(check(stem, d))
    write_csv(FLAGGED, rows, FLAG_FIELDS)
    print(f"validated {n} reports; {len({r['filename'] for r in rows})} flagged ({len(rows)} issues)")


if __name__ == "__main__":
    main()
