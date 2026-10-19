"""List and download Nebraska local-government audit filings, one at a time, politely.

Listing uses the Auditor of Public Accounts' official search
(https://www.nebraska.gov/auditor/reports/index.cgi?audit=1). The form POSTs to index.cgi with
  type=audit, subtype=<subdivision type> (multi), year=<fiscal year> (multi),
  countyselect=<county> (multi, optional), name=<entity> (multi, optional),
  csv=0 (HTML results page) or csv=1 ("Export As Excel File").
One request per subdivision type: the Excel export first (used if it carries PDF links), then
the HTML results page. Raw responses are saved to output/listing_debug/.

Strategies (--strategy):
  search     - the official search above (default)
  dirlist    - parse an HTML directory listing at DIR_URL (the Audits_Filed folder has none today)
  (with "search", a saved export at config/export_<year>.xlsx is read first and the live
   search only runs for subdivision types it doesn't cover)
  urls_file  - config/audit_urls.txt, one PDF URL per line (always merged in when present)

Writes output/file_list.csv (filename, url, entity_name, county, subdivision_type,
fiscal_year, filing_type = Audit/Waiver) and logs download failures to
output/download_errors.csv. Exits with status 2 if no PDFs are listed.
"""
import argparse
import csv
import datetime as dt
import io
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (AUDITS, DOWNLOAD_ERRORS, FILE_LIST, MANIFEST, OUTPUT, ROOT,  # noqa: E402
                    append_csv, read_csv, write_csv)

# ---------------- CONFIG (edit here, or override with flags / env vars) ----------------
YEAR = os.environ.get("AUDIT_YEAR", "2025")
SEARCH_URL = os.environ.get("AUDIT_SEARCH_URL", "https://www.nebraska.gov/auditor/reports/index.cgi")
SEARCH_PAGE = SEARCH_URL + "?audit=1"
SUBTYPES = os.environ.get("AUDIT_SUBTYPES", "all")
# Every subdivision type offered by the Auditor's search form ("all" expands to these).
ALL_SUBTYPES = [
    "Agricultural Societies", "Airport Authorities", "Cemetery Districts", "Cities and Villages",
    "Cities and Villages - Utilities", "Community Colleges", "Community Redevelopment Authorities", "Counties",
    "County Proprietary Funds", "ESUs", "Fire - Mutual Finance Organizations", "Fire Districts",
    "Health Care Facilities", "Historical Societies", "Housing Authorities", "Land Banks",
    "Miscellaneous Districts", "Municipal Inland Port Authorities", "Natural Resources Districts",
    "Public Power Districts", "Road Improvement Districts", "Sanitary Improvement Districts",
    "School Districts", "Townships", "Water Districts",
]
# Filing types to download (all filings are listed in file_list.csv regardless).
FILING_TYPES = os.environ.get("AUDIT_FILING_TYPES", "Audit,Waiver")
DIR_URL = os.environ.get("AUDIT_DIR_URL", "https://auditors.nebraska.gov/Audits_Filed/{year}/")
# A link counts as a filing PDF if its URL matches this.
PDF_URL_REGEX = os.environ.get("AUDIT_PDF_REGEX", r"\.pdf(\?|#|$)")
URLS_FILE = ROOT / "config" / "audit_urls.txt"
# Saved export from the search page; used first when present (search only fills in missing types).
EXPORT_FILE = ROOT / "config" / "export_{year}.xlsx"
USER_AGENT = "Nebraska-Audit research pipeline (github.com/trltrl12/Nebraska-Audit)"
# ----------------------------------------------------------------------------------------

DEBUG_DIR = OUTPUT / "listing_debug"
LIST_FIELDS = ["filename", "url", "entity_name", "county", "subdivision_type", "fiscal_year",
               "filing_type", "link_text"]
ERROR_FIELDS = ["timestamp", "run_id", "filename", "url", "attempts", "error"]
RUN_ID = os.environ.get("GITHUB_RUN_ID", "local")

session = requests.Session()
session.headers["User-Agent"] = USER_AGENT


def save_debug(name, content):
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", name)[:150]
    data = content if isinstance(content, bytes) else content.encode("utf-8", "replace")
    (DEBUG_DIR / safe).write_bytes(data[:8_000_000])


# ---------- parsing search responses (HTML table, HTML-as-xls, xlsx, CSV) ----------

COL_HINTS = {  # checked in this order; a column is claimed by the first key that matches it
    "subdivision_type": r"type\s*of\s*sub|sub\w*\s*type|subtype|category",
    "filing_type": r"filing\s*type|audit\s*/\s*waiver|waiver|report\s*type|^type$",
    "entity_name": r"name\s*of\s*sub|subdivision\s*name|entity|^name|political|subdivision",
    "county": r"county",
    "fiscal_year": r"fiscal|year|\bfy\b|period",
}
# Row labels that mark a transposed export (one filing per column)
TRANSPOSED_RE = re.compile(r"name\s*of\s*subdivision|link\s*to\s*report|filing\s*type", re.I)


