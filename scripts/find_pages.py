"""Find the financial-statement pages in each downloaded audit and save them as a small PDF.

For every PDF in audits/ not yet in output/manifest.csv:
  * cash-basis audits: "Statement of Receipts, Disbursements, and Changes in Cash Balances"
    (and close variants);
  * GAAP audits: governmental funds "Statement of Revenues, Expenditures, and Changes in
    Fund Balances", "Statement of Cash Flows - Proprietary Funds", governmental funds
    "Balance Sheet";
  * plus continuation pages.
Matching pages go to trimmed/<filename> and a row is appended to output/manifest.csv.
Full PDFs are deleted after processing unless --keep is given.
"""
import argparse
import datetime as dt
import re
import sys
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (AUDITS, FILE_LIST, MANIFEST, MANIFEST_FIELDS, TRIMMED,  # noqa: E402
                    append_csv, read_csv)

HEADER_LINES = 14          # a statement title must appear in the first N text lines of a page
MAX_CONTINUATIONS = 8
SCANNED_CHARS_PER_PAGE = 100

# (statement type, basis, title regex, regex that must also appear in the page header, exclusion regex)
PATTERNS = [
    ("cash_receipts_disbursements", "cash",
     r"statement\s+of\s+(cash\s+)?receipts,?\s+(disbursements|expenditures)(,?\s+and\s+changes\s+in\s+"
     r"(fund\s+)?(cash|cash\s+and\s+investment)s?\s+balances?)?", None, None),
    ("cash_receipts_disbursements", "cash",
     r"statement\s+of\s+(cash\s+)?receipts\s+and\s+(disbursements|expenditures)", None, None),
    ("cash_receipts_disbursements", "cash",
     r"statement\s+of\s+(changes\s+in\s+)?cash\s+(receipts|balances?)", None, None),
    ("cash_receipts_disbursements", "cash",
     r"statement\s+of\s+(cash\s+)?receipts,?\s+disbursements", None, None),
    ("gov_revenues_expenditures", "GAAP",
     r"statement\s+of\s+revenues,?\s+expenditures,?\s+and\s+changes\s+in\s+fund\s+balances?",
     r"governmental\s+funds?", r"budget|reconciliation|\bactual\b"),
    ("proprietary_cash_flows", "GAAP",
     r"statement\s+of\s+cash\s+flows", r"proprietary|enterprise|business.type", r"fiduciary"),
    ("gov_balance_sheet", "GAAP",
     r"\bbalance\s+sheet\b", r"governmental\s+funds?", r"reconciliation"),
]
PATTERNS = [(t, b, re.compile(p), re.compile(r) if r else None, re.compile(x) if x else None)
            for t, b, p, r, x in PATTERNS]
TOC_RE = re.compile(r"table\s+of\s+contents|\bcontents\b")
CONTINUED_RE = re.compile(r"\(?\bcontinued\b\)?|\bcont'?d\b")
NUMBER_RE = re.compile(r"\(?\$?\s?\d{1,3}(,\d{3})+(\.\d\d)?\)?")
DOT_LEADER_RE = re.compile(r"\.{5,}\s*\d+")

ENTITY_TYPES = [
    ("sanitary and improvement district", r"sanitary\s+(and|&)\s+improvement\s+district|\bsid\b"),
    ("natural resources district", r"natural\s+resources?\s+district|\bnrd\b"),
    ("school district", r"school\s+district|public\s+schools|educational\s+service\s+unit"),
    ("fire district", r"(rural\s+)?fire\s+(protection\s+)?district"),
    ("hospital", r"hospital|health\s+care\s+(center|authority)"),
    ("public power / utility", r"public\s+power|power\s+district|utilities\s+(board|district)|irrigation\s+district"),
    ("housing authority", r"housing\s+authority"),
    ("airport authority", r"airport\s+authority"),
    ("community college", r"community\s+college"),
    ("county", r"\bcounty\s+of\b|\bcounty,?\s+nebraska\b|\bcounty\b"),
    ("village", r"\bvillage\s+of\b|\bvillage\b"),
    ("city", r"\bcity\s+of\b|\bcity\b"),
]


def norm(s):
    s = s.lower().replace("–", "-").replace("—", "-").replace("’", "'")
    return re.sub(r"[ \t]+", " ", s)


def header(text):
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return " ".join(lines[:HEADER_LINES])


def is_toc(text):
    t = text[:600]
    return bool(TOC_RE.search(t)) and len(DOT_LEADER_RE.findall(text)) + text.count("....") >= 2 \
        or len(DOT_LEADER_RE.findall(text)) >= 4


