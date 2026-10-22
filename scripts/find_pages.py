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
import io
import os
import re
from difflib import SequenceMatcher
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (AUDITS, FILE_LIST, MANIFEST, MANIFEST_FIELDS, ROOT, TRIMMED,  # noqa: E402
                    append_csv, read_csv, write_csv)

# Bump whenever page selection changes: reports trimmed by an older version are re-trimmed
# automatically on the next fetch run (see --drop-stale). Bumping re-trims EVERY report; for a fix
# that matters to a few reports, list them in config/retrim.csv instead and keep the version.
# (The v9 and v10 page-selection changes are applied that way: targeted re-trims under version 8.)
FINDER_VERSION = 8

HEADER_FRAC = 0.30         # statement titles sit in the top 30% of the page
OCR_FRAC = 0.35            # how much of a scanned page to OCR
OCR_DPI = 150
MIN_TEXT_CHARS = 50        # below this a page is treated as scanned (image only)
MAX_CONTINUATIONS = 8
MAX_UNTITLED = 2           # untitled number-heavy pages accepted right after a statement
MAX_DETAIL_PAGES = 25      # soft cap on supplementary detail pages per report (extraction cost)
MAX_DETAIL_PAGES_HARD = 30 # the top-priority schedule is never split below this
TITLE_MAX_OFFSET = 30      # a title must start within this many characters of its line
FLATTEN_DPI = 200          # layered scans are re-rendered to one black-and-white image at this dpi
FLATTEN_MARK = "flattened-v1"

# Statement title families: (type, regex). Checked in order; the first match wins.
_OF = r"statements?\s+of\s*"
_BASIS = r"((the\s+)?(modified\s+)?cash(\s+basis)?\s+)?"   # "cash" / "cash basis" / "modified cash basis"
TITLES = [
    ("receipts_disbursements", _OF + r"(the\s+)?" + _BASIS + r"receipts,?\s*(and\s+)?(disburse?ments|expenditures)"),
    ("fund_balance_changes", _OF + _BASIS + r"(fund\s+balances?\s+and\s+)?changes\s+in\s+" + _BASIS + r"(cash|fund)\s+balances?"),
    ("revenues_expenditures", _OF + r"revenues?,?\s*expenditures"),
    ("proprietary_revenues_expenses", _OF + r"revenues?,?\s*(and\s+)?(support,?\s*(and\s+)?|gains,?\s*(and\s+)?)?expenses"),
    ("activities", _OF + r"activities"),
    ("cash_flows", _OF + r"(changes\s+in\s+)?cash\s+flows?\b"),   # "statement of changes in cash flow"
    ("assets_fund_balances", _OF + _BASIS + r"assets,?\s*(liabilities,?\s*)?(and|&)\s+(fund\s+balances?|net\s+(position|assets)|cash\s+balances?)"),
    ("net_position", _OF + r"(fund\s+)?net\s+(position|assets)|" + _OF + r"financial\s+position"),
    ("balance_sheet", r"balance\s+sheets?\b"),
    # Some small-village audits present their only statement as a "schedule"; used only when
    # no statement of receipts/revenues was found anywhere in the report.
    ("schedule_receipts_disbursements", r"schedules?\s+of\s*(modified\s+)?(cash\s+)?receipts,?\s*(and\s+)?disbursements"),
]
TITLES = [(t, re.compile(rx)) for t, rx in TITLES]
TITLES_RX = dict(TITLES)
# Waiver-type filings. Each of these pages carries receipts, disbursements AND beginning/ending
# cash + investments, so it is both the flow and the balance statement.
WAIVER_SUMMARY_RE = re.compile(r"summary\s+statement\s+of\s+(actual\s+)?cash\s+receipts,?\s*disbursements,?\s*(and\s+)?balances")
VILLAGE_WAIVER_RE = re.compile(r"village\s+audit\s+waiver\s+request\s+form")
WAIVER_INVESTMENTS_RE = re.compile(r"^schedule\s+of\s+investments\b", re.M)
# Budget forms filed as the audit waiver (Budgets_Filed/*_Bnnnn): page 2 "total all funds" holds
# actual 2023-24 (column 1), actual[/estimated] 2024-25 (column 2) and the adopted budget (column 3);
# the "general budget form worksheet" pages split each year by fund; "documentation of transfers"
# explains column-2 transfers.
BUDGET_FORM_RE = re.compile(r"\b(general|sid|city|village|county)\s+budget\s+form\b|this\s+budget\s+is\s+for\s+the\s+period")
BUDGET_PAGES = [
    ("budget_all_funds", re.compile(r"total\s*all\s*funds")),   # OCR may drop the spaces
    ("budget_fund_worksheet", re.compile(r"budget\s+form\s+worksheet")),
    ("budget_transfers", re.compile(r"documentation\s+of\s+transfers")),
]
BUDGET_PAGES_RX = dict(BUDGET_PAGES)
NOT_FINANCIAL_RE = re.compile(r"full[- ]time\s+equivalent\s+student\s+enrollment|enrollment\s+statements")
BALANCE_TYPES = {"net_position", "balance_sheet", "assets_fund_balances", "waiver_summary", "budget_all_funds"}
FLOW_TYPES = {"receipts_disbursements", "fund_balance_changes", "revenues_expenditures", "waiver_summary",
              "budget_all_funds"}
