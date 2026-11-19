#!/usr/bin/env python3
"""Cross-year category check (user 2026-11-17).

Lists every line where the same entity prints the same label (same statement section, fund and department) in two or
more fiscal years but the category differs between years. Writes output/json/_meta/xyear_check.csv, one row per line and
year. Lines already settled by a page check (output/json/_meta/xyear_decisions/<entity>.csv, verdict `differs` or
`unclear`) are not listed again. Nothing is changed here: a mismatch is a reason to read the pages, never a reason to
copy a category from another year.

    python3 scripts/xyear_check.py            # rebuild the queue, print counts
"""
import csv
import json
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JSON_DIR = ROOT / "output" / "json"
META = JSON_DIR / "_meta"
QUEUE = META / "xyear_check.csv"
DECISIONS = META / "xyear_decisions"
FIELDS = ["entity", "stem", "year", "where", "fund", "department", "label", "printed_label", "amount", "source_page",
          "current_category", "other_years"]


def norm(s):
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def amt(x):
    a = x.get("amount")
    return (a.get("amount"), a.get("source_page")) if isinstance(a, dict) else (a, x.get("source_page"))


def lines(d):
    for w in ("receipts", "disbursements"):
        for x in d.get(w) or []:
            if isinstance(x, dict):
                yield w, x.get("fund_type") or "", "", x
    for x in d.get("detail") or []:
        if isinstance(x, dict) and x.get("section") in ("revenue", "expenditure"):
            yield "detail " + x["section"], x.get("fund") or "", x.get("department") or "", x
    bs = d.get("balance_sheet") or {}
    for w in ("assets", "liabilities", "deferred_outflows", "deferred_inflows"):
        for x in bs.get(w) or []:
            if isinstance(x, dict):
                yield "balance sheet " + w, x.get("fund_type") or "", "", x


def settled():
    done = set()
    if DECISIONS.exists():
        for f in DECISIONS.glob("*.csv"):
            for r in csv.DictReader(f.open()):
                if r.get("verdict") in ("differs", "unclear"):
                    done.add((r["stem"], r["where"], r["label"], str(r["amount"]), str(r["source_page"])))
    return done


def build():
    groups = defaultdict(lambda: defaultdict(list))
    files = []
    names = {}  # entity_name -> entity key of its <entity>_FY<year> files
    for f in sorted(JSON_DIR.glob("*.json")):
        try:
            d = json.loads(f.read_text())
        except Exception:
            continue
        if not isinstance(d, dict):
            continue
        m = re.match(r"(.*)_FY(20\d\d)$", f.stem)
        if m:
            names.setdefault(norm(d.get("entity_name")), m.group(1))
        files.append((f, d, m))
    for f, d, m in files:
        if not m:
            # State Auditor audits keep their CO..-/CV..- filenames: entity and year come from the report itself
            if not re.match(r"C[OV]\d", f.stem):
                continue
            y = re.match(r"(20\d\d)", str(d.get("fiscal_year_end") or ""))
            n = str(d.get("entity_name") or "")
            if not y or not n:
                continue
            key = names.get(norm(n)) or re.sub(r"[^A-Za-z0-9]+", "_", re.sub(r"^(City|Village) of ", "", n)).strip("_")
            m = re.match(r"(.*)_FY(20\d\d)$", f"{key}_FY{y.group(1)}")
        for where, fund, dept, x in lines(d):
            L = x.get("original_label")
            if norm(L):
                groups[(m.group(1), where, norm(fund), norm(dept), norm(L))][m.group(2)].append((f.stem, fund, dept, x))
    done = settled()
    rows = []
    for (ent, where, _, _, _), ys in groups.items():
        cats = {y: frozenset(str(x.get("category")) for _, _, _, x in v) for y, v in ys.items()}
        if len(ys) < 2 or len(set(cats.values())) < 2:
            continue
        for y, v in sorted(ys.items()):
            other = "; ".join(f"FY{o}: {' / '.join(sorted(c))}" for o, c in sorted(cats.items()) if o != y)
            for stem, fund, dept, x in v:
                a, p = amt(x)
                if (stem, where, x.get("original_label"), str(a), str(p)) in done:
                    continue
                rows.append({"entity": ent, "stem": stem, "year": y, "where": where, "fund": fund, "department": dept,
                             "label": x.get("original_label"), "printed_label": x.get("printed_label") or "",
                             "amount": a, "source_page": p, "current_category": x.get("category"), "other_years": other})
    rows.sort(key=lambda r: (r["entity"], r["where"], norm(r["label"]), r["fund"], r["department"], r["year"]))
    return rows


def main():
    rows = build()
    META.mkdir(parents=True, exist_ok=True)
    with QUEUE.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    print(f"{len(rows)} lines in {len({r['entity'] for r in rows})} entities differ between years ({QUEUE.name})")


if __name__ == "__main__":
    main()
