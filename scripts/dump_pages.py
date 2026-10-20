"""Diagnostics for find_pages.py: show what it sees and selects for each PDF.

For every PDF given (default: everything in audits/), writes output/page_debug/<stem>.txt with,
per page: character count, image count, text source (text layer or OCR), the classification,
and the header lines find_pages reads (top of the page, sorted by position). Also writes
output/page_debug/_summary.csv comparing the current selection with output/manifest.csv.
Set DUMP_OCR=0 to skip OCR of scanned pages (much faster).
"""
import argparse
import os
import sys
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import AUDITS, MANIFEST, OUTPUT, read_csv, write_csv  # noqa: E402
import find_pages as fp  # noqa: E402

OUT = OUTPUT / "page_debug"
LINES = 15


def dump(path):
    doc = pymupdf.open(path)
    if os.environ.get("DUMP_OCR", "1") == "0":
        fp._ocr_ok = False
    pages = [fp.page_header(p) for p in doc]
    out = [f"# {Path(path).name}  pages={len(doc)}"]
    for i, (page, (lines, full, src)) in enumerate(zip(doc, pages)):
        c = fp.classify_lines(lines, full)
        out.append(f"\n=== page {i + 1} | chars={len(page.get_text('text').strip())} "
                   f"images={len(page.get_images())} src={src} | class={c or '-'}")
        out += ["  " + ln[:140] for ln in lines[:LINES]]
    keep, types, basis, flag = fp.select_pages(pages, Path(path).name)
    doc.close()
    return "\n".join(out) + "\n", dict(pages_kept=";".join(str(k + 1) for k in keep),
                                       page_types=";".join(types), basis_guess=basis, flag=flag)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("files", nargs="*")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    old = {r["filename"]: r for r in read_csv(MANIFEST)}
    files = [Path(f) for f in a.files] or sorted(AUDITS.glob("*.pdf"))
    rows = []
    for f in files:
        try:
            text, sel = dump(f)
            (OUT / f"{f.stem}.txt").write_text(text, encoding="utf-8")
            o = old.get(f.name, {})
            rows.append(dict(filename=f.name, filing_type=o.get("filing_type", ""),
                             old_pages=o.get("pages_kept", ""), old_types=o.get("page_types", ""),
                             old_flag=o.get("flag", ""), new_pages=sel["pages_kept"],
                             new_types=sel["page_types"], new_basis=sel["basis_guess"], new_flag=sel["flag"]))
            print(f"dumped {f.name}: {sel['pages_kept'] or '-'} {sel['flag']}")
        except Exception as e:  # noqa: BLE001
            print(f"failed {f.name}: {e}")
    write_csv(OUT / "_summary.csv", rows, ["filename", "filing_type", "old_pages", "old_types", "old_flag",
                                           "new_pages", "new_types", "new_basis", "new_flag"])


if __name__ == "__main__":
    main()
