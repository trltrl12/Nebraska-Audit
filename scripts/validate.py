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
tax lines, computed values, amounts not found on their page, verifier notes). A value marked
"checked" (confirmed against the page image), a report_discrepancies entry marked "checked" (listed
on the workbook's Mistakes tab instead) and a verifier issue marked "resolved" stay off the list.
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (TRIMMED, DETAIL_EXPENSE_CATEGORIES, DETAIL_REVENUE_CATEGORIES, DETAIL_SECTIONS,  # noqa: E402
                    ASSET_CATEGORIES, BS_SECTIONS, LIABILITY_CATEGORIES, balance_sheet_check,
                    DISBURSEMENT_CATEGORIES, FLAGGED, FUND_TYPES, PROPRIETARY_FIELDS, REVIEW_CSV,
                    RECEIPT_CATEGORIES, amt, fund_type, load_reports, page, reconcile, reconcile_business,
                    reconcile_proprietary, reported_total, sum_items, write_csv, JSON_DIR, VALIDATED, json_hash)
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

    declared = [x for x in d.get("report_discrepancies") or [] if isinstance(x, dict)]
    used = set()

    def cmp(chk, reason, expected, actual, pages=""):
        """Exact comparison: flag over $1, review any smaller nonzero difference. A difference the
        extractor declared in report_discrepancies (the printed report itself doesn't add up) goes
        to the review list with that wording instead of being flagged."""
        if expected is None or actual is None:
            return
        diff = float(actual) - float(expected)
        # Pick the declared discrepancy that best fits this comparison: same size, then same
        # check, same page, and not already used by another comparison.
        pg = {p.strip() for p in str(pages).split(";") if p.strip()}
        best, best_score = None, -1
        for n, x in enumerate(declared):
            if amt(x.get("difference")) is None or abs(diff) < REVIEW_OVER \
                    or abs(abs(diff) - abs(amt(x.get("difference")))) >= 0.01:
                continue
            same_check = x.get("check") == chk or (chk == "totals" and x.get("check") == "business"
                                                   and "business" in reason)
            if not same_check and x.get("check") != "totals":
                continue
            score = 4 * same_check + 2 * (str(x.get("source_page")) in pg) + (n not in used)
            if score > best_score:
                best, best_score = n, score
        if best is not None:
            x = declared[best]
            used.add(best)
            if x.get("checked"):  # confirmed against the report; listed on the Mistakes tab only
                return
            rv(chk, f"THE REPORT ITSELF DOESN'T ADD UP: {reason}: off by {diff:+,.2f} as printed; "
                    f"{x.get('note', '')}".strip("; "), amount=round(float(actual), 2),
               src_page=str(x.get("source_page") or ""))
            return
        if abs(diff) > FLAG_OVER:
            f(chk, reason, expected=round(float(expected), 2), actual=round(float(actual), 2), pages=pages)
        elif abs(diff) >= REVIEW_OVER:
            first = str(pages).split(";")[0] if pages else ""
            rv(chk, f"{reason}: off by {diff:+,.2f} (report rounding?)", amount=round(float(actual), 2),
               src_page=first)

    money = ["beginning_cash", "beginning_investments", "beginning_fund_balance", "ending_cash",
             "ending_investments", "ending_fund_balance", "transfers_in", "transfers_out", "debt_proceeds",
             "other_financing"]
    for where, obj, keys in (("", d, money), ("business_type.", d.get("business_type") or {},
                              ["beginning", "ending", "transfers_net", "debt_proceeds", "other_financing"])):
        for k in keys:
            v = obj.get(k)
            if v is not None and not (isinstance(v, dict) or isinstance(v, (int, float))):
                f("schema", f"{where}{k} must be one money object {{amount, source_page}}, got {type(v).__name__}")
                obj[k] = None
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
            # lines printed below the report's own total line (e.g. interest income listed under
            # "other financing") are real receipts/disbursements but not part of the printed total
            lines = [i for i in d.get(key) or [] if fund_type(i) == fund and not i.get("outside_printed_total")]
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
            # hidden transfers split out of a printed line stay inside the printed total
            for x in (rt.get("includes_split") or []) if isinstance(rt, dict) else []:
                computed += amt(x) or 0
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
        missing = [k for k in PROPRIETARY_FIELDS if k not in p]  # null = section not printed
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
        # Budget forms have no balance sheet by design (extractor rules: balance_sheet null).
        if not stem.endswith("_B2526"):
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
        if it.get("outside_printed_total") and sec != "other financing":
            sums.setdefault(key, 0.0)  # printed below the schedule's total line: not part of it
            continue
        sums[key] = sums.get(key, 0.0) + float(it.get("amount") or 0)
        if sec == "other financing" and not it.get("outside_printed_total"):
            # signed: + in (counts toward revenue), − out (toward expenditure)
            a = float(it.get("amount") or 0)
            if it.get("printed_in") in ("revenue", "expenditure"):
                # printed inside that section's total with the opposite sign (e.g. warrants issued
                # shown as a negative disbursement): + in lowers an expenditure total, − out a revenue one
                k2 = (key[0], key[1], f"{it['printed_in']}+of")
                sums[k2] = sums.get(k2, 0.0) + (-a if it["printed_in"] == "expenditure" else a)
            else:
                k2 = (key[0], key[1], "revenue+of" if a > 0 else "expenditure+of")
                sums[k2] = sums.get(k2, 0.0) + abs(a)
    for tot in d.get("detail_totals") or []:
        key = (tot.get("fund") or "", tot.get("schedule") or "", tot.get("section"))
        stated = amt(tot)
        if stated is None or key not in sums:
            continue
        if "other financing" in (tot.get("includes") or []):  # printed total includes the transfers
            sums[key] += sums.get((key[0], key[1], f"{key[2]}+of"), 0.0)
        for sch in tot.get("includes_schedules") or []:  # lines moved to a more detailed schedule
            sums[key] += sums.get((key[0], sch, key[2]), 0.0)
        cmp("detail", f"detail lines vs printed total for {key[2]} / {key[0] or 'all funds'} / {key[1]}",
            float(stated), sums[key], pages=str(tot.get("source_page", "")))

    category_checks(d, f, rv)

    # The extractor must have read every page of the trimmed PDF ("Read pages 1-N of N" in notes)
    tp = TRIMMED / f"{stem}.pdf"
    seen = re.findall(r"Read pages 1-(\d+) of (\d+)", d.get("notes") or "")
    if tp.exists() and not seen:
        f("coverage", "notes don't end with \"Read pages 1-N of N\", so it isn't confirmed that every page "
                      "of the trimmed PDF was read: re-extract")
    if tp.exists() and seen:
        import pymupdf
        n = pymupdf.open(tp).page_count
        if int(seen[-1][0]) != n or int(seen[-1][1]) != n:
            f("coverage", f"notes say pages 1-{seen[-1][0]} of {seen[-1][1]} were read, but the trimmed PDF "
                          f"has {n} pages (re-trimmed or not fully read): re-extract")

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
            if i.get("resolved"):  # checked against the page and settled
                continue
            if i.get("severity") == "error" and not str(i.get("issue", "")).startswith("[fixed"):
                f("verifier", f"{i.get('path', '')}: {text}", pages=str(i.get("page", "")))
            else:
                rv("verifier", text, i.get("path", ""), i.get("extracted", ""), i.get("page", ""))
    else:
        rv("verifier", "not yet independently verified")
    return flags, reviews


# Label patterns and the categories they should get (receipts category, detail_category).
# A line matching a pattern but categorized differently goes to review.
LABEL_RULES = [
    (r"special assess", "special assessments", "special assessments"),
    (r"keno|lottery", "keno/lottery", "keno/lottery"),
    (r"pro[- ]?rate", "intergovernmental/state aid", "motor vehicle pro-rate"),
    (r"homestead", "Homestead Exemption", "Homestead Exemption"),
    (r"property tax credit|^(\d+ )?(state )?tax credits?$|property tax relief", "Property Tax Credit", "Property Tax Credit"),
    (r"^(\d+ )?(esu|educational service unit)( receipts)?$", "ESU Receipts", "ESU Receipts"),
    (r"motor vehicle|wheel tax", "motor vehicle tax", "motor vehicle tax"),
    (r"occupation|franchise", "occupation/franchise tax", "occupation/franchise tax"),
    (r"lodging", "lodging tax", "lodging tax"),
    (r"inheritance", "inheritance tax", "inheritance tax"),
    (r"name ?plate", "Nameplate Capacity Tax", "Nameplate Capacity Tax"),
    (r"in[- ]lieu", "In Lieu of Tax", "In Lieu of Tax"),
    (r"car ?line", "Carline Tax", "Carline Tax"),
    (r"public power.*tax", "Public Power District Sales Tax", "Public Power District Sales Tax"),
    (r"penalt|interest on (delinquent |property )?tax", "Penalties and Interest on Taxes", "Penalties and Interest on Taxes"),
    (r"^(\d+ )?interest( income| earned| on investments)?$|investment (income|earnings)", "investment income",
     "investment income"),
    (r"sale of (property|assets|capital|equipment)|gain on sale", "sale of assets", "sale of assets"),
    (r"reimburse|refund", "reimbursements/refunds", "reimbursements/refunds"),
    (r"private sector share|developer contribution", "Private Sector Share of Projects", "Private Sector Share of Projects"),
    (r"donation|contribution", "donations/contributions", "donations/contributions"),
    (r"licen[cs]e|permit", "licenses and permits", "licenses and permits"),
    (r"\bfines?\b|forfeit", "fines and forfeitures", "fines and forfeitures"),
    (r"\bTEEOSA\b|state equalization aid", "intergovernmental/state aid", "state school aid (TEEOSA)"),
    (r"highway allocation", "intergovernmental/state aid", "highway allocation"),
]
TRANSFER_RE = re.compile(r"\btransfers?\b(?! station)", re.I)
# A catch-all category is right when the report's own label says "other"/"miscellaneous".
OTHER_LABEL_RE = re.compile(r"\b(other|misc\w*|sundry|various)\b", re.I)
# Budget forms: the workbook's filing_type column already marks the Actual/Estimated column.
BUDGET_FORM_RE = re.compile(r"_B\d{4}$")
ESTIMATED_RE = re.compile(r"actual/estimated|estimated column|prior-year actual column|^budget form$", re.I)
OTHER_SHARE = 0.10   # "other" above this share of a fund type's receipts/disbursements → review


def _norm(label):
    return re.sub(r"^\d+\s+|[^a-z0-9]+", " ", str(label or "").lower()).strip()


def category_checks(d, f, rv):
    """Catch lines that are probably in the wrong category, and too much in 'other'."""
    lines = [(k, i, it) for k in ("receipts", "disbursements", "detail")
             for i, it in enumerate(d.get(k) or []) if isinstance(it, dict)]
    for k, i, it in lines:
        lab, sec = str(it.get("original_label") or ""), it.get("section")
        where = f"{k}[{i}]"
        if it.get("checked"):  # category confirmed against the report
            continue
        # Transfers are financing, never revenue or spending
        if TRANSFER_RE.search(lab) and (k != "detail" or sec != "other financing") \
                and not it.get("split_from") and not re.search(r"intergovernmental transfer", lab, re.I):
            f("category", f"{where} {lab!r} {amt(it):,.0f}: transfer recorded as "
              f"{'a ' + k[:-1] if k != 'detail' else sec}; transfers belong in transfers_in/out "
              f"(detail section 'other financing')", pages=str(it.get("source_page", "")))
            continue
        if k == "detail" and sec == "other financing":
            continue
        is_rev = k == "receipts" or sec == "revenue"
        for pat, cat, dcat in LABEL_RULES:
            if re.search(pat, lab, re.I) and is_rev:
                got = (it.get("category"), it.get("detail_category") if k == "detail" else dcat)
                if got != (cat, dcat):
                    rv("category", f"{lab!r} is usually {cat}" + (f" / {dcat}" if k == "detail" else "")
                       + f", recorded as {' / '.join(str(x) for x in got[: 2 if k == 'detail' else 1])}",
                       lab, amt(it), it.get("source_page", ""), it.get("fund_type", ""))
                break
        if (it.get("category") in ("other", "other taxes") or it.get("detail_category") in (
                "miscellaneous revenue", "miscellaneous expenditure", "other local taxes")) \
                and not OTHER_LABEL_RE.search(lab) and not it.get("category_decision"):  # decided by the user
            rv("other", f"in a catch-all category ({it.get('category')}"
               + (f" / {it.get('detail_category')}" if k == "detail" else "") + "); confirm nothing more specific fits",
               lab, amt(it), it.get("source_page", ""), it.get("fund_type", ""))
    # Same label categorized differently within one report
    seen = {}
    for k, i, it in lines:
        if it.get("checked") or not (k == "receipts" or (k == "detail" and it.get("section") == "revenue")):
            continue  # spending labels like "Supplies" rightly differ by function
        key = ("rev" if k == "receipts" or it.get("section") == "revenue" else "exp", _norm(it.get("original_label")))
        seen.setdefault(key, set()).add(it.get("category"))
    for (side, lab), cats in sorted(seen.items()):
        if len(cats) > 1 and lab:
            rv("category", f"same label {lab!r} has different categories in this report: "
               + ", ".join(sorted(str(c) for c in cats)), lab)
    # Share of each fund type's totals sitting in "other"
    for key in ("receipts", "disbursements"):
        for ft in FUND_TYPES:
            items = [it for it in d.get(key) or [] if isinstance(it, dict) and fund_type(it) == ft
                     and amt(it) is not None]
            total = sum(abs(amt(it)) for it in items)
            other = sum(abs(amt(it)) for it in items if it.get("category") == "other" and not it.get("checked")
                        and not OTHER_LABEL_RE.search(str(it.get("original_label") or "")))
            if total and other / total > OTHER_SHARE:
                rv("other", f"{other / total:.0%} of {ft} {key} ({other:,.0f} of {total:,.0f}) is in 'other'",
                   amount=round(other, 2), ftype=ft)


def review_items(stem, d):
    """Things a person should know about, even when everything reconciles."""
    out = []
    groups = [(w, d.get(w) or []) for w in ("receipts", "disbursements", "detail", "adjustments")]
    bs = d.get("balance_sheet") or {}
    groups += [(f"balance_sheet.{sec}", bs.get(sec) or []) for sec in BS_SECTIONS]
    bt = d.get("business_type") or {}
    groups.append(("business_type.adjustments", bt.get("adjustments") or []))
    # Single money fields (balances, transfers, totals) can carry a review note too
    singles = [(k, v) for k, v in d.items() if isinstance(v, dict) and "amount" in v]
    singles += [(f"business_type.{k}", v) for k, v in bt.items() if isinstance(v, dict) and "amount" in v]
    groups.append(("fields", [dict(v, original_label=k) for k, v in singles]))
    for where, items in groups:
        for it in items:
            if not isinstance(it, dict) or it.get("checked"):
                continue
            reasons = []
            rv = it.get("review")
            if rv and BUDGET_FORM_RE.search(stem):
                rv = "; ".join(c for c in re.split(r";\s*", str(rv)) if c.strip() and c.strip() != "True"
                               and not ESTIMATED_RE.search(c.strip())) if rv is not True else ""
            if rv:
                reasons.append(str(rv))
            if it.get("split_from"):
                reasons.append(f"split from combined line: {it['split_from']}")
            if it.get("computed") and where != "fields":
                reasons.append(f"computed, not printed: {it['computed']}")
            cats = (it.get("category"), it.get("detail_category"))
            if "taxes (unlabeled)" in cats:
                reasons.append("tax line with no tax type; could not be split from the report")
            if reasons:
                out.append(dict(filename=stem, entity_name=d.get("entity_name"), where=where,
                                fund_type=it.get("fund_type"), original_label=it.get("original_label"),
                                amount=it.get("amount"), reason="; ".join(dict.fromkeys(reasons)),
                                source_page=it.get("source_page")))
    for q in d.get("decision_needed") or []:
        q = q if isinstance(q, dict) else {"question": str(q)}
        out.append(dict(filename=stem, entity_name=d.get("entity_name"), where="decision_needed",
                        fund_type="", original_label=q.get("recorded_as", ""), amount=q.get("amount"),
                        reason=f"decision needed: {q.get('question', '')}", source_page=q.get("source_page")))
    return out


def main():
    rows, review, n, stems = [], [], 0, []
    for stem, d, err in load_reports():
        n += 1
        stems.append(stem)
        if err:
            rows.append(dict(filename=stem, check="schema", reason=err))
            continue
        fl, rvs = check(stem, d)
        rows.extend(fl)
        review.extend(review_items(stem, d))
        review.extend(rvs)
    write_csv(FLAGGED, rows, FLAG_FIELDS)
    write_csv(VALIDATED, [dict(filename=s, hash=json_hash(s)) for s in sorted(stems)], ["filename", "hash"])
    write_csv(REVIEW_CSV, review, REVIEW_FIELDS)
    print(f"validated {n} reports; {len({r['filename'] for r in rows})} flagged ({len(rows)} issues); "
          f"{len(review)} review items")


if __name__ == "__main__":
    main()
