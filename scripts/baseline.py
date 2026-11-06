#!/usr/bin/env python3
"""Row-level snapshot of every money value in output/json (all years), for before/after comparison.

Usage:
  python3 scripts/baseline.py snapshot <out.csv.gz>        # write a snapshot of the current JSON
  python3 scripts/baseline.py compare <before.csv.gz> [<after.csv.gz>] [--log output/json/_meta/corrections_log.csv]

Each money value gets one row: stem, container (receipts / detail / balance_sheet.assets / beginning_cash ...),
printed label (printed_label or original_label as stored), standardized label, amount, source_page, category,
detail_category, fund, fund_type, section, computed.

compare matches rows on (stem, container, printed label, amount, source_page, fund, section) as a multiset, so
added rows do not shift anything. It reports:
  - rows removed or added (an amount, printed label or page that changed shows up as one removed + one added);
  - category / detail_category / standardized-label changes on rows that are otherwise identical;
and marks each change as LOGGED when output/json/_meta/corrections_log.csv lists the stem (an authorised change),
otherwise UNEXPECTED. Exit code 1 when any removed/added row or printed-label change is UNEXPECTED.
"""
import csv
import gzip
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JSON_DIR = ROOT / "output" / "json"
FIELDS = ["stem", "container", "printed", "label", "amount", "source_page", "category", "detail_category",
          "fund", "fund_type", "section", "computed"]
MATCH = ["stem", "container", "printed", "amount", "source_page", "fund", "section"]


def num(a):
    try:
        return f"{float(a):.2f}"
    except (TypeError, ValueError):
        return "" if a is None else str(a)


def rows_of(stem, d):
    out = []

    def item(container, x):
        a = x.get("amount")
        page = x.get("source_page")
        if isinstance(a, dict):
            page = a.get("source_page", page)
            a = a.get("amount")
        out.append(dict(stem=stem, container=container,
                        printed=(x.get("printed_label") or x.get("original_label") or ""),
                        label=x.get("original_label") or "", amount=num(a), source_page=str(page or ""),
                        category=x.get("category") or "", detail_category=x.get("detail_category") or "",
                        fund=x.get("fund") or "", fund_type=x.get("fund_type") or "",
                        section=x.get("section") or "", computed=x.get("computed") or ""))

    def value(container, v):
        if isinstance(v, dict) and "amount" in v and not isinstance(v.get("amount"), dict):
            out.append(dict(stem=stem, container=container, printed="", label="", amount=num(v.get("amount")),
                            source_page=str(v.get("source_page") or ""), category="", detail_category="",
                            fund="", fund_type="", section="", computed=v.get("computed") or ""))
        elif isinstance(v, dict):
            for k, w in v.items():
                value(f"{container}.{k}", w)
        elif isinstance(v, (int, float)) and not isinstance(v, bool):
            out.append(dict(stem=stem, container=container, printed="", label="", amount=num(v), source_page="",
                            category="", detail_category="", fund="", fund_type="", section="", computed=""))

    for key, v in d.items():
        if key in ("receipts", "disbursements", "detail", "adjustments", "detail_totals"):
            for x in v or []:
                if isinstance(x, dict):
                    item(key, x)
        elif key == "balance_sheet" and isinstance(v, dict):
            for k, w in v.items():
                if isinstance(w, list):
                    for x in w:
                        if isinstance(x, dict):
                            item(f"balance_sheet.{k}", x)
                elif isinstance(w, dict):
                    value(f"balance_sheet.{k}", w)
        elif key in ("business_type", "proprietary", "reported_totals", "governmental_net_position"):
            if isinstance(v, dict):
                for k, w in v.items():
                    if isinstance(w, list):
                        for x in w:
                            if isinstance(x, dict):
                                item(f"{key}.{k}", x)
                    else:
                        value(f"{key}.{k}", w)
        elif isinstance(v, dict) and "amount" in v:
            value(key, v)
    return out


def snapshot(path):
    n = 0
    with gzip.open(path, "wt", newline="") as fh:
        w = csv.DictWriter(fh, FIELDS)
        w.writeheader()
        for f in sorted(JSON_DIR.glob("*.json")):
            try:
                d = json.loads(f.read_text())
            except Exception:
                continue
            if not isinstance(d, dict):
                continue
            for r in rows_of(f.stem, d):
                w.writerow(r)
                n += 1
    print(f"snapshot: {n} money values -> {path}")


def load(path):
    with gzip.open(path, "rt", newline="") as fh:
        return list(csv.DictReader(fh))


def current_rows():
    rows = []
    for f in sorted(JSON_DIR.glob("*.json")):
        try:
            d = json.loads(f.read_text())
        except Exception:
            continue
        if isinstance(d, dict):
            rows += rows_of(f.stem, d)
    return rows


def compare(before, after, log_path):
    logged = set()
    if log_path and Path(log_path).exists():
        with open(log_path, newline="") as fh:
            logged = {r["stem"] for r in csv.DictReader(fh)}
    key = lambda r: tuple(r[k] for k in MATCH)
    b = defaultdict(list)
    a = defaultdict(list)
    for r in before:
        b[key(r)].append(r)
    for r in after:
        a[key(r)].append(r)
    removed, added, catchg, labchg = [], [], [], []
    for k in set(b) | set(a):
        x, y = b.get(k, []), a.get(k, [])
        for i in range(max(len(x), len(y))):
            if i >= len(y):
                removed.append(x[i])
            elif i >= len(x):
                added.append(y[i])
            else:
                if (x[i]["category"], x[i]["detail_category"]) != (y[i]["category"], y[i]["detail_category"]):
                    catchg.append((x[i], y[i]))
                if x[i]["label"] != y[i]["label"]:
                    labchg.append((x[i], y[i]))
    unexpected = 0
    print(f"rows before {len(before)}, after {len(after)}")
    for name, lst in (("REMOVED", removed), ("ADDED", added)):
        c = Counter(("LOGGED" if r["stem"] in logged else "UNEXPECTED") for r in lst)
        unexpected += c["UNEXPECTED"]
        print(f"{name}: {len(lst)} ({dict(c)})")
        for r in [r for r in lst if r["stem"] not in logged][:40]:
            print(f"   UNEXPECTED {name} {r['stem']} {r['container']} '{r['printed']}' {r['amount']} p{r['source_page']}")
    c = Counter(("LOGGED" if x["stem"] in logged else "rule") for x, _ in catchg)
    print(f"CATEGORY/DETAIL CHANGES: {len(catchg)} ({dict(c)})")
    trans = Counter((x["category"], y["category"]) for x, y in catchg)
    for (o, n), k in trans.most_common(60):
        print(f"   {k:6d}  {o or '(blank)'} -> {n or '(blank)'}")
    print(f"STANDARDIZED-LABEL CHANGES: {len(labchg)}")
    for (o, n), k in Counter((x["label"], y["label"]) for x, y in labchg).most_common(30):
        print(f"   {k:6d}  {o} -> {n}")
    return 1 if unexpected else 0


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        sys.exit(__doc__)
    if args[0] == "snapshot":
        snapshot(args[1])
    elif args[0] == "compare":
        log = None
        if "--log" in args:
            i = args.index("--log")
            log = args[i + 1]
            args = args[:i] + args[i + 2:]
        before = load(args[1])
        after = load(args[2]) if len(args) > 2 else current_rows()
        sys.exit(compare(before, after, log or str(JSON_DIR / "_meta" / "corrections_log.csv")))
    else:
        sys.exit(__doc__)
