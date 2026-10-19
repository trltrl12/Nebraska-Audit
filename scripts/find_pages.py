"""Find the basic financial-statement pages in each downloaded audit and save them as a small PDF.

For every PDF in audits/ not yet in output/manifest.csv, each page's title is read from the top
of the page (text layer sorted by position, or OCR for scanned pages) and matched against:
  * revenue/expenditure statements: receipts & disbursements, changes in fund/cash balances,
    revenues & expenditures, statement of activities, proprietary revenues & expenses;
  * balance statements: statement of net position, balance sheet, statement of assets
    (liabilities) and fund balances / net position;
  * statement of cash flows;
plus "(continued)" pages. Budget, reconciliation, combining, fiduciary and other supplementary
pages are excluded. Matching pages go to trimmed/<filename> and a row is appended to
output/manifest.csv. Full PDFs are deleted after processing unless --keep is given.
OCR needs Tesseract (apt install tesseract-ocr) and TESSDATA_PREFIX set.
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

HEADER_FRAC = 0.30         # statement titles sit in the top 30% of the page
OCR_FRAC = 0.35            # how much of a scanned page to OCR
OCR_DPI = 150
MIN_TEXT_CHARS = 50        # below this a page is treated as scanned (image only)
MAX_CONTINUATIONS = 8
MAX_UNTITLED = 2           # untitled number-heavy pages accepted right after a statement
MAX_DETAIL_PAGES = 15      # cap on supplementary detail pages per report (extraction cost)
TITLE_MAX_OFFSET = 30      # a title must start within this many characters of its line

# Statement title families: (type, regex). Checked in order; the first match wins.
_OF = r"statements?\s+of\s*"
_BASIS = r"((the\s+)?(modified\s+)?cash(\s+basis)?\s+)?"   # "cash" / "cash basis" / "modified cash basis"
TITLES = [
    ("receipts_disbursements", _OF + r"(the\s+)?" + _BASIS + r"receipts,?\s*(and\s+)?(disbursements|expenditures)"),
    ("fund_balance_changes", _OF + _BASIS + r"(fund\s+balances?\s+and\s+)?changes\s+in\s+" + _BASIS + r"(cash|fund)\s+balances?"),
    ("revenues_expenditures", _OF + r"revenues,?\s*expenditures"),
    ("proprietary_revenues_expenses", _OF + r"revenues,?\s*expenses"),
    ("activities", _OF + r"activities"),
    ("cash_flows", _OF + r"cash\s+flows"),
    ("assets_fund_balances", _OF + _BASIS + r"assets,?\s*(liabilities,?\s*)?(and|&)\s+(fund\s+balances?|net\s+position|cash\s+balances?)"),
    ("net_position", _OF + r"(fund\s+)?net\s+(position|assets)"),
    ("balance_sheet", r"balance\s+sheets?\b"),
    # Some small-village audits present their only statement as a "schedule"; used only when
    # no statement of receipts/revenues was found anywhere in the report.
    ("schedule_receipts_disbursements", r"schedules?\s+of\s*(modified\s+)?(cash\s+)?receipts,?\s*(and\s+)?disbursements"),
]
TITLES = [(t, re.compile(rx)) for t, rx in TITLES]
FLOW_TYPES = {"receipts_disbursements", "fund_balance_changes", "revenues_expenditures"}
# A header containing any of these is not a basic statement we want.
EXCLUDE_RE = re.compile(
    r"reconcil|budget|combining|individual\s+fund|federal\s+awards|findings|fiduciary|custodial|"
    r"agency\s+fund|pension|opeb|proportionate|contributions\s+to|notes\s+to|questioned|"
    r"schedule\s+of\s+(investments|outstanding|interfund|changes|expenditures|cash\s+disbursements\s+of)|"
    r"table\s+of\s+contents|\bindex\b|summary\s+schedule|county\s+officers?|office\s+activities|"
    r"discussion\s+and\s+analysis|summary\s+statements?|condensed|\(unaudited\)|activity\s+funds?\b|"
    r"independent\s+(auditor|accountant)|certified\s+public\s+accountants")
# A matched "title" line that reads like a sentence is prose (MD&A, notes, opinion), not a title.
PROSE_RE = re.compile(r"\b(presents?|shows?|reports?|reported|are\s+different|provides?|includes?|"
                      r"is\s+presented|we\s+have|in\s+the\s+accompanying|focus|comprises?|responsible|"
                      r"our|we|opinions?|assurance)\b")
TITLE_MAX_WORDS = 18
TITLE_MAX_LEN = 130
CONTINUED_RE = re.compile(r"\bcontinued\b|\bcont'?d\b|\bpage\s+[2-9]\b|\bpage\s+\d\s+of\s+\d")
EXHIBIT_RE = re.compile(r"\b(exhibit|statement)\s+([a-z]|[0-9]{1,2}|[ivxl!|1]{1,5})(?=[\s.,:;-]|$)")
NOTE_HEADING_RE = re.compile(r"^\(?(\d{1,2}|[a-h])[.)]\s")
NUMBER_RE = re.compile(r"\(?\$?\s?\d{1,3}(,\d{3})+(\.\d\d)?\)?")
CASH_BASIS_RE = re.compile(r"cash\s+basis|cash\s+transactions|cash\s+receipts|receipts,?\s*(and\s+)?disbursements")
NOTES_RE = re.compile(r"^notes\s+to\s+(the\s+)?(basic\s+)?financial\s+statements")
MODIFIED_RE = re.compile(r"modified\s+cash")


def norm(s):
    s = s.lower()
    for a in "–—−":
        s = s.replace(a, "-")
    s = s.replace("’", "'").replace("\xa0", " ")
    return re.sub(r"[ \t]+", " ", s)


_ocr_ok = None


def ocr_region(page, frac):
    """OCR the top part of an image-only page (needs Tesseract; returns "" if unavailable)."""
    global _ocr_ok
    if _ocr_ok is False:
        return ""
    try:
        r = page.rect
        pix = page.get_pixmap(dpi=OCR_DPI, clip=pymupdf.Rect(r.x0, r.y0, r.x1, r.y0 + r.height * frac))
        text = pymupdf.open("pdf", pix.pdfocr_tobytes())[0].get_text("text")
        _ocr_ok = True
        return text
    except Exception as e:  # noqa: BLE001
        if _ocr_ok is None:
            print(f"OCR unavailable ({e}); scanned pages will not be classified")
        _ocr_ok = False
        return ""


def rotated_header(page):
    """Header lines of a page whose text runs sideways (landscape tables printed on a portrait
    page), or None if the text is mostly horizontal. Text running upward has its top at the
    left edge; text running downward has its top at the right edge."""
    lines, hor, rot = [], 0, 0
    for b in page.get_text("dict")["blocks"]:
        for ln in b.get("lines", []):
            txt = "".join(sp["text"] for sp in ln["spans"]).strip()
            if not txt:
                continue
            dx, dy = ln["dir"]
            if abs(dx) > 0.9:
                hor += len(txt)
            else:
                rot += len(txt)
                lines.append((dy, ln["bbox"], txt))
    if rot <= hor or not lines:
        return None
    up = sum(1 for dy, _, _ in lines if dy < 0) >= len(lines) / 2
    r = page.rect
    if up:   # reads bottom-to-top: header is the leftmost strip, lines ordered left to right
        sel = [(bb[0], -bb[3], t) for dy, bb, t in lines if dy < 0 and bb[0] <= r.x0 + r.width * HEADER_FRAC]
    else:    # reads top-to-bottom: header is the rightmost strip, lines ordered right to left
        sel = [(-bb[2], bb[1], t) for dy, bb, t in lines if dy > 0 and bb[2] >= r.x1 - r.width * HEADER_FRAC]
    return [norm(t).strip() for _, _, t in sorted(sel)]


def page_header(page):
    """(header lines in reading order, full text, source) for one page."""
    full = page.get_text("text")
    if len(full.strip()) < MIN_TEXT_CHARS:
        text, src = ocr_region(page, OCR_FRAC), "ocr"
        full = text
        lines = [norm(ln).strip() for ln in text.splitlines() if ln.strip()]
        return lines, full, src
    lines = rotated_header(page)
    if lines is not None:
        return lines, full, "text-rotated"
    r = page.rect
    text = page.get_text("text", clip=pymupdf.Rect(r.x0, r.y0, r.x1, r.y0 + r.height * HEADER_FRAC), sort=True)
    lines = [norm(ln).strip() for ln in text.splitlines() if ln.strip()]
    return lines, full, "text"


def title_matches(lines):
    """Statement types whose title starts (near) the beginning of a header line.
    Two consecutive lines are joined so titles that wrap still match."""
    found = []
    ok = [ln for ln in lines if len(ln) <= TITLE_MAX_LEN and not PROSE_RE.search(ln)
          and not NOTE_HEADING_RE.search(ln)]
    cands = ok + [f"{a} {b}" for a, b in zip(ok, ok[1:])]
    for ln in cands:
        if len(ln.split()) > 2 * TITLE_MAX_WORDS:
            continue
        for t, rx in TITLES:
            m = rx.search(ln)
            if m and m.start() <= TITLE_MAX_OFFSET and t not in found:
                found.append(t)
    return found


def is_toc(full_text):
    """A table of contents lists several statement titles on one page."""
    lines = [norm(ln).strip() for ln in full_text.splitlines() if ln.strip()]
    hits = sum(1 for ln in lines for _, rx in TITLES if (m := rx.search(ln)) and m.start() <= TITLE_MAX_OFFSET)
    return hits >= 4 or bool(re.search(r"table\s+of\s+contents|^contents$", norm(full_text[:400]), re.M))


# Supplementary schedules with line-item detail (revenue sources, spending by department/object).
# These usually follow the notes. Checked only on pages that aren't basic statements.
DETAIL_TITLES = [
    ("detail_budget", r"budgetary\s+comparison|budget\s+(and|to)\s+actual|actual\s+and\s+budget|"
                      r"budgetary\s+basis|compared\s+to\s+budget"),
    ("detail_combining", r"combining\s+(statements?|schedules?)\s+of\s+" + _BASIS
                         + r"(receipts|revenues|disbursements|expenditures)"),
    ("detail_departments", r"(statement|schedule)s?\s+of\s+(general\s+fund\s+)?" + _BASIS
                           + r"(disbursements|expenditures)\b|by\s+(department|function|object)|departmental"),
    ("detail_schedule", r"(statement|schedule)s?\s+of\s+" + _BASIS
                        + r"(receipts|revenues)(,?\s*(and\s+)?(disbursements|expenditures))?"),
]
DETAIL_TITLES = [(t, re.compile(rx)) for t, rx in DETAIL_TITLES]
DETAIL_EXCLUDE_RE = re.compile(
    r"federal\s+awards|findings|questioned|outstanding|investments|interfund|pension|opeb|"
    r"contributions\s+to|notes\s+to|table\s+of\s+contents|independent\s+(auditor|accountant)|"
    r"certified\s+public\s+accountants|discussion\s+and\s+analysis|county\s+officers?|"
    r"office\s+activit|summary\s+schedule|debt|bonds?\s+payable|capital\s+assets|"
    r"taxes\s+(levied|certified|collected)|tax\s+collections?|fiduciary|custodial|agency\s+funds?|"
    r"reconcil|net\s+position|balance\s+sheet|assets\s+and|condensed|activity\s+funds?\b")


def classify_detail(lines, full_text=""):
    """Detail-schedule type for a page that isn't a basic statement, or None."""
    head = " ".join(lines[:20])
    if not lines or DETAIL_EXCLUDE_RE.search(head) or (full_text and is_toc(full_text)):
        return None
    ok = [ln for ln in lines[:20] if len(ln) <= TITLE_MAX_LEN and not PROSE_RE.search(ln)
          and not NOTE_HEADING_RE.search(ln)]
    for ln in ok + [f"{a} {b}" for a, b in zip(ok, ok[1:])]:
        for t, rx in DETAIL_TITLES:
            m = rx.search(ln)
            if m and m.start() <= TITLE_MAX_OFFSET + 20:
                return t
    return None