# A header containing any of these is not a basic statement we want.
EXCLUDE_RE = re.compile(
    r"combining|individual\s+fund|federal\s+awards|findings|fiduciary|"
    r"proportionate|contributions\s+to|notes\s+to|questioned|"
    r"schedule\s+of\s+(investments|outstanding|interfund|changes|expenditures|cash\s+disbursements\s+of)|"
    r"table\s+of\s+contents|\bindex\b|summary\s+schedule|county\s+officers?|office\s+activities|"
    r"discussion\s+and\s+analysis|summary\s+statements?|condensed|"
    r"independent\s+(auditor|accountant)|certified\s+public\s+accountants")
# These words also name statement *columns* ("Special Education Agency Fund", "Police & Fire
# Pension", "Budget", "2024 (unaudited)"), so they only count in the title lines above the date
# line, never in the column headings below it. A community development agency is not an agency fund.
AGENCY_FUND = r"(?<!development\s)agency\s+funds?"
COLUMN_EXCLUDE_RE = re.compile(r"budget|custodial|" + AGENCY_FUND + r"|pension|opeb|\(unaudited\)|activity\s+funds?\b")
# Reconciliation pages are excluded only when a header line *starts* with "reconciliation": SID
# statements carry a "reconciliation" column ("fund fund total reconciliation net position").
# A cash flow statement's own "Reconciliation of operating income (loss) to net cash ..." section
# doesn't count: that page is the statement's continuation.
RECONCIL_TITLE_RE = re.compile(r"^\W*(a\s+)?reconcil", re.M)
CASH_FLOW_RECONCIL_RE = re.compile(r"^\W*reconcil\w*\s+of\s+(net\s+)?operating\s+(income|loss|revenues|\(loss\))")
# The date line ends a statement title ("For the Year Ended June 30, 2025", "September 30, 2025").
DATE_LINE_RE = re.compile(
    r"\b(years?|period|months?)\s+end|\bended\b|\b(january|february|march|april|may|june|july|august|"
    r"september|october|november|december)\s+\d{1,2}\b|\b\d{1,2}/\d{1,2}/\d{2,4}\b|\bfiscal\s+years\b|"
    r"\bas\s+of\b")


def title_lines(lines, default):
    """Header lines up to and including the date line, or `default` if no date line is found."""
    for k, ln in enumerate(lines[:12]):
        if DATE_LINE_RE.search(ln):
            return lines[:k + 1]
    return default


# A bare "Reconciliation" / "Reconciling items (Note 2)" line is a column heading of a SID's combined
# fund/government-wide statement (OCR and some text layers put each heading word on its own line).
RECONCIL_COLUMN_RE = re.compile(r"^\W*reconcil\w*(\s+items)?(\s+\(note\s+\d+\))?\W*$")


def reconcil_title(lines):
    return bool(RECONCIL_TITLE_RE.search("\n".join(ln for ln in lines[:20] if not CASH_FLOW_RECONCIL_RE.search(ln)
                                                    and not RECONCIL_COLUMN_RE.search(ln))))


def excluded(lines, rx=None, column_default=None):
    """True if a page header names a page we never want (see EXCLUDE_RE / COLUMN_EXCLUDE_RE /
    RECONCIL_TITLE_RE). column_default: the lines COLUMN_EXCLUDE_RE checks when the header has no
    date line (default: the first 20 header lines)."""
    rx = rx or EXCLUDE_RE
    cols = title_lines(lines, lines[:20] if column_default is None else column_default)
    return bool(rx.search(" ".join(lines[:20])) or COLUMN_EXCLUDE_RE.search(" ".join(cols))
                or reconcil_title(lines))


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


