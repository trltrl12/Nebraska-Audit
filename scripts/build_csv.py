"""Build output/cashflow_long.csv, output/cashflow_summary.csv, output/progress.csv
and the detailed workbook output/cashflow.xlsx.

source_page in the JSON is the page number inside the trimmed PDF; the *_original
columns map it back to the page number of the full audit via output/manifest.csv.
"""
import sys
from collections import OrderedDict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (DOWNLOAD_ERRORS, FILE_LIST, FLAGGED, LONG_CSV, MANIFEST,  # noqa: E402
                    PROGRESS_CSV, PROPRIETARY_FIELDS, SUMMARY_CSV, amt, load_reports, page,
                    read_csv, reconcile, reconcile_proprietary, write_csv)

LONG_FIELDS = ["entity_name", "entity_type", "filename", "fiscal_year_end", "basis", "section",
               "category", "amount", "original_labels", "source_pages", "source_pages_original"]
SUMMARY_FIELDS = ["entity_name", "entity_type", "filename", "fiscal_year_end", "basis", "balance_type",
                  "beginning_balance", "total_receipts", "total_disbursements", "transfers_in",
                  "transfers_out", "debt_proceeds", "other_financing", "adjustments", "ending_balance",
                  "computed_ending", "difference", "reconciles", "beginning_cash", "ending_cash",
                  "beginning_investments", "ending_investments",
                  "prop_operating", "prop_noncapital_financing", "prop_capital_and_related_financing",
                  "prop_investing", "prop_reconciles", "flagged"]
PROGRESS_FIELDS = ["filename", "entity_name", "entity_type", "county", "subdivision_type", "filing_type", "status", "basis_guess", "trimmed_file",
                   "pages_kept", "extraction_attempt", "flag_reasons"]


def orig_pages(pages, mapping):
    out = []
    for p in pages:
        try:
            out.append(mapping[int(p) - 1])
        except (ValueError, IndexError, TypeError):
            out.append("?")
    return ";".join(out)