def classify_lines(lines, full_text=""):
    """Statement type(s) for a page from its header lines, joined with "+" (combined statements
    such as "assets and fund balances and receipts, disbursements ..." match several), or None."""
    head = " ".join(lines[:20])
    if not lines or EXCLUDE_RE.search(head) or (full_text and is_toc(full_text)):
        return None
    found = title_matches(lines[:20])
    # "assets ... and receipts, disbursements" also contains the receipts title further along
    if "assets_fund_balances" in found and re.search(r"receipts,?\s*(and\s+)?disbursements|changes\s+in", head):
        found.append("receipts_disbursements")
    found = list(dict.fromkeys(found))
    return "+".join(found) if found else None


def exhibit_label(lines):
    """'Exhibit II' / 'Exhibit B' / 'Statement 3' label in a page header (OCR'd l ! | 1 read as i)."""
    for ln in lines[:8]:
        m = EXHIBIT_RE.search(ln)
        if m:
            lab = m.group(2)
            if re.fullmatch(r"[ivxl!|1]+", lab) and not lab.isdigit():
                lab = re.sub(r"[l!|1]", "i", lab)
            return f"{m.group(1)} {lab}"
    return None


def has_type(t, kinds):
    return bool(t) and any(k in t.split("+") for k in kinds)