def ocr_region(page, frac, side="top", dpi=OCR_DPI):
    """OCR one edge strip of an image-only page (needs Tesseract; returns "" if unavailable).
    side "top" is the normal header; "left"/"right" read a landscape table scanned sideways onto a
    portrait page (text running upward / downward), turned upright before OCR."""
    global _ocr_ok
    if _ocr_ok is False:
        return ""
    try:
        r = page.rect
        if side == "top":
            clip, rot = pymupdf.Rect(r.x0, r.y0, r.x1, r.y0 + r.height * frac), 0
        elif side == "left":
            clip, rot = pymupdf.Rect(r.x0, r.y0, r.x0 + r.width * frac, r.y1), 90
        else:
            clip, rot = pymupdf.Rect(r.x1 - r.width * frac, r.y0, r.x1, r.y1), -90
        mat = pymupdf.Matrix(dpi / 72, dpi / 72).prerotate(rot)
        pix = page.get_pixmap(matrix=mat, clip=clip)
        text = pymupdf.open("pdf", pix.pdfocr_tobytes())[0].get_text("text")
        _ocr_ok = True
        return text
    except Exception as e:  # noqa: BLE001
        if _ocr_ok is None:
            print(f"OCR unavailable ({e}); scanned pages will not be classified")
        _ocr_ok = False
        return ""


# Text-layer quality: a scanner's bad OCR layer (e.g. a landscape page OCR'd sideways) reads as
# "rn 0 71- 0 rn 00 n n ..." - few real words or amounts. Such pages are OCR'd again instead.
WORD_RE = re.compile(r"\(?[a-z][a-z.,:;)'\-/&]{2,}")
AMOUNT_TOKEN_RE = re.compile(r"[$(]*\d{1,3}(,\d{3})*(\.\d+)?\)?|\$|-")
GARBLED_MIN_TOKENS = 50
GARBLED_MAX_GOOD = 0.5


def good_token_share(text):
    """Share of tokens that are real words (3+ letters with a vowel) or well-formed amounts."""
    toks = text.lower().split()
    good = sum(1 for t in toks if (WORD_RE.fullmatch(t) and re.search(r"[aeiouy]", t)) or AMOUNT_TOKEN_RE.fullmatch(t))
    return (good / len(toks) if toks else 0.0), len(toks)


def garbled(text):
    share, n = good_token_share(text)
    return n >= GARBLED_MIN_TOKENS and share < GARBLED_MAX_GOOD


def n_words(text):
    return sum(1 for t in text.lower().split() if WORD_RE.fullmatch(t) and re.search(r"[aeiouy]", t))


def ocr_quality(text):
    """(real words, their share of the non-amount tokens) of OCR output."""
    toks = [t for t in text.lower().split() if not AMOUNT_TOKEN_RE.fullmatch(t)]
    w = n_words(text)
    return w, (w / len(toks) if toks else 0.0)


OCR_MIN_WORDS = 5          # fewer real words than this in the OCR'd header, or
OCR_MIN_SHARE = 0.4        # less than this share of real words: try reading the page sideways


def ocr_header(page):
    """OCR text of a scanned page's header. A landscape table scanned sideways onto a portrait page
    reads as noise from the top strip; then the left and right strips are read turned upright and
    the one with the most real words (weighted by their share) is used. A sideways strip must
    itself read as text (share >= OCR_MIN_SHARE): an upright page whose top strip is just a few
    column headings ("Total Governmental Funds" over numbers) reads sideways as many short noise
    "words" ("aineo fale feats ..."), which would otherwise outscore the real header."""
    text = ocr_region(page, OCR_FRAC)
    w, share = ocr_quality(text)
    if (w >= OCR_MIN_WORDS and share >= OCR_MIN_SHARE) or _ocr_ok is False:
        return text
    best, score = text, w * share
    for side in ("left", "right"):
        alt = ocr_region(page, OCR_FRAC, side)
        aw, ashare = ocr_quality(alt)
        if aw >= OCR_MIN_WORDS and ashare >= OCR_MIN_SHARE and aw * ashare > score:
            best, score = alt, aw * ashare
    return best


def _junk(ln):
    """OCR noise lines ("|", "i", "{") that would split a wrapped title."""
    return len(re.sub(r"[^a-z0-9]", "", ln)) <= 1