def untranspose(rows):
    """The APA export puts field names in column A and one filing per column."""
    if len(rows) > 1 and sum(bool(TRANSPOSED_RE.search(str(r[0] if r else ""))) for r in rows) >= 2:
        width = max(len(r) for r in rows)
        rows = [list(r) + [""] * (width - len(r)) for r in rows]
        return [list(col) for col in zip(*rows)]
    return rows


def map_columns(header):
    out = {}
    for key, rx in COL_HINTS.items():
        for i, h in enumerate(header):
            if i not in out.values() and re.search(rx, (h or "").strip(), re.I):
                out[key] = i
                break
    return out


def filing_type_of(cells, url, link_text):
    hay = " ".join([*cells, url, link_text])
    if re.search(r"waiver", hay, re.I):
        return "Waiver"
    if re.search(r"\baudit", hay, re.I):
        return "Audit"
    return ""


def records_from_table(header, rows, base_url, pdf_re):
    """rows: list of (cells[str], links[(href, text)])."""
    cols = map_columns(header)
    recs = []
    for cells, links in rows:
        # URLs can also sit in cell text (exports)
        links = list(links) + [(m, "") for c in cells for m in re.findall(r"https?://[^\s\"']+?\.pdf", c, re.I)]
        for href, text in links:
            url = urljoin(base_url, href.strip())
            if not pdf_re.search(url):
                continue
            g = lambda k: cells[cols[k]].strip() if k in cols and cols[k] < len(cells) else ""  # noqa: E731
            ft = g("filing_type")
            ft = filing_type_of([ft], "", "") if ft else filing_type_of(cells, url, text)
            recs.append(dict(url=url, entity_name=g("entity_name") or (cells[0].strip() if cells else ""),
                             county=g("county"), subdivision_type=g("subdivision_type"),
                             fiscal_year=g("fiscal_year"), filing_type=ft, link_text=text.strip()))
    return recs


def parse_html(html, base_url, pdf_re):
    soup = BeautifulSoup(html, "html.parser")
    recs = []
    for table in soup.find_all("table"):
        trs = table.find_all("tr")
        if not trs:
            continue
        header, body = [], []
        for tr in trs:
            ths = tr.find_all("th")
            tds = tr.find_all(["td", "th"])
            if ths and not tr.find_all("td") and not header:
                header = [c.get_text(" ", strip=True) for c in ths]
                continue
            cells = [c.get_text(" ", strip=True) for c in tds]
            links = [(a["href"], a.get_text(" ", strip=True)) for a in tr.find_all("a", href=True)]
            body.append((cells, links))
        if not header and body and not any(l for _, l in body[:1]):
            header = body.pop(0)[0]
        recs += records_from_table(header, body, base_url, pdf_re)
    if not recs:  # PDF links outside tables
        for a in soup.find_all("a", href=True):
            parent = a.find_parent(["li", "p", "div", "tr"]) or a
            recs += records_from_table([], [([parent.get_text(" ", strip=True)], [(a["href"], a.get_text(" ", strip=True))])],
                                       base_url, pdf_re)
    return recs


def parse_xlsx(data, base_url, pdf_re):
    from openpyxl import load_workbook
    # data_only=False keeps =HYPERLINK("url") formulas, which is where the export puts the links
    wb = load_workbook(io.BytesIO(data), read_only=False, data_only=False)
    recs = []
    for ws in wb.worksheets:
        grid, links = [], {}
        for r in ws.iter_rows():
            grid.append([str(c.value) if c.value is not None else "" for c in r])
            for c in r:
                if c.hyperlink and c.hyperlink.target:
                    links[(c.row - 1, c.column - 1)] = c.hyperlink.target
        if not grid:
            continue
        for (i, j), url in links.items():  # fold real hyperlinks into the cell text
            grid[i][j] = f"{grid[i][j]} {url}"
        grid = untranspose(grid)
        recs += records_from_table(grid[0], [(r, []) for r in grid[1:]], base_url, pdf_re)
    return recs


def parse_csv_text(text, base_url, pdf_re):
    delim = "\t" if text[:4096].count("\t") > text[:4096].count(",") else ","
    rows = untranspose(list(csv.reader(io.StringIO(text), delimiter=delim)))
    if not rows:
        return []
    return records_from_table(rows[0], [(r, []) for r in rows[1:]], base_url, pdf_re)


