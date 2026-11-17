"""Apply the schedule fixes written by the schedule_fixer agent (output/json/_meta/schedule_fix/<stem>.json).

A report's fix is applied only when, after its added / removed detail lines, every gap the agent calls fixed really
ties (schedule total = statement total within 3). Otherwise nothing is changed for that report and it is listed in
output/json/_meta/schedule_fix_status.csv. Gaps the agent explains (different basis, statement issue, page not in trim)
are recorded there too. Usage: python3 scripts/schedule_fix_apply.py [--write]
"""
import csv, json, re, sys
from collections import Counter
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from schedule_gap import gaps, v, J  # noqa: E402

FIX = J / "_meta" / "schedule_fix"
# removals of more than ten lines are applied only after an independent page check confirmed every removed line
CHECKED_REMOVALS = {"Potter_FY2022", "Potter_FY2023", "Potter_FY2025", "Hubbard_FY2022"}
FIXED = {"missing fund column", "missing lines", "duplicate lines"}
SECTIONS = {"revenue", "expenditure", "other financing"}


def fund_name(name, existing):
    """Fund name on the schedule = the statement column's label (user 2026-11-17): "Other Governmental Funds" only
    when that is all the heading says; "Other Governmental Fund (Cemetery Trust)" -> "Cemetery Trust Fund"; a fund
    already on the schedule keeps its existing spelling; names end in "Fund" unless they already contain it."""
    n = re.sub(r"\s+", " ", str(name or "")).strip()
    m = re.match(r"^(?:other|non-?major)(?: governmental)?(?: funds?)?\s*[-:]?\s*\((.+)\)$", n, re.I)
    if m:
        n = m.group(1).strip()
    if "fund" not in n.lower():
        n += " Fund"
    key = re.sub(r"[^a-z0-9]+", "", re.sub(r"\bfunds?\b", "", n.lower()))
    for e in existing:
        if e and re.sub(r"[^a-z0-9]+", "", re.sub(r"\bfunds?\b", "", e.lower())) == key:
            return e
    return n


def norm(s):
    return re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).strip()