def rotated_header(page):
    """Header lines of a page whose text runs sideways (landscape tables printed on a portrait
    page), or None if the text is mostly horizontal. Text running upward has its top at the
    left edge; text running downward has its top at the right edge. Positions are taken as
    displayed, i.e. after the page's /Rotate; a rotated page whose text displays horizontally
    gets its header from the displayed top of the page."""
    lines, horiz, hor, rot = [], [], 0, 0
    m = page.rotation_matrix
    mdir = pymupdf.Matrix(m.a, m.b, m.c, m.d, 0, 0)
    for b in page.get_text("dict")["blocks"]:
        for ln in b.get("lines", []):
            txt = "".join(sp["text"] for sp in ln["spans"]).strip()
            if not txt:
                continue
            bb = pymupdf.Rect(ln["bbox"]) * m
            d = pymupdf.Point(ln["dir"]) * mdir
            dx, dy = d.x, d.y
            if abs(dx) > 0.9:
                hor += len(txt)
                horiz.append((bb, txt))
            else:
                rot += len(txt)
                lines.append((dy, bb, txt))
    r = page.rect
    if rot <= hor or not lines:
        if not page.rotation or not horiz:
            return None
        # displayed horizontally on a /Rotate'd page: get_text(clip=) would read the wrong edge
        top = sorted((bb.y0, bb.x0, t) for bb, t in horiz if bb.y0 <= r.y0 + r.height * HEADER_FRAC)
        out = []
        for y, _, t in top:   # join pieces of one printed line
            if out and abs(y - out[-1][0]) < 3:
                out[-1][1].append(t)
            else:
                out.append((y, [t]))
        return [norm(" ".join(ts)).strip() for _, ts in out]
    up = sum(1 for dy, _, _ in lines if dy < 0) >= len(lines) / 2
    if up:   # reads bottom-to-top: header is the leftmost strip, lines ordered left to right
        sel = [(bb[0], -bb[3], t) for dy, bb, t in lines if dy < 0 and bb[0] <= r.x0 + r.width * HEADER_FRAC]
    else:    # reads top-to-bottom: header is the rightmost strip, lines ordered right to left
        sel = [(-bb[2], bb[1], t) for dy, bb, t in lines if dy > 0 and bb[2] >= r.x1 - r.width * HEADER_FRAC]
    return [norm(t).strip() for _, _, t in sorted(sel)]


def page_header(page):
    """(header lines in reading order, full text, source) for one page."""
    full = page.get_text("text")
    bad_layer = len(full.strip()) >= MIN_TEXT_CHARS and garbled(full)
    if len(full.strip()) < MIN_TEXT_CHARS or bad_layer:
        text = ocr_header(page)
        if not (bad_layer and n_words(text) < OCR_MIN_WORDS):   # else keep the (bad) text layer
            lines = [norm(ln).strip() for ln in text.splitlines() if ln.strip()]
            return [ln for ln in lines if not _junk(ln)], text, "ocr"
    lines = rotated_header(page)
    if lines is not None:
        return lines, full, "text-rotated"
    r = page.rect
    text = page.get_text("text", clip=pymupdf.Rect(r.x0, r.y0, r.x1, r.y0 + r.height * HEADER_FRAC), sort=True)
    lines = [norm(ln).strip() for ln in text.splitlines() if ln.strip()]
    return lines, full, "text"


FUND_PREFIX_RE = re.compile(r"((governmental|proprietary|enterprise|business-type|major|nonmajor|and|&|funds?)\s+)+")


def title_matches(lines):
    """Statement types whose title starts (near) the beginning of a header line.
    Two consecutive lines are joined so titles that wrap still match."""
    found = []
    ok = [ln for ln in lines if len(ln) <= TITLE_MAX_LEN and not PROSE_RE.search(ln)
          and not NOTE_HEADING_RE.search(ln)]
    cands = ok + [f"{a} {b}" for a, b in zip(ok, ok[1:])]
    # SID combined titles: "... changes in fund balances/net position/statement of activities"
    cands += [p.strip() for ln in ok if "/" in ln for p in ln.split("/")[1:] if p.strip().startswith("statement")]
    for ln in cands:
        if len(ln.split()) > 2 * TITLE_MAX_WORDS:
            continue
        # "Governmental and Proprietary Funds Balance Sheet/Statement of Net Position": a leading
        # fund-type phrase doesn't count toward the title offset
        pre = FUND_PREFIX_RE.match(ln)
        start = pre.end() if pre else 0
        for t, rx in TITLES:
            m = rx.search(ln)
            if m and m.start() - start <= TITLE_MAX_OFFSET and t not in found:
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
                           + r"(operating\s+|functional\s+)?(disbursements|expenditures|expenses)\b|by\s+(department|function|object)"
                             r"|departmental"
                             # school districts' "Cash disbursements for operational expenses" (by program and object)
                             r"|(cash\s+)?(disbursements|expenditures)\s+for\s+(operational|operating)\s+expen"),
    ("detail_schedule", r"(statement|schedule)s?\s+of\s+" + _BASIS
                        + r"(operating\s+|(net\s+)?patient\s+service\s+|net\s+)?(receipts|revenues?)\b"
                          r"(,?\s*(and\s+)?(disbursements|expenditures|expenses))?"),
]
DETAIL_TITLES = [(t, re.compile(rx)) for t, rx in DETAIL_TITLES]
# Hospital supplementary schedules are titled only "Schedule 5 / Net patient service revenue",
# "Other revenue", "Expenses"; accept these bare titles when a "schedule N" label heads the page.
SCHEDULE_LABEL_RE = re.compile(r"^schedule\s+\d{1,2}\b")
BARE_DETAIL_RE = re.compile(r"^((net\s+)?patient\s+service\s+revenues?|other\s+(operating\s+)?revenues?|"
                            r"(operating\s+)?revenues?|(operating\s+|departmental\s+)?expenses|"
                            r"(operating\s+)?expenses\s+by\s+(department|function))$")