def parse_response(resp, pdf_re):
    data = resp.content
    if data[:2] == b"PK":
        return parse_xlsx(data, resp.url, pdf_re), "xlsx"
    text = resp.text
    if re.search(r"<(table|html|a\s)", text[:20000], re.I):
        return parse_html(text, resp.url, pdf_re), "html"
    return parse_csv_text(text, resp.url, pdf_re), "csv"


def list_search(year, subtypes, delay, pdf_re):
    # Load the search page first (sets any session cookies the CGI expects).
    try:
        time.sleep(delay)
        session.get(SEARCH_PAGE, timeout=60)
    except Exception as e:  # noqa: BLE001
        print(f"[search] could not load {SEARCH_PAGE}: {e}")
    recs = []
    for st in subtypes:
        got, export = [], []
        for csv_flag in ("1", "0"):  # Excel export first, then the HTML results page
            params = dict(type="audit", subtype=st, year=year, csv=csv_flag)
            try:
                time.sleep(delay)
                r = session.post(SEARCH_URL, data=params, timeout=180,
                                 headers={"Referer": SEARCH_PAGE})
                r.raise_for_status()
            except Exception as e:  # noqa: BLE001
                print(f"[search] {st} csv={csv_flag}: {e}")
                continue
            kind_guess = "xlsx" if r.content[:2] == b"PK" else ("html" if b"<" in r.content[:2000] else "csv")
            save_debug(f"search_{year}_{st}_{'export' if csv_flag == '1' else 'results'}.{kind_guess}", r.content)
            try:
                found, kind = parse_response(r, pdf_re)
            except Exception as e:  # noqa: BLE001
                print(f"[search] {st}: could not parse response: {type(e).__name__}: {e}")
                found, kind = [], "unparsed"
            print(f"[search] {st} {'export' if csv_flag == '1' else 'results'} ({kind}, {len(r.content)} bytes): "
                  f"{len(found)} PDF links")
            if csv_flag == "1":
                export = found
                if found:
                    got = found
                    break
            else:
                # Results page has the links; borrow filing type etc. from the export when missing.
                by_name = {}
                for e in export:
                    by_name.setdefault(e["entity_name"].lower(), e)
                for f in found:
                    e = by_name.get(f["entity_name"].lower())
                    if e:
                        for k in ("filing_type", "county", "fiscal_year"):
                            f[k] = f[k] or e[k]
                got = found
        for g in got:
            g["subdivision_type"] = g["subdivision_type"] or st
            g["fiscal_year"] = g["fiscal_year"] or year
        recs += got
    return recs


def list_dirlist(year, delay, pdf_re):
    url = DIR_URL.format(year=year)
    try:
        time.sleep(delay)
        r = session.get(url, timeout=60)
        r.raise_for_status()
    except Exception as e:  # noqa: BLE001
        print(f"[dirlist] {url}: {e}")
        return []
    save_debug(f"dirlist_{year}.html", r.text)
    recs = parse_html(r.text, url, pdf_re)
    print(f"[dirlist] {url}: {len(recs)} PDFs")
    return recs


def list_export_file(year, pdf_re):
    """A saved copy of the search's "Export As Excel File" (config/export_<year>.xlsx or .csv)."""
    for p in (EXPORT_FILE.with_suffix(".xlsx"), EXPORT_FILE.with_suffix(".csv")):
        p = Path(str(p).format(year=year))
        if p.exists():
            data = p.read_bytes()
            recs = (parse_xlsx(data, SEARCH_URL, pdf_re) if data[:2] == b"PK"
                    else parse_csv_text(data.decode("utf-8", "replace"), SEARCH_URL, pdf_re))
            print(f"[export_file] {p.relative_to(ROOT)}: {len(recs)} PDF links")
            return recs
    return []


def list_urls_file():
    if not URLS_FILE.exists():
        return []
    urls = [ln.strip() for ln in URLS_FILE.read_text().splitlines() if ln.strip() and not ln.startswith("#")]
    if urls:
        print(f"[urls_file] {len(urls)} URLs")
    return [dict(url=u, filing_type=filing_type_of([], u, "")) for u in urls]


def filename_of(url):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", unquote(Path(urlparse(url).path).name))