def is_narrative(text):
    """Auditor's opinion / notes pages: lots of prose, few numbers."""
    words = len(text.split())
    nums = len(NUMBER_RE.findall(text))
    return words > 250 and nums < 8


def classify(text):
    h = norm(header(text))
    full = norm(text)
    if is_toc(full) or is_narrative(full):
        return None
    for t, basis, pat, req, excl in PATTERNS:
        m = pat.search(h)
        if not m:
            continue
        if req and not req.search(h):
            continue
        if excl and excl.search(h):
            continue
        return t, basis
    return None


def entity_from_filename(fn):
    stem = Path(fn).stem
    stem = re.sub(r"[_\-\s]*(FY|CY)?\s?(19|20)\d\d([_\-]\d{2,4})?$", "", stem, flags=re.I)
    return re.sub(r"[_]+", " ", stem).strip()


def entity_type_of(fn, first_text):
    hay = norm(entity_from_filename(fn) + " " + first_text[:3000])
    for name, rx in ENTITY_TYPES:
        if re.search(rx, hay):
            return name
    return "unknown"


def process(pdf_path, url):
    doc = pymupdf.open(pdf_path)
    texts = [p.get_text("text") for p in doc]
    total_chars = sum(len(t.strip()) for t in texts)
    keep, types = [], []
    i = 0
    while i < len(texts):
        c = classify(texts[i])
        if not c:
            i += 1
            continue
        t, _ = c
        keep.append(i)
        types.append(t)
        # Continuation pages: "(continued)" in the header, or the same title repeated,
        # or a numbers-heavy page with no other title directly following.
        j = i + 1
        while j < len(texts) and j - i <= MAX_CONTINUATIONS:
            h = norm(header(texts[j]))
            cj = classify(texts[j])
            if cj and cj[0] == t:
                keep.append(j); types.append(t); j += 1; continue
            if cj:
                break
            if CONTINUED_RE.search(h) and not is_narrative(norm(texts[j])) \
                    and len(NUMBER_RE.findall(texts[j])) >= 10:
                keep.append(j); types.append(t + "(cont)"); j += 1; continue
            break
        i = j

    found = sorted(set(types), key=types.index)
    basis = ("GAAP" if any(t.startswith(("gov_", "proprietary")) for t in found)
             else "cash" if found else "")
    if not keep:
        flag = "likely_scanned" if total_chars < SCANNED_CHARS_PER_PAGE * max(len(texts), 1) \
            else "no_statements_found"
    else:
        flag = ""
    trimmed_name = ""
    if keep:
        out = pymupdf.open()
        for p in keep:
            out.insert_pdf(doc, from_page=p, to_page=p)
        TRIMMED.mkdir(exist_ok=True)
        trimmed_name = Path(pdf_path).name
        out.save(TRIMMED / trimmed_name, garbage=4, deflate=True)
        out.close()
    first_text = " ".join(texts[:3])
    row = dict(filename=Path(pdf_path).name, url=url,
               entity_name=entity_from_filename(Path(pdf_path).name),
               entity_type=entity_type_of(Path(pdf_path).name, first_text),
               basis_guess=basis, total_pages=len(texts),
               pages_kept=";".join(str(p + 1) for p in keep),
               page_types=";".join(types), trimmed_file=f"trimmed/{trimmed_name}" if trimmed_name else "",
               flag=flag, processed_at=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"))
    doc.close()
    return row


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--keep", action="store_true", help="keep full PDFs in audits/ after processing")
    ap.add_argument("files", nargs="*", help="specific PDFs (default: everything in audits/)")
    a = ap.parse_args()

    urls = {r["filename"]: r["url"] for r in read_csv(FILE_LIST)}
    done = {r["filename"] for r in read_csv(MANIFEST)}
    files = [Path(f) for f in a.files] or sorted(AUDITS.glob("*.pdf"))
    n = 0
    for f in files:
        if f.name in done:
            continue
        try:
            row = process(f, urls.get(f.name, ""))
        except Exception as e:  # noqa: BLE001
            row = dict(filename=f.name, url=urls.get(f.name, ""), entity_name=entity_from_filename(f.name),
                       flag=f"error: {type(e).__name__}: {e}"[:200],
                       processed_at=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"))
        append_csv(MANIFEST, row, MANIFEST_FIELDS)
        n += 1
        print(f"{f.name}: pages {row.get('pages_kept') or '-'} {row.get('flag', '')}")
        if not a.keep:
            f.unlink(missing_ok=True)
    print(f"processed {n} PDFs")


if __name__ == "__main__":
    main()