DETAIL_EXCLUDE_RE = re.compile(
    r"federal\s+awards|findings|questioned|outstanding|investments|interfund|"
    r"contributions\s+to|notes?\s+to|table\s+of\s+contents|independent\s+(auditor|accountant)|"
    r"certified\s+public\s+accountants|discussion\s+and\s+analysis|county\s+officers?|"
    r"office\s+activit|summary\s+schedule|accountability|capital\s+assets|"
    r"taxes\s+(levied|certified|collected)|tax\s+collections?|fiduciary|"
    # (an enterprise fund's "changes in net position - budget and actual" is a budget schedule)
    r"reconcil|(?<!changes in )net\s+position|balance\s+sheet|assets\s+and|condensed|"
    r"our\s+audit|we\s+were\s+engaged|engaged\s+to\s+report|other\s+matters|in\s+our\s+opinion")
# Fund names that also head columns ("police & fire pension", "community development agency fund"):
# checked only in the title lines above the date line (see COLUMN_EXCLUDE_RE).
# Student activity fund schedules are kept (category student activities). Debt schedules are
# matched in the title lines only: a "Special Revenue | Debt Service" column heading wraps to
# "special debt / revenue service" and must not exclude the page.
DETAIL_COLUMN_EXCLUDE_RE = re.compile(r"pension|opeb|custodial|" + AGENCY_FUND
                                      + r"|debt(?!\s+service)|bonds?\s+payable")


def title_block(lines, limit=10, lines_out=False):
    """The heading lines above the table body: stop at the first line with a dollar amount or a
    section label ("Receipts:", "Local sources:"), so body lines such as 'Interest on investments
    150' or 'Debt service' don't trigger exclusions."""
    out = []
    for ln in lines[:limit]:
        if NUMBER_RE.search(ln) or "$" in ln or ln.rstrip().endswith(":"):
            break
        out.append(ln)
    return out if lines_out else " ".join(out)


def detail_excluded(lines, column_default=None):
    """True if a detail page's title block names a schedule we never want. column_default: the
    lines DETAIL_COLUMN_EXCLUDE_RE checks when there is no date line (default: the title block)."""
    tb = title_block(lines, lines_out=True)
    cols = title_lines(tb, tb if column_default is None else column_default)
    return bool(DETAIL_EXCLUDE_RE.search(" ".join(tb)) or DETAIL_COLUMN_EXCLUDE_RE.search(" ".join(cols)))


def classify_detail(lines, full_text=""):
    """Detail-schedule type for a page that isn't a basic statement, or None."""
    if not lines or detail_excluded(lines) or (full_text and is_toc(full_text)) \
            or re.search(r"reconcil", " ".join(lines[:20])):
        return None
    ok = [ln for ln in lines[:20] if len(ln) <= TITLE_MAX_LEN and not PROSE_RE.search(ln)
          and not NOTE_HEADING_RE.search(ln)]
    found = set()
    for ln in ok + [f"{a} {b}" for a, b in zip(ok, ok[1:])]:
        for t, rx in DETAIL_TITLES:
            m = rx.search(ln)
            if m and m.start() <= TITLE_MAX_OFFSET + 20:
                found.add(t)
    if any(SCHEDULE_LABEL_RE.search(ln) for ln in lines[:3]) and any(BARE_DETAIL_RE.search(ln) for ln in lines[:8]):
        found.add("detail_departments" if any(re.search(r"expenses", ln) for ln in lines[:8]) else "detail_schedule")
    # most specific first (DETAIL_TITLES order), so a wrapped "... by department" title wins
    return next((t for t, _ in DETAIL_TITLES if t in found), None)


def classify_lines(lines, full_text=""):
    """Statement type(s) for a page from its header lines, joined with "+" (combined statements
    such as "assets and fund balances and receipts, disbursements ..." match several), or None."""
    head = " ".join(lines[:20])
    if WAIVER_SUMMARY_RE.search(head):   # "summary statement" would otherwise be excluded
        return "waiver_summary"
    if not lines or excluded(lines) or (full_text and is_toc(full_text)):
        return None
    found = title_matches(lines[:20])
    # combined "receipts, disbursements ... and statement of assets and fund balances" titles:
    # the second title starts too far into the wrapped line for title_matches
    if has_type("+".join(found), FLOW_TYPES) and re.search(r"and\s+" + TITLES_RX["assets_fund_balances"].pattern, head):
        found.append("assets_fund_balances")
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


