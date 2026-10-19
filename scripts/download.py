"""Download Nebraska local-government audit PDFs, one at a time, politely.

Listing strategies (choose with --strategy, default "auto" = try them all and merge):
  dirlist    - parse an HTML directory listing at DIR_URL (e.g. .../Audits_Filed/2025/)
  reports    - crawl the APA reports database (nebraska.gov/auditor/reports/index.cgi?audit=1
               and FileSearch/years.cgi): follows links, submits search forms whose
               fields offer the target year, and follows pagination
  urls_file  - read config/audit_urls.txt (one PDF URL per line)

Every candidate PDF URL must match PDF_URL_REGEX. All URL patterns are in the CONFIG
block below or overridable by command-line flags / environment variables.

Writes output/file_list.csv (all PDFs found) and logs failures to
output/download_errors.csv. Raw HTML of listing pages is saved to
output/listing_debug/ so the site's structure can be inspected if listing fails.
"""
import argparse
import datetime as dt
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse, unquote

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (AUDITS, DOWNLOAD_ERRORS, FILE_LIST, MANIFEST, OUTPUT, ROOT,  # noqa: E402
                    append_csv, read_csv, write_csv)

# ---------------- CONFIG (edit here, or override with flags / env vars) ----------------
YEAR = os.environ.get("AUDIT_YEAR", "2025")
DIR_URL = os.environ.get("AUDIT_DIR_URL", "https://auditors.nebraska.gov/Audits_Filed/{year}/")
SEARCH_URLS = os.environ.get(
    "AUDIT_SEARCH_URLS",
    "https://www.nebraska.gov/auditor/reports/index.cgi?audit=1,"
    "https://www.nebraska.gov/auditor/reports/index.cgi,"
    "https://www.nebraska.gov/auditor/FileSearch/years.cgi",
).split(",")
# A URL is an audit PDF if it matches this (year substituted in).
PDF_URL_REGEX = os.environ.get("AUDIT_PDF_REGEX", r"/Audits_Filed/{year}/[^?#]+\.pdf$")
# Only crawl pages on these hosts, and only paths matching this regex.
CRAWL_HOSTS = {"www.nebraska.gov", "nebraska.gov", "auditors.nebraska.gov"}
CRAWL_PATH_REGEX = r"/auditor/(reports|FileSearch)/|/Audits_Filed/"
MAX_CRAWL_PAGES = int(os.environ.get("AUDIT_MAX_CRAWL_PAGES", "400"))
URLS_FILE = ROOT / "config" / "audit_urls.txt"
USER_AGENT = "Nebraska-Audit research pipeline (github.com/trltrl12/Nebraska-Audit)"
# ----------------------------------------------------------------------------------------

DEBUG_DIR = OUTPUT / "listing_debug"
ERROR_FIELDS = ["timestamp", "run_id", "filename", "url", "attempts", "error"]
RUN_ID = os.environ.get("GITHUB_RUN_ID", "local")

session = requests.Session()
session.headers["User-Agent"] = USER_AGENT


def get(url, delay, **kw):
    time.sleep(delay)
    r = session.get(url, timeout=60, **kw)
    r.raise_for_status()
    return r


def save_debug(name, text):
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", name)[:120]
    (DEBUG_DIR / f"{safe}.html").write_text(text[:500_000], encoding="utf-8", errors="replace")


def pdf_links(html, base, pdf_re):
    soup = BeautifulSoup(html, "html.parser")
    out = set()
    for a in soup.find_all("a", href=True):
        u = urljoin(base, a["href"].strip())
        if pdf_re.search(urlparse(u).path):
            out.add(u)
    return out, soup


def list_dirlist(year, delay, pdf_re):
    url = DIR_URL.format(year=year)
    try:
        r = get(url, delay)
    except Exception as e:  # noqa: BLE001
        print(f"[dirlist] {url}: {e}")
        return set()
    save_debug(f"dirlist_{year}", r.text)
    links, _ = pdf_links(r.text, url, pdf_re)
    print(f"[dirlist] {url}: {len(links)} PDFs")
    return links


def form_requests(soup, base, year):
    """Yield (method, url, params) for forms that have a field offering the target year."""
    for form in soup.find_all("form"):
        action = urljoin(base, form.get("action") or base)
        method = (form.get("method") or "get").lower()
        params, has_year = {}, False
        for inp in form.find_all("input"):
            n = inp.get("name")
            if not n or inp.get("type") in ("submit", "button", "image", "reset"):
                continue
            if inp.get("type") in ("checkbox", "radio") and not inp.has_attr("checked"):
                continue
            params[n] = inp.get("value", "")
        for sel in form.find_all("select"):
            n = sel.get("name")
            if not n:
                continue
            opts = sel.find_all("option")
            year_opt = next((o for o in opts if year in (o.get("value") or o.text)), None)
            if year_opt is not None and re.search(r"year|fy|fiscal", n + sel.get("id", ""), re.I):
                params[n] = year_opt.get("value") or year_opt.text.strip()
                has_year = True
            else:
                # Prefer an "all" option, else the first one.
                allo = next((o for o in opts if re.search(r"^\s*(all|any)\b", o.text, re.I)), None)
                o = allo or (opts[0] if opts else None)
                params[n] = (o.get("value") if o and o.has_attr("value") else (o.text.strip() if o else ""))
        for inp in form.find_all("input"):
            n = inp.get("name") or ""
            if re.search(r"year|fy|fiscal", n, re.I) and inp.get("type") in (None, "text", "number", "search"):
                params[n] = year
                has_year = True
        if has_year:
            yield method, action, params