def classify(text):
    """Classify from plain page text (used by tests / diagnostics)."""
    lines = [norm(ln).strip() for ln in text.splitlines() if ln.strip()]
    return classify_lines(lines[:20], text)


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


# Search-form subdivision type -> entity_type ("Cities and Villages" is refined from the text)
SUBTYPE_TO_ENTITY = {
    "counties": "county", "county proprietary funds": "county", "school districts": "school district",
    "esus": "school district", "natural resources districts": "natural resources district",
    "fire districts": "fire district", "sanitary improvement districts": "sanitary and improvement district",
    "health care facilities": "hospital", "public power districts": "public power / utility",
    "cities and villages - utilities": "public power / utility", "housing authorities": "housing authority",
    "airport authorities": "airport authority", "community colleges": "community college",
}


def entity_type_from(meta, fn, first_text):
    st = (meta.get("subdivision_type") or "").strip().lower()
    if st in SUBTYPE_TO_ENTITY:
        return SUBTYPE_TO_ENTITY[st]
    t = entity_type_of(fn, first_text)
    if st == "cities and villages" and t not in ("city", "village"):
        return "city/village"
    return t if t != "unknown" or not st else st


def select_pages(pages):
    """pages: [(header lines, full text, source)]. Returns (keep indexes, their types, basis, flag)."""
    types = [classify_lines(lines, full) for lines, full, _ in pages]
    # Basic statements come before the notes; statement-like pages after the notes are
    # supplementary (individual fund statements, combining schedules) - unless that's all there is.
    first = next((i for i, t in enumerate(types) if t), None)
    if first is not None:
        notes = next((i for i in range(first, len(pages))
                      if any(NOTES_RE.search(ln) for ln in pages[i][0][:8])), None)
        if notes is not None and any(types[:notes]):
            types = types[:notes] + [None] * (len(types) - notes)
    # Schedules count as the main statement only when the report has no statement of
    # receipts/revenues at all; otherwise they're detail.
    if any(has_type(t, FLOW_TYPES) for t in types):
        types = ["detail_schedule" if t == "schedule_receipts_disbursements" else t for t in types]
    # Detail schedules (line items) anywhere in the report
    types = [t or classify_detail(lines, full) for t, (lines, full, _) in zip(types, pages)]

    keep, kept_types = [], []
    i = 0
    while i < len(pages):
        t = types[i]
        if not t:
            i += 1
            continue
        keep.append(i)
        kept_types.append(t)
        # Continuation pages: same title again, or "(continued)" with numbers and no other title.
        label = exhibit_label(pages[i][0])
        excl = DETAIL_EXCLUDE_RE if t.startswith("detail_") else EXCLUDE_RE
        untitled = 0
        j = i + 1
        while j < len(pages) and j - i <= MAX_CONTINUATIONS:
            lines, full, src = pages[j]
            if types[j] or excl.search(" ".join(lines[:20])):
                break  # a titled page is handled by the outer loop
            lab_j = exhibit_label(lines)
            head8 = " ".join(lines[:8])
            # OCR sometimes drops spaces ("exhibithi", "page1"), so the exhibit word alone counts here
            if (lab_j or "exhibit" in head8) and \
                    (lab_j == label or (label is None and re.search(r"\bpage\s*\d", head8))):
                # untitled facing/continuation page of the same exhibit
                keep.append(j)
                kept_types.append(t + "(cont)")
                j += 1
                continue
            # OCR only covers the top of the page, so don't require many numbers there
            if CONTINUED_RE.search(" ".join(lines[:12])) and \
                    len(NUMBER_RE.findall(full)) >= (0 if src == "ocr" else 10):
                keep.append(j)
                kept_types.append(t + "(cont)")
                j += 1
                continue
            # Second page of a wide statement: no title at all, just more columns of numbers.
            if untitled < MAX_UNTITLED and not PROSE_RE.search(" ".join(lines[:6])) and \
                    len(NUMBER_RE.findall(full)) >= (8 if src == "ocr" else 15):
                untitled += 1
                keep.append(j)
                kept_types.append(t + "(cont)")
                j += 1
                continue
            break
        i = j

    # Cap the detail pages (they're extra cost for extraction); basic statements always stay.
    detail_idx = [n for n, t in enumerate(kept_types) if t.startswith("detail_")]
    truncated = len(detail_idx) > MAX_DETAIL_PAGES
    if truncated:
        drop = set(detail_idx[MAX_DETAIL_PAGES:])
        keep = [k for n, k in enumerate(keep) if n not in drop]
        kept_types = [t for n, t in enumerate(kept_types) if n not in drop]
    basic = [t for t in kept_types if not t.startswith("detail_")]

    heads = " ".join(" ".join(pages[p][0][:20]) for p, t in zip(keep, kept_types) if not t.startswith("detail_"))
    if not keep:
        basis = ""
    elif MODIFIED_RE.search(heads):
        basis = "modified cash"
    elif any(has_type(t, ("revenues_expenditures", "proprietary_revenues_expenses", "cash_flows",
                          "balance_sheet")) for t in basic) and not CASH_BASIS_RE.search(heads):
        basis = "GAAP"
    else:
        basis = "cash"
    n_ocr = sum(1 for _, _, src in pages if src == "ocr")
    if not basic:
        flag = "no_statements_found" + ("; scanned, OCR found no titles" if n_ocr > len(pages) / 2 else "")
    elif len(basic) > 25:
        flag = "many_pages"
    elif not any(has_type(t, FLOW_TYPES | {"schedule_receipts_disbursements", "activities"}) for t in basic):
        flag = "no_revenue_statement"
    elif not any(has_type(t, ("net_position", "balance_sheet", "assets_fund_balances")) for t in basic):
        flag = "no_balance_statement"
    else:
        flag = ""
    if truncated:
        flag = (flag + "; " if flag else "") + f"detail_truncated({len(detail_idx)})"
    if basic and not any(t.startswith("detail_") for t in kept_types):
        flag = (flag + "; " if flag else "") + "no_detail_schedules"
    if n_ocr:
        flag = (flag + "; " if flag else "") + f"ocr_pages={n_ocr}"
    return keep, kept_types, basis, flag


