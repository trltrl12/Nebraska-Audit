#!/usr/bin/env python3
"""Compare two one-tab workbooks (cashflow_one_tab_<year>.xlsx) row by row.

Usage: python3 scripts/compare_workbooks.py <before.xlsx> <after.xlsx> [--show N]

Rows are matched as a multiset on (Filename, Level, Fund Type, Section, Source, Schedule, Fund, printed label,
Amount, Pages); the printed label is the "Printed Label" column when present, else "Original Label". Reports
rows only in one workbook, and for matched rows any change of Category / Detail Category / labels. Also compares
per-report row counts and per-report amount totals by Level and Section.
"""
import sys
from collections import Counter, defaultdict

import openpyxl


def load(path):
    ws = openpyxl.load_workbook(path, read_only=True).worksheets[0]
    it = ws.iter_rows(values_only=True)
    head = [str(h) if h is not None else "" for h in next(it)]
    return [dict(zip(head, r)) for r in it]


def printed(r):
    return r.get("Printed Label") or r.get("Original Label") or ""


def amt(r):
    try:
        return f"{float(r.get('Amount') or 0):.2f}"
    except (TypeError, ValueError):
        return str(r.get("Amount"))


KEY = ("Filename", "Level", "Fund Type", "Section", "Source", "Schedule", "Fund")


def key(r):
    return tuple(str(r.get(k) or "") for k in KEY) + (printed(r), amt(r), str(r.get("Pages") or ""))


def main():
    args = sys.argv[1:]
    show = 25
    if "--show" in args:
        i = args.index("--show")
        show = int(args[i + 1])
        args = args[:i] + args[i + 2:]
    a, b = load(args[0]), load(args[1])
    A, B = defaultdict(list), defaultdict(list)
    for r in a:
        A[key(r)].append(r)
    for r in b:
        B[key(r)].append(r)
    only_a, only_b, changed = [], [], []
    for k in set(A) | set(B):
        x, y = A.get(k, []), B.get(k, [])
        for i in range(max(len(x), len(y))):
            if i >= len(y):
                only_a.append(x[i])
            elif i >= len(x):
                only_b.append(y[i])
            elif (x[i].get("Category"), x[i].get("Detail Category"), x[i].get("Original Label"),
                  x[i].get("Standardized Label")) != (y[i].get("Category"), y[i].get("Detail Category"),
                                                       y[i].get("Original Label"), y[i].get("Standardized Label")):
                changed.append((x[i], y[i]))
    print(f"rows: before {len(a)}, after {len(b)}; only before {len(only_a)}, only after {len(only_b)}, "
          f"changed {len(changed)}")
    for name, lst in (("ONLY BEFORE", only_a), ("ONLY AFTER", only_b)):
        print(f"{name}: by report {Counter(r['Filename'] for r in lst).most_common(15)}")
        for r in lst[:show]:
            print(f"   {r['Filename']} | {r.get('Level')} | {r.get('Section')} | {r.get('Fund')} | {printed(r)} | "
                  f"{r.get('Amount')} | {r.get('Category')}")
    print("CATEGORY CHANGES:")
    for (o, n), k in Counter((x.get("Category"), y.get("Category")) for x, y in changed).most_common(40):
        print(f"   {k:6d}  {o} -> {n}")
    tot = lambda rows: Counter({(r["Filename"], r.get("Level"), r.get("Section")): 0 for r in rows})
    sa, sb = defaultdict(float), defaultdict(float)
    for r in a:
        sa[(r["Filename"], r.get("Level"), r.get("Section"))] += float(r.get("Amount") or 0)
    for r in b:
        sb[(r["Filename"], r.get("Level"), r.get("Section"))] += float(r.get("Amount") or 0)
    diff = [(k, sa.get(k, 0), sb.get(k, 0)) for k in set(sa) | set(sb) if abs(sa.get(k, 0) - sb.get(k, 0)) > 0.005]
    print(f"SECTION TOTALS THAT CHANGED: {len(diff)}")
    for k, x, y in sorted(diff)[:show]:
        print(f"   {k}: {x:,.2f} -> {y:,.2f}")


if __name__ == "__main__":
    main()
