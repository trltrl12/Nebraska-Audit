"""Schedule (detail) totals vs statement totals, per report, fund type and section (user 2026-11-17: the schedule rows
must add up to the statement; a fund column printed only on the statement belongs on the schedule under the fund name
the statement prints, e.g. "Other Governmental Funds"). Writes output/json/_meta/schedule_gap.csv."""
import csv, json, re, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
J = ROOT / "output" / "json"


def v(x):
    a = x.get("amount")
    a = a.get("amount") if isinstance(a, dict) else a
    return a if isinstance(a, (int, float)) else 0


def gaps(d):
    out = []
    det = [x for x in d.get("detail") or [] if isinstance(x, dict)]
    for ft in ("governmental", "business-type"):
        dl = [x for x in det if (x.get("fund_type") or "governmental") == ft]
        if not dl:
            continue
        for sec, key in (("revenue", "receipts"), ("expenditure", "disbursements")):
            st = sum(v(x) for x in d.get(key) or [] if isinstance(x, dict) and x.get("fund_type") == ft)
            sc = sum(v(x) for x in dl if x.get("section") == sec)
            if st and abs(st - sc) > 3:
                out.append((ft, sec, round(st, 2), round(sc, 2), round(st - sc, 2)))
        if ft == "governmental":
            for name, sign in (("transfers_in", 1), ("transfers_out", -1)):
                t = d.get(name)
                t = t.get("amount") if isinstance(t, dict) else t
                if not isinstance(t, (int, float)) or not t:
                    continue
                sc = sum(v(x) for x in dl if x.get("section") == "other financing" and v(x) * sign > 0
                         and re.search(r"transfer", str(x.get("original_label") or ""), re.I))
                if abs(abs(t) - abs(sc)) > 3:
                    out.append((ft, name, round(abs(t), 2), round(abs(sc), 2), round(abs(t) - abs(sc), 2)))
    return out


def main():
    rows = []
    for f in sorted(J.glob("*.json")):
        try:
            d = json.loads(f.read_text())
        except Exception:
            continue
        if isinstance(d, dict):
            rows += [(f.stem,) + g for g in gaps(d)]
    with (J / "_meta" / "schedule_gap.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["stem", "fund_type", "section", "statement_total", "schedule_total", "gap"])
        w.writerows(rows)
    print(len(rows), "gaps in", len({r[0] for r in rows}), "reports")


if __name__ == "__main__":
    main()