def process(pdf_path, meta):
    url = meta.get("url", "")
    doc = pymupdf.open(pdf_path)
    pages = [page_header(p) for p in doc]
    keep, kept_types, basis, flag = select_pages(pages)
    trimmed_name = ""
    if keep:
        out = pymupdf.open()
        for p in keep:
            out.insert_pdf(doc, from_page=p, to_page=p)
        TRIMMED.mkdir(exist_ok=True)
        trimmed_name = Path(pdf_path).name
        out.save(TRIMMED / trimmed_name, garbage=4, deflate=True)
        out.close()
    first_text = " ".join(full for _, full, _ in pages[:3])
    row = dict(filename=Path(pdf_path).name, url=url,
               entity_name=meta.get("entity_name") or entity_from_filename(Path(pdf_path).name),
               entity_type=entity_type_from(meta, Path(pdf_path).name, first_text),
               county=meta.get("county", ""), subdivision_type=meta.get("subdivision_type", ""),
               fiscal_year=meta.get("fiscal_year", ""), filing_type=meta.get("filing_type", ""),
               basis_guess=basis, total_pages=len(pages),
               pages_kept=";".join(str(p + 1) for p in keep),
               page_types=";".join(kept_types), trimmed_file=f"trimmed/{trimmed_name}" if trimmed_name else "",
               flag=flag, processed_at=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"))
    doc.close()
    return row


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--keep", action="store_true", help="keep full PDFs in audits/ after processing")
    ap.add_argument("files", nargs="*", help="specific PDFs (default: everything in audits/)")
    a = ap.parse_args()

    meta = {r["filename"]: r for r in read_csv(FILE_LIST)}
    done = {r["filename"] for r in read_csv(MANIFEST)}
    files = [Path(f) for f in a.files] or sorted(AUDITS.glob("*.pdf"))
    n = 0
    for f in files:
        if f.name in done:
            continue
        try:
            row = process(f, meta.get(f.name, {}))
        except Exception as e:  # noqa: BLE001
            m = meta.get(f.name, {})
            row = dict(filename=f.name, url=m.get("url", ""), entity_name=m.get("entity_name") or entity_from_filename(f.name),
                       county=m.get("county", ""), subdivision_type=m.get("subdivision_type", ""),
                       fiscal_year=m.get("fiscal_year", ""), filing_type=m.get("filing_type", ""),
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