def main():
    manifest = {Path(r["filename"]).stem: r for r in read_csv(MANIFEST)}
    flagged = {}
    for r in read_csv(FLAGGED):
        flagged.setdefault(r["filename"], []).append(f"{r['check']}: {r['reason']}")

    long_rows, summary_rows, extracted = [], [], {}
    for stem, d, err in load_reports():
        extracted[stem] = d
        if err or not d:
            continue
        m = manifest.get(stem, {})
        mapping = (m.get("pages_kept") or "").split(";") if m.get("pages_kept") else []
        base = dict(entity_name=d.get("entity_name"), entity_type=d.get("entity_type"), filename=stem,
                    fiscal_year_end=d.get("fiscal_year_end"), basis=d.get("basis"))

        for section in ("receipts", "disbursements"):
            agg = OrderedDict()
            for it in d.get(section) or []:
                a = agg.setdefault(it.get("category"), dict(amount=0.0, labels=[], pages=[]))
                a["amount"] += float(it.get("amount") or 0)
                a["labels"].append(str(it.get("original_label", "")))
                if it.get("source_page") not in (None, "") and str(it["source_page"]) not in a["pages"]:
                    a["pages"].append(str(it["source_page"]))
            for cat, a in agg.items():
                long_rows.append(dict(base, section=section, category=cat, amount=round(a["amount"], 2),
                                      original_labels="; ".join(a["labels"]), source_pages=";".join(a["pages"]),
                                      source_pages_original=orig_pages(a["pages"], mapping)))
        scalars = [("balance", k) for k in ("beginning_cash", "beginning_investments", "ending_cash",
                                            "ending_investments", "beginning_fund_balance", "ending_fund_balance")]
        scalars += [("financing", k) for k in ("transfers_in", "transfers_out", "debt_proceeds", "other_financing")]
        for section, k in scalars:
            v = d.get(k)
            if amt(v) is None:
                continue
            p = [str(page(v))] if page(v) is not None else []
            long_rows.append(dict(base, section=section, category=k, amount=amt(v), original_labels="",
                                  source_pages=";".join(p), source_pages_original=orig_pages(p, mapping)))
        for it in d.get("adjustments") or []:
            p = [str(it.get("source_page"))] if it.get("source_page") is not None else []
            long_rows.append(dict(base, section="adjustment", category="adjustment", amount=it.get("amount"),
                                  original_labels=it.get("original_label", ""), source_pages=";".join(p),
                                  source_pages_original=orig_pages(p, mapping)))
        prop = d.get("proprietary") or {}
        for k in PROPRIETARY_FIELDS + ["beginning_cash", "ending_cash"]:
            if amt(prop.get(k)) is None:
                continue
            p = [str(page(prop[k]))] if page(prop[k]) is not None else []
            long_rows.append(dict(base, section="proprietary_cash_flows", category=k, amount=amt(prop[k]),
                                  original_labels="", source_pages=";".join(p),
                                  source_pages_original=orig_pages(p, mapping)))

        r = reconcile(d)
        pr = reconcile_proprietary(d)
        summary_rows.append(dict(
            base, balance_type=r["balance_type"], beginning_balance=r["beginning"],
            total_receipts=round(r["receipts"], 2), total_disbursements=round(r["disbursements"], 2),
            transfers_in=amt(d.get("transfers_in")), transfers_out=amt(d.get("transfers_out")),
            debt_proceeds=amt(d.get("debt_proceeds")), other_financing=amt(d.get("other_financing")),
            adjustments=round(sum(float(i.get("amount") or 0) for i in d.get("adjustments") or []), 2),
            ending_balance=r["ending"], computed_ending=r["computed_ending"], difference=r["difference"],
            reconciles={True: "yes", False: "no", None: ""}[r["reconciles"]],
            beginning_cash=amt(d.get("beginning_cash")), ending_cash=amt(d.get("ending_cash")),
            beginning_investments=amt(d.get("beginning_investments")),
            ending_investments=amt(d.get("ending_investments")),
            **{f"prop_{k}": amt(prop.get(k)) for k in PROPRIETARY_FIELDS},
            prop_reconciles="" if not pr else ("yes" if pr["reconciles"] else "no"),
            flagged="yes" if stem in flagged else "no"))

    # Progress: every known report
    progress = []
    seen = set()
    for stem, m in manifest.items():
        seen.add(m["filename"])
        d = extracted.get(stem)
        if not m.get("trimmed_file"):
            status = "no_statements_found"
        elif stem in flagged:
            status = "flagged"
        elif stem in extracted:
            status = "done"
        else:
            status = "pending"
        progress.append(dict(filename=m["filename"], entity_name=m.get("entity_name"),
                             entity_type=m.get("entity_type"), county=m.get("county"),
                             subdivision_type=m.get("subdivision_type"), filing_type=m.get("filing_type"),
                             status=status, basis_guess=m.get("basis_guess"),
                             trimmed_file=m.get("trimmed_file"), pages_kept=m.get("pages_kept"),
                             extraction_attempt=(d or {}).get("extraction_attempt", "") if d else "",
                             flag_reasons=" | ".join(flagged.get(stem, [])) or m.get("flag", "")))
    failed = {r["filename"] for r in read_csv(DOWNLOAD_ERRORS)}
    for r in read_csv(FILE_LIST):
        if r["filename"] not in seen:
            progress.append(dict(filename=r["filename"], county=r.get("county"),
                                 subdivision_type=r.get("subdivision_type"), filing_type=r.get("filing_type"),
                                 entity_name=r.get("entity_name") or Path(r["filename"]).stem.replace("_", " "),
                                 status="download_failed" if r["filename"] in failed else "not_fetched"))
    progress.sort(key=lambda r: r["filename"].lower())

    write_csv(LONG_CSV, long_rows, LONG_FIELDS)
    write_csv(SUMMARY_CSV, summary_rows, SUMMARY_FIELDS)
    write_csv(PROGRESS_CSV, progress, PROGRESS_FIELDS)
    counts = {}
    for r in progress:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    print("progress:", ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))

    import build_xlsx  # detailed Excel workbook from the same data
    build_xlsx.main()


if __name__ == "__main__":
    main()