# The "Total All Funds" title sits in a boxed cell that OCR often misreads; the page is then known
# by its column headings ("Actual 2023-2024 | Actual/Estimated 2024-2025 | Adopted Budget 2025-2026",
# or "(Column 1) (Column 2) (Column 3)" over "Beginning Balances, Receipts, & Transfers").
ALL_FUNDS_COLS_RE = [re.compile(r"\bactual"), re.compile(r"adopted\s+budget")]
ALL_FUNDS_NUMBERED_RE = [re.compile(r"column\s*1"), re.compile(r"column\s*2"), re.compile(r"beginning")]


def budget_all_funds_fallback(pages, keep, kept_types):
    """Index of the Total All Funds page when its title wasn't read, or None: first a page with its
    column headings, else the page just before "Documentation of Transfers" (form page 2 comes right
    before form page 3) unless that is the cover page."""
    for i, (lines, _, _) in enumerate(pages):
        head = " ".join(lines[:12])
        if i in keep or BUDGET_FORM_RE.search(head) or BUDGET_PAGES_RX["budget_fund_worksheet"].search(head):
            continue
        if all(rx.search(head) for rx in ALL_FUNDS_COLS_RE) or all(rx.search(head) for rx in ALL_FUNDS_NUMBERED_RE):
            return i
    tr = next((k for k, t in zip(keep, kept_types) if t == "budget_transfers"), None)
    if tr and tr - 1 not in keep and not BUDGET_FORM_RE.search(" ".join(pages[tr - 1][0][:12])):
        return tr - 1
    return None


def select_budget_form(pages):
    """Budget form filed in place of a waiver: keep only the pages that print prior-year actuals."""
    keep, kept_types = [], []
    for i, (lines, full, src) in enumerate(pages):
        head = " ".join(lines[:12])
        for t, rx in BUDGET_PAGES:
            if rx.search(head) and not (t == "budget_fund_worksheet" and re.search(r"adopted\s+budget", head)):
                keep.append(i)
                kept_types.append(t)
                break
    if "budget_all_funds" not in kept_types:
        i = budget_all_funds_fallback(pages, keep, kept_types)
        if i is not None:
            n = sum(1 for k in keep if k < i)
            keep.insert(n, i)
            kept_types.insert(n, "budget_all_funds")
    n_ocr = sum(1 for _, _, src in pages if src == "ocr")
    flag = "budget_form" + ("" if "budget_all_funds" in kept_types else "; no_budget_actuals_found")
    if n_ocr:
        flag += f"; ocr_pages={n_ocr}"
    return keep, kept_types, "cash", flag


