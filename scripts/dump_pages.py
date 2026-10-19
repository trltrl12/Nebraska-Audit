"""Diagnostics for find_pages.py: write a per-page text summary of each PDF.

For every PDF given (default: everything in audits/), writes output/page_debug/<stem>.txt with,
per page: character count, image count, the current classification, and the first
LINES lines of text. Pages with almost no text are OCR'd (top half only) when Tesseract is
installed, so scanned reports can be inspected too.
"""
import argparse
import re
import sys
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import AUDITS, OUTPUT  # noqa: E402
import find_pages  # noqa: E402

OUT = OUTPUT / "page_debug"
LINES = 30


def ocr_top(page, frac=0.5, dpi=150):
    try:
        r = page.rect
        clip = pymupdf.Rect(r.x0, r.y0, r.x1, r.y0 + r.height * frac)
        pix = page.get_pixmap(dpi=dpi, clip=clip)
        doc = pymupdf.open("pdf", pix.pdfocr_tobytes())
        return doc[0].get_text("text")
    except Exception as e:  # noqa: BLE001
        return f"<OCR failed: {type(e).__name__}: {e}>"


def dump(path):
    doc = pymupdf.open(path)
    out = [f"# {Path(path).name}  pages={len(doc)}"]
    for i, page in enumerate(doc):
        text = page.get_text("text")
        chars = len(text.strip())
        imgs = len(page.get_images())
        src = "text"
        if chars < 50:
            text, src = ocr_top(page), "ocr-top-half"
        c = find_pages.classify(text) if src == "text" else None
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()][:LINES]
        out.append(f"\n=== page {i + 1} | chars={chars} images={imgs} src={src} | class={c[0] if c else '-'}")
        out += ["  " + re.sub(r"\s+", " ", ln)[:140] for ln in lines]
    doc.close()
    return "\n".join(out) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("files", nargs="*")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    files = [Path(f) for f in a.files] or sorted(AUDITS.glob("*.pdf"))
    for f in files:
        try:
            (OUT / f"{f.stem}.txt").write_text(dump(f), encoding="utf-8")
            print(f"dumped {f.name}")
        except Exception as e:  # noqa: BLE001
            print(f"failed {f.name}: {e}")


if __name__ == "__main__":
    main()