def list_reports(year, delay, pdf_re):
    """Breadth-first crawl of the APA search pages, submitting year forms."""
    found, seen, queue, n = set(), set(), [("get", u.strip(), None) for u in SEARCH_URLS if u.strip()], 0
    while queue and n < MAX_CRAWL_PAGES:
        method, url, params = queue.pop(0)
        key = (method, url, tuple(sorted((params or {}).items())))
        if key in seen:
            continue
        seen.add(key)
        try:
            time.sleep(delay)
            r = (session.post(url, data=params, timeout=60) if method == "post"
                 else session.get(url, params=params, timeout=60))
            r.raise_for_status()
        except Exception as e:  # noqa: BLE001
            print(f"[reports] {url} {params or ''}: {e}")
            continue
        n += 1
        if n <= 15:
            save_debug(f"reports_{n}_{urlparse(r.url).path}_{urlparse(r.url).query}", r.text)
        if "html" not in r.headers.get("content-type", "html"):
            continue
        links, soup = pdf_links(r.text, r.url, pdf_re)
        found |= links
        for f in form_requests(soup, r.url, year):
            queue.append(f)
        for a in soup.find_all("a", href=True):
            u = urljoin(r.url, a["href"].strip())
            pu = urlparse(u)
            if pu.netloc not in CRAWL_HOSTS or not re.search(CRAWL_PATH_REGEX, pu.path):
                continue
            if pu.path.lower().endswith(".pdf"):
                continue
            # Only follow links tied to the year or that look like pagination/next pages.
            text = a.get_text(" ", strip=True).lower()
            if year in u or re.search(r"next|page|more|»|>", text) or re.search(r"page=|offset=|start=", pu.query):
                queue.append(("get", u.split("#")[0], None))
    print(f"[reports] crawled {n} pages: {len(found)} PDFs")
    return found


def list_urls_file(pdf_re):
    if not URLS_FILE.exists():
        return set()
    urls = {ln.strip() for ln in URLS_FILE.read_text().splitlines() if ln.strip() and not ln.startswith("#")}
    print(f"[urls_file] {len(urls)} URLs")
    return urls


def filename_of(url):
    return unquote(Path(urlparse(url).path).name)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--year", default=YEAR)
    ap.add_argument("--strategy", default="auto", choices=["auto", "dirlist", "reports", "urls_file"])
    ap.add_argument("--max-files", type=int, default=0, help="download at most N files this call (0 = no limit)")
    ap.add_argument("--delay", type=float, default=1.5, help="seconds between requests")
    ap.add_argument("--retries", type=int, default=3)
    ap.add_argument("--relist", action="store_true", help="rebuild file_list.csv even if it exists")
    ap.add_argument("--list-only", action="store_true")
    a = ap.parse_args()

    pdf_re = re.compile(PDF_URL_REGEX.format(year=re.escape(a.year)), re.I)

    rows = read_csv(FILE_LIST)
    if a.relist or not rows:
        urls = set()
        if a.strategy in ("auto", "dirlist"):
            urls |= list_dirlist(a.year, a.delay, pdf_re)
        if a.strategy in ("auto", "reports") and (a.strategy == "reports" or not urls):
            urls |= list_reports(a.year, a.delay, pdf_re)
        if a.strategy in ("auto", "urls_file"):
            urls |= list_urls_file(pdf_re)
        known = {r["url"] for r in rows}
        rows += [{"filename": filename_of(u), "url": u} for u in sorted(urls) if u not in known]
        # De-duplicate on filename
        rows = list({r["filename"]: r for r in rows}.values())
        rows.sort(key=lambda r: r["filename"].lower())
        write_csv(FILE_LIST, rows, ["filename", "url"])
        print(f"file_list.csv: {len(rows)} PDFs")
    if not rows:
        print("ERROR: no PDFs listed. Inspect output/listing_debug/ and adjust the CONFIG block, "
              "or put URLs in config/audit_urls.txt.")
        print("downloaded=0 remaining=0")
        return
    if a.list_only:
        return

    processed = {r["filename"] for r in read_csv(MANIFEST)}
    failed_this_run = {r["filename"] for r in read_csv(DOWNLOAD_ERRORS) if r.get("run_id") == RUN_ID}
    AUDITS.mkdir(exist_ok=True)
    todo = [r for r in rows if r["filename"] not in processed and r["filename"] not in failed_this_run]
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
                resp = get(r["url"], a.delay if attempt == 1 else a.delay * 2 * attempt, stream=True)
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