def merge(rows, recs):
    """Add new records to file_list rows, keyed by URL; give each a unique filename."""
    by_url = {r["url"]: r for r in rows}
    names = {r["filename"] for r in rows}
    for rec in recs:
        if rec["url"] in by_url:
            old = by_url[rec["url"]]
            for k, v in rec.items():
                if v and not old.get(k):
                    old[k] = v
            continue
        fn = filename_of(rec["url"])
        if not fn.lower().endswith(".pdf"):
            fn += ".pdf"
        stem, n = fn[:-4], 2
        while fn in names:
            fn, n = f"{stem}_{n}.pdf", n + 1
        names.add(fn)
        row = dict(rec, filename=fn)
        rows.append(row)
        by_url[rec["url"]] = row
    rows.sort(key=lambda r: r["filename"].lower())
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--year", default=YEAR)
    ap.add_argument("--subtypes", default=SUBTYPES, help="comma-separated subdivision types")
    ap.add_argument("--filing-types", default=FILING_TYPES, help="comma-separated: Audit,Waiver")
    ap.add_argument("--strategy", default="search", choices=["search", "dirlist", "urls_file"])
    ap.add_argument("--max-files", type=int, default=0, help="download at most N files this call (0 = no limit)")
    ap.add_argument("--delay", type=float, default=1.5, help="seconds between requests")
    ap.add_argument("--retries", type=int, default=3)
    ap.add_argument("--relist", action="store_true", help="re-query the search even if listed already")
    ap.add_argument("--list-only", action="store_true")
    a = ap.parse_args()

    pdf_re = re.compile(PDF_URL_REGEX, re.I)
    subtypes = [s.strip() for s in a.subtypes.split(",") if s.strip()]
    if [s.lower() for s in subtypes] == ["all"]:
        subtypes = ALL_SUBTYPES
    filing_types = {s.strip().lower() for s in a.filing_types.split(",") if s.strip()}

    rows = [r for r in read_csv(FILE_LIST) if r.get("url")]
    listed = lambda: {(r.get("subdivision_type"), str(r.get("fiscal_year"))) for r in rows}  # noqa: E731
    missing = subtypes if a.relist else [s for s in subtypes if (s, a.year) not in listed()]
    if missing or not rows:
        recs = []
        if a.strategy == "search":
            rows = merge(rows, list_export_file(a.year, pdf_re))
            missing = [s for s in missing if (s, a.year) not in listed()]
            if missing:
                recs += list_search(a.year, missing, a.delay, pdf_re)
        elif a.strategy == "dirlist":
            recs += list_dirlist(a.year, a.delay, pdf_re)
        recs += list_urls_file()
        rows = merge(rows, recs)
        write_csv(FILE_LIST, rows, LIST_FIELDS)
        print(f"file_list.csv: {len(rows)} PDFs")
    if not rows:
        print("::error title=No PDFs found::The audit search returned 0 PDF links. Inspect the raw "
              "responses in output/listing_debug/ and adjust scripts/download.py (CONFIG / parsers), "
              "or list URLs in config/audit_urls.txt.")
        print("downloaded=0 remaining=0")
        sys.exit(2)
    if a.list_only:
        return

    processed = {r["filename"] for r in read_csv(MANIFEST)}
    failed_this_run = {r["filename"] for r in read_csv(DOWNLOAD_ERRORS) if r.get("run_id") == RUN_ID}
    AUDITS.mkdir(exist_ok=True)
    todo = [r for r in rows if r["filename"] not in processed and r["filename"] not in failed_this_run
            and r.get("subdivision_type", "") in subtypes + [""]
            and (not r.get("filing_type") or r["filing_type"].lower() in filing_types)]
    downloaded = failures = 0
    for r in todo:
        if a.max_files and downloaded >= a.max_files:
            break
        dest = AUDITS / r["filename"]
        if dest.exists() and dest.stat().st_size > 0:
            downloaded += 1  # already on disk, awaiting find_pages
            continue
        err = None
        for attempt in range(1, a.retries + 1):
            try:
                time.sleep(a.delay if attempt == 1 else a.delay * 2 * attempt)
                resp = session.get(r["url"], timeout=120, stream=True)
                resp.raise_for_status()
                tmp = dest.with_suffix(".part")
                with tmp.open("wb") as f:
                    for chunk in resp.iter_content(1 << 16):
                        f.write(chunk)
                with tmp.open("rb") as f:
                    if f.read(5) != b"%PDF-":
                        tmp.unlink()
                        raise ValueError("response is not a PDF")
                tmp.rename(dest)
                err = None
                break
            except Exception as e:  # noqa: BLE001
                err = f"{type(e).__name__}: {e}"
                print(f"  attempt {attempt} failed for {r['filename']}: {err}")
        if err:
            append_csv(DOWNLOAD_ERRORS, dict(timestamp=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                                             run_id=RUN_ID, filename=r["filename"], url=r["url"],
                                             attempts=a.retries, error=err[:300]), ERROR_FIELDS)
            failures += 1
        else:
            downloaded += 1
            print(f"  downloaded {r['filename']}")
    remaining = len(todo) - downloaded - failures
    print(f"downloaded={downloaded} remaining={max(remaining, 0)}")


if __name__ == "__main__":
    main()