def select_pages(pages, filename=""):
    """pages: [(header lines, full text, source)]. Returns (keep indexes, their types, basis, flag)."""
    first_heads = " ".join(" ".join(lines[:15]) for lines, _, _ in pages[:12])
    if NOT_FINANCIAL_RE.search(first_heads) or re.search(r"_FTESE_", filename):
        return [], [], "", "not_financial_report (enrollment audit)"
    if re.search(r"_B\d{4}\.pdf$", filename) or (BUDGET_FORM_RE.search(first_heads)
                                                   and not VILLAGE_WAIVER_RE.search(first_heads)):
        return select_budget_form(pages)
    village_waiver = bool(VILLAGE_WAIVER_RE.search(first_heads))
    types = [classify_lines(lines, full) for lines, full, _ in pages]
    if village_waiver:   # Exhibit A's "Investments (should agree to Schedule 1)" -> keep Schedule 1
        types = [t or ("waiver_investments" if WAIVER_INVESTMENTS_RE.search("\n".join(lines[:6])) else None)
                 for t, (lines, _, _) in zip(types, pages)]
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
        detail = t.startswith("detail_")
        untitled = 0
        j = i + 1
        while j < len(pages) and j - i <= MAX_CONTINUATIONS:
            lines, full, src = pages[j]
            # (an untitled page has no date line, so fund names in its column headings don't count)
            if types[j] or reconcil_title(lines) or \
                    (detail_excluded(lines, []) if detail else excluded(lines, column_default=[])):
                break  # a titled page is handled by the outer loop
            lab_j = exhibit_label(lines)
            head8 = " ".join(lines[:8])
            # second page of a two-page statement of financial/net position (hospitals)
            if has_type(t, BALANCE_TYPES) and re.search(r"^liabilities(,|\s+and)", head8):
                keep.append(j)
                kept_types.append(t + "(cont)")
                j += 1
                continue
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
            # Same title as the page before, garbled by OCR ("receip1's", "activitifs fund")
            if src == "ocr" and len(lines) >= 3 and SequenceMatcher(
                    None, " ".join(lines[:3]), " ".join(pages[j - 1][0][:3])).ratio() >= 0.9:
                keep.append(j)
                kept_types.append(t + "(cont)")
                j += 1
                continue
            # Second page of a wide statement: no title at all, just more columns of numbers. A scanned
            # right-hand page may OCR few numbers ("(72 267)"), so there it is enough that its first
            # line repeats a column heading of the page before ("enterprise funds").
            same_head = src == "ocr" and bool(lines) and not DATE_LINE_RE.search(lines[0]) and \
                lines[0] in pages[j - 1][0][:12] and lines[0] != pages[j - 1][0][0]
            if untitled < MAX_UNTITLED and not PROSE_RE.search(" ".join(lines[:6])) and \
                    (same_head or len(NUMBER_RE.findall(full)) >= (8 if src == "ocr" else 15)):
                untitled += 1
                keep.append(j)
                kept_types.append(t + "(cont)")
                j += 1
                continue
            break
        i = j

    # Cap the detail pages (they're extra cost for extraction); basic statements always stay.
    # Departmental and budget schedules carry the most line-item detail, so they're kept first.
    # A schedule run (consecutive pages of one detail type, incl. its continuations) is kept or
    # dropped as a whole; runs are taken in priority order while they fit MAX_DETAIL_PAGES, and
    # the top-priority run is always kept whole up to MAX_DETAIL_PAGES_HARD.
    detail_idx = [n for n, t in enumerate(kept_types) if t.startswith("detail_")]
    truncated = len(detail_idx) > MAX_DETAIL_PAGES
    if truncated:
        prio = {"detail_departments": 0, "detail_budget": 1, "detail_combining": 2, "detail_schedule": 3}
        base = lambda n: kept_types[n].replace("(cont)", "")  # noqa: E731
        runs = []
        for n in detail_idx:
            if runs and base(n) == base(runs[-1][-1]) and keep[n] == keep[runs[-1][-1]] + 1:
                runs[-1].append(n)
            else:
                runs.append([n])
        runs.sort(key=lambda r: (prio.get(base(r[0]), 4), r[0]))
        taken, used = set(), 0
        for r in runs:
            if used + len(r) <= MAX_DETAIL_PAGES or (not taken and len(r) <= MAX_DETAIL_PAGES_HARD):
                taken.update(r)
                used += len(r)
            elif not taken:  # an oversized top-priority run keeps its first pages
                taken.update(r[:MAX_DETAIL_PAGES_HARD])
                used += MAX_DETAIL_PAGES_HARD
        drop = set(detail_idx) - taken
        truncated = bool(drop)
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
    elif not any(has_type(t, FLOW_TYPES | {"schedule_receipts_disbursements", "activities",
                                           "proprietary_revenues_expenses"}) for t in basic):
        flag = "no_revenue_statement"
    elif not any(has_type(t, BALANCE_TYPES) for t in basic) and not village_waiver:
        flag = "no_balance_statement"   # village waiver Exhibit A carries the beginning/ending balances
    else:
        flag = ""
    if truncated:
        flag = (flag + "; " if flag else "") + f"detail_truncated({len(detail_idx)})"
    if basic and not any(t.startswith("detail_") for t in kept_types):
        flag = (flag + "; " if flag else "") + "no_detail_schedules"
    if n_ocr:
        flag = (flag + "; " if flag else "") + f"ocr_pages={n_ocr}"
    return keep, kept_types, basis, flag


def needs_flatten(page):
    """Scanner 'mixed raster' pages (a background image plus stacked 1-bit CCITT/JBIG2 masks and an
    invisible OCR layer) show only partly in many viewers (e.g. numbers but no words)."""
    ims = page.get_images(full=True)
    return len(ims) > 1 or any(im[8] in ("CCITTFaxDecode", "JBIG2Decode") for im in ims)


def flatten_layered(doc):
    """Return a copy of doc in which layered scan pages are a single black-and-white image."""
    from PIL import Image  # only needed here
    if not any(needs_flatten(p) for p in doc):
        return doc
    out = pymupdf.open()
    for n, p in enumerate(doc):
        if not needs_flatten(p):
            out.insert_pdf(doc, from_page=n, to_page=n)
            continue
        pix = p.get_pixmap(dpi=FLATTEN_DPI, colorspace=pymupdf.csGRAY)
        im = Image.frombytes("L", (pix.width, pix.height), pix.samples)
        buf = io.BytesIO()
        im.point(lambda v: 255 if v > 180 else 0).convert("1").save(buf, "PNG", optimize=True)
        new = out.new_page(width=p.rect.width, height=p.rect.height)
        new.insert_image(new.rect, stream=buf.getvalue())
    return out


def save_trimmed(doc, path):
    doc = flatten_layered(doc)
    doc.set_metadata({"keywords": FLATTEN_MARK})
    doc.save(path, garbage=4, deflate=True)