def main():
    write = "--write" in sys.argv
    hold_f = J / "_meta" / "schedule_fix_hold.csv"   # stem,reason: fixes the page check did not confirm
    hold = {r["stem"]: r["reason"] for r in csv.DictReader(hold_f.open())} if hold_f.exists() else {}
    status, stat, changed = [], Counter(), []
    for f in sorted(FIX.glob("*.json")):
        stem = f.stem
        p = J / (stem + ".json")
        try:
            fx = json.loads(f.read_text())
            d = json.loads(p.read_text())
        except Exception as e:
            status.append((stem, "", "", "unreadable fix file", str(e)[:200], 0, 0)); stat["unreadable"] += 1
            continue
        before = {(g[0], g[1]): g for g in gaps(d)}
        if any(isinstance(x, dict) and x.get("schedule_fix") for x in d.get("detail") or []):
            for k, g in before.items():
                status.append((stem, k[0], k[1], "already applied; still open", "", g[4], 0))
            stat["already applied"] += 1
            continue
        st_cat = {}
        for key in ("receipts", "disbursements"):
            for x in d.get(key) or []:
                if isinstance(x, dict):
                    for lab in (x.get("original_label"), x.get("printed_label")):
                        st_cat.setdefault((key, x.get("fund_type"), norm(lab)), (x.get("category"), x.get("detail_category")))
        det = [x for x in d.get("detail") or []]
        rm, add, bad = set(), [], []
        existing_funds = sorted({x.get("fund") for x in det if isinstance(x, dict) and x.get("fund")})
        entries = [e for e in fx.get("gaps") or [] if isinstance(e, dict)]
        for e in entries:
            for r in e.get("remove") or []:
                i = r.get("detail_index")
                if not isinstance(i, int) or not (0 <= i < len(det)) or abs(v(det[i]) - (r.get("amount") or 0)) > 0.5:
                    bad.append("remove index does not match a detail line: %r" % (r,))
                else:
                    rm.add(i)
            for a in e.get("add") or []:
                if a.get("section") not in SECTIONS or not isinstance(a.get("amount"), (int, float)) or not a.get("fund") \
                        or not a.get("original_label") or a.get("fund_type") not in ("governmental", "business-type"):
                    bad.append("added line incomplete: %r" % (a,)); continue
                if a["amount"] == 0:
                    continue
                key = "receipts" if a["section"] == "revenue" else "disbursements"
                cat, dcat = st_cat.get((key, a["fund_type"], norm(a["original_label"])), (None, None))
                if a["section"] == "other financing":
                    cat = dcat = None
                else:
                    cat = cat or a.get("category")
                    dcat = a.get("detail_category") or dcat or ("not itemized" if a["section"] == "expenditure" else cat)
                    if not cat:
                        bad.append("added line has no category: %r" % (a,)); continue
                dup = [x for x in det if isinstance(x, dict) and x.get("section") == a["section"] and abs(v(x) - a["amount"]) < 0.005
                       and norm(x.get("fund")) == norm(a["fund"]) and norm(x.get("original_label")) == norm(a["original_label"])]
                if dup:
                    bad.append("added line is already in detail: %r" % (a,)); continue
                add.append({"schedule": a.get("schedule") or "", "fund_type": a["fund_type"],
                            "fund": fund_name(a["fund"], existing_funds), "printed_fund": a["fund"],
                            "department": a.get("department") or None, "section": a["section"], "category": cat,
                            "detail_category": dcat, "original_label": a["original_label"], "amount": a["amount"],
                            "budget_final": None, "source_page": a.get("source_page"),
                            "computed": "schedule fix 2026-11-17: line read from the page so the schedule adds up to the statement",
                            "schedule_fix": "2026-11-17"})
        new = dict(d)
        new["detail"] = [x for i, x in enumerate(det) if i not in rm] + add
        after = {(g[0], g[1]): g for g in gaps(new)}
        ok = not bad
        for e in entries:
            k = (e.get("fund_type"), e.get("section"))
            cause = str(e.get("cause") or "").strip().lower()
            if e.get("add") or e.get("remove"):
                partial = any(c in cause for c in ("different basis", "statement issue", "partly", "explained"))
                if k in after and not (k[1].startswith("transfers") and after[k][4] < 0):
                    if not (partial and k in before and abs(after[k][4]) < abs(before[k][4]) - 3):
                        ok = False; bad.append("%s %s still off by %s after the fix" % (k[0], k[1], after[k][4]))
        # a fix must not open or worsen another gap
        for k, g in after.items():
            if k[1].startswith("transfers") and g[4] < 0:
                continue
            if k not in before or abs(g[4]) > abs(before[k][4]) + 3:
                ok = False; bad.append("%s %s gap opened/worsened: %s" % (k[0], k[1], g[4]))
        if stem in hold:
            ok = False; bad.append("held: " + hold[stem])
        elif len(rm) > 10 and stem not in CHECKED_REMOVALS:
            ok = False; bad.append("held: removes %d lines; needs a full page check first" % len(rm))
        applied = ok and (add or rm)
        for e in entries:
            k = (e.get("fund_type"), e.get("section"))
            cause = str(e.get("cause") or "")
            gap_b = before.get(k, (0, 0, 0, 0, 0))[4]
            gap_a = (after if applied else before).get(k, (0, 0, 0, 0, 0))[4]
            res = ("fixed" if applied and k not in after else "explained" if not (e.get("add") or e.get("remove")) else
                   "fix rejected" if not ok else "partly fixed")
            stat[res] += 1
            status.append((stem, k[0], k[1], res + ": " + cause, (e.get("explanation") or "")[:700] + (" | REJECTED: " + "; ".join(bad)[:400] if res == "fix rejected" else ""), gap_b, gap_a))
        for k, g in before.items():
            if k not in {(e.get("fund_type"), e.get("section")) for e in entries} and not (k[1].startswith("transfers") and g[4] < 0):
                stat["gap not addressed by the agent"] += 1
                status.append((stem, k[0], k[1], "not addressed", "", g[4], (after if applied else before).get(k, (0, 0, 0, 0, 0))[4]))
        if applied:
            stat["reports changed"] += 1; stat["lines added"] += len(add); stat["lines removed"] += len(rm)
            changed.append(stem)
            if write:
                p.write_text(json.dumps(new, indent=2, ensure_ascii=False))
    with (J / "_meta" / "schedule_fix_status.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["stem", "fund_type", "section", "result", "explanation", "gap_before", "gap_after"])
        w.writerows(status)
    print(dict(stat))
    (J / "_meta" / "schedule_fix_changed.txt").write_text("\n".join(changed) + "\n")
    print(len(changed), "reports", "changed" if write else "would change")


if __name__ == "__main__":
    main()
