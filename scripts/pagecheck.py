"""Confirm that every extracted amount is printed on the trimmed-PDF page it cites.

For each money value in a report's JSON (receipts, disbursements, detail lines and totals,
balances, transfers, business-type balances, balance-sheet lines and totals, proprietary cash
flows) the number is looked for among the numbers on its source page: the PDF text layer, or OCR
for image-only pages (needs Tesseract; results cached in output/page_text/). Values the extractor
computed itself (marked "computed" or "split_from") are reported as computed rather than missing.
"""
import json
import re
import sys
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import OUTPUT, TRIMMED, amt, fund_type, page  # noqa: E402

CACHE = OUTPUT / "page_text"
OCR_DPI = 200
NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
_ocr_ok = None


def _ocr(p):
    global _ocr_ok
    if _ocr_ok is False:
        return ""
    try:
        pix = p.get_pixmap(dpi=OCR_DPI)
        text = pymupdf.open("pdf", pix.pdfocr_tobytes())[0].get_text("text")
        _ocr_ok = True
        return text
    except Exception as e:  # noqa: BLE001
        if _ocr_ok is None:
            print(f"pagecheck: OCR unavailable ({e}); image-only pages can't be checked")
        _ocr_ok = False
        return ""


def page_texts(stem):
    """List of page texts for trimmed/<stem>.pdf (None if the PDF isn't there)."""
    pdf = TRIMMED / f"{stem}.pdf"
    if not pdf.exists():
        return None
    key = f"{pdf.stat().st_size}"
    cache = CACHE / f"{stem}.json"
    if cache.exists():
        try:
            c = json.loads(cache.read_text(encoding="utf-8"))
            if c.get("key") == key:
                return c["pages"]
        except Exception:  # noqa: BLE001
            pass
    doc = pymupdf.open(pdf)
    texts, ocred = [], False
    for p in doc:
        t = p.get_text("text")
        if len(t.strip()) < 50:
            t, ocred = _ocr(p), True
        texts.append(t)
    doc.close()
    if ocred and _ocr_ok:  # only cache pages that were really read
        CACHE.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"key": key, "pages": texts}), encoding="utf-8")
    return texts


def numbers(text):
    out = set()
    for tok in NUM_RE.findall(text or ""):
        try:
            out.add(round(float(tok.replace(",", "")), 2))
        except ValueError:
            pass
    return out


def values(d):
    """(where, label, amount, source_page, fund_type, computed_note) for every money value."""
    def item(where, it, label=None):
        if not isinstance(it, dict) or amt(it) is None:
            return None
        note = it.get("computed") or (f"split from {it['split_from']}" if it.get("split_from") else "")
        return (where, label or it.get("original_label") or where, amt(it), page(it),
                fund_type(it) if "fund_type" in it else "", note)

    out = []
    for k in ("receipts", "disbursements", "detail", "adjustments"):
        out += [item(k, it) for it in d.get(k) or []]
    out += [item("detail_totals", it, f"total {it.get('section')} / {it.get('fund') or ''} / {it.get('schedule') or ''}")
            for it in d.get("detail_totals") or []]
    for k in ("beginning_cash", "beginning_investments", "beginning_fund_balance", "ending_cash",
              "ending_investments", "ending_fund_balance", "transfers_in", "transfers_out", "debt_proceeds",
              "other_financing"):
        out.append(item(k, d.get(k), k))
    for ft, tots in (d.get("reported_totals") or {}).items():
        if isinstance(tots, dict) and ft in ("governmental", "business-type"):
            for k, v in tots.items():
                out.append(item("reported_totals", v, f"printed {ft} total {k}"))
        elif isinstance(tots, dict):  # older flat form
            out.append(item("reported_totals", tots, f"printed total {ft}"))
    bt = d.get("business_type") or {}
    for k in ("beginning", "ending", "transfers_net", "debt_proceeds", "other_financing"):
        out.append(item("business_type", bt.get(k), f"business-type {k}"))
    out += [item("business_type", it) for it in bt.get("adjustments") or []]
    bs = d.get("balance_sheet") or {}
    for sec in ("assets", "deferred_outflows", "liabilities", "deferred_inflows", "equity"):
        out += [item(f"balance_sheet.{sec}", it) for it in bs.get(sec) or []]
        out.append(item("balance_sheet", bs.get(f"total_{sec}"), f"printed total_{sec}"))
    for k, v in (d.get("proprietary") or {}).items():
        out.append(item("proprietary", v, f"cash flows {k}"))
    return [v for v in out if v]


def check_report(stem, d):
    """List of {where, label, amount, source_page, reason} for amounts not printed on their page."""
    texts = page_texts(stem)
    if texts is None:
        return [dict(where="on_page", label="", amount="", source_page="",
                     reason="trimmed PDF not available; page check skipped")]
    nums = [numbers(t) for t in texts]
    out = []
    for where, label, a, sp, ft, note in values(d):
        if round(a, 2) == 0:
            continue  # zeros print as "-"
        try:
            pn = nums[int(sp) - 1]
        except (TypeError, ValueError, IndexError):
            out.append(dict(where=where, label=label, amount=a, source_page=sp, fund_type=ft,
                            reason=f"cites page {sp}, which isn't in the trimmed PDF"))
            continue
        v = round(abs(a), 2)
        if v in pn or (v == round(v) and float(round(v)) in pn):
            continue
        if note:
            reason = f"computed, not printed on p.{sp}: {note}"
        elif not texts[int(sp) - 1].strip():
            reason = f"page {sp} could not be read (image without OCR); amount unconfirmed"
        else:
            reason = f"amount not found on cited page {sp}; check it against the report"
        out.append(dict(where=where, label=label, amount=a, source_page=sp, fund_type=ft, reason=reason))
    return out