def process(pdf_path, meta):
    url = meta.get("url", "")
    doc = pymupdf.open(pdf_path)
    pages = [page_header(p) for p in doc]
    keep, kept_types, basis, flag = select_pages(pages, Path(pdf_path).name)
    trimmed_name = ""
    if keep:
        out = pymupdf.open()
        for p in keep:
            out.insert_pdf(doc, from_page=p, to_page=p)
        TRIMMED.mkdir(exist_ok=True)
        trimmed_name = Path(pdf_path).name
        save_trimmed(out, TRIMMED / trimmed_name)
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
               flag=flag, processed_at=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
               finder_version=FINDER_VERSION)
    doc.close()
    return row


def _process_safe(job):
    f, m = job
    try:
        return process(f, m)
    except Exception as e:  # noqa: BLE001
        return dict(filename=f.name, url=m.get("url", ""), entity_name=m.get("entity_name") or entity_from_filename(f.name),
                    county=m.get("county", ""), subdivision_type=m.get("subdivision_type", ""),
                    fiscal_year=m.get("fiscal_year", ""), filing_type=m.get("filing_type", ""),
                    flag=f"error: {type(e).__name__}: {e}"[:200],
                    processed_at=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                    finder_version=FINDER_VERSION)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--keep", action="store_true", help="keep full PDFs in audits/ after processing")
    ap.add_argument("--drop-stale", action="store_true",
                    help="remove manifest rows (and trimmed PDFs) made by an older FINDER_VERSION, so the "
                         "next download/trim re-processes them; then exit")
    ap.add_argument("--flatten-existing", action="store_true",
                    help="flatten layered scan pages in trimmed/*.pdf made before flattening existed; then exit")
    ap.add_argument("files", nargs="*", help="specific PDFs (default: everything in audits/)")
    a = ap.parse_args()

    if a.flatten_existing:
        n = 0
        for f in sorted(TRIMMED.glob("*.pdf")):
            doc = pymupdf.open(f)
            if (doc.metadata or {}).get("keywords") == FLATTEN_MARK:
                continue
            tmp = f.with_suffix(".tmp")
            save_trimmed(doc, tmp)
            doc.close()
            tmp.replace(f)
            n += 1
        print(f"flattened/marked {n} trimmed PDFs")
        return

    if a.drop_stale:
        rows = read_csv(MANIFEST)
        # config/retrim.csv (stem,before): also re-trim these reports if trimmed before that UTC time
        # (a finder fix that only affects a few already-trimmed reports, without a version bump)
        rt = {r["stem"]: r["before"] for r in read_csv(ROOT / "config" / "retrim.csv")}
        fresh = [r for r in rows if str(r.get("finder_version") or "") == str(FINDER_VERSION)
                 and not (r["filename"][:-4] in rt and (r.get("processed_at") or "") < rt[r["filename"][:-4]])]
        stale = [r for r in rows if r not in fresh]
        for r in stale:
            if r.get("trimmed_file"):
                (TRIMMED.parent / r["trimmed_file"]).unlink(missing_ok=True)
        if stale or (rows and "finder_version" not in rows[0]):
            write_csv(MANIFEST, fresh, MANIFEST_FIELDS)  # also upgrades the header
        print(f"finder v{FINDER_VERSION}: {len(stale)} stale reports queued for re-trimming, {len(fresh)} current")
        return

    meta = {r["filename"]: r for r in read_csv(FILE_LIST)}
    done = {r["filename"] for r in read_csv(MANIFEST)}
    files = [Path(f) for f in a.files] or sorted(AUDITS.glob("*.pdf"))
    n = 0
    todo = [f for f in files if f.name not in done]
    # OCR is CPU-bound: trim several PDFs at once (one Tesseract thread each); rows are written here,
    # in order, so the manifest is only ever touched by this process.
    workers = max(1, int(os.environ.get("TRIM_WORKERS", os.cpu_count() or 1)))
    os.environ.setdefault("OMP_THREAD_LIMIT", "1")
    jobs = [(f, meta.get(f.name, {})) for f in todo]
    if workers > 1 and len(jobs) > 1:
        pool = ProcessPoolExecutor(workers)
        results = pool.map(_process_safe, jobs)
    else:
        pool, results = None, map(_process_safe, jobs)
    for (f, _), row in zip(jobs, results):
        append_csv(MANIFEST, row, MANIFEST_FIELDS)
        n += 1
        print(f"{f.name}: pages {row.get('pages_kept') or '-'} {row.get('flag', '')}")
        if not a.keep:
            f.unlink(missing_ok=True)
    if pool:
        pool.shutdown()
    print(f"processed {n} PDFs")


if __name__ == "__main__":
    main()
