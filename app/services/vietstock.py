"""HTTP access (retries, timeouts, rate limit, resumable download) and the Vietstock report listing.

IMPORTANT: the Vietstock page parsing below is BEST EFFORT and has not been validated against the live site
from the development sandbox (outbound access to vietstock.vn was blocked). Vietstock may load document lists via
authenticated/AJAX endpoints; we never bypass authentication. If listing yields nothing, discovery falls back
to clearly-labelled secondary sources (manual URL CSV, local inbox).
"""
from __future__ import annotations

import csv
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)
UA = "viet-governance-ai/0.1 (academic research; respects robots and rate limits)"


class SourceUnavailable(Exception):
    pass


class DownloadError(Exception):
    pass


@dataclass
class Candidate:
    ticker: str
    year: int | None
    url: str
    title: str = ""
    publication_date: str | None = None
    source_category: str = "VIETSTOCK"


class HttpClient:
    def __init__(self, timeout=30, retries=3, backoff=2.0, min_interval=1.5, max_bytes=200 * 1024 * 1024,
                 session=None, sleep=time.sleep):
        self.timeout, self.retries, self.backoff = timeout, retries, backoff
        self.min_interval, self.max_bytes = min_interval, max_bytes
        self.s = session or requests.Session()
        self.s.headers.update({"User-Agent": UA, "Accept-Language": "vi,en;q=0.8"})
        self._last = 0.0
        self._sleep = sleep

    def _throttle(self):
        wait = self.min_interval - (time.monotonic() - self._last)
        if wait > 0:
            self._sleep(wait)
        self._last = time.monotonic()

    def _request(self, method, url, **kw):
        err = None
        for attempt in range(self.retries):
            self._throttle()
            try:
                r = self.s.request(method, url, timeout=self.timeout, **kw)
                if r.status_code in (429, 500, 502, 503, 504):
                    err = f"HTTP {r.status_code}"
                    ra = r.headers.get("Retry-After")
                    self._sleep(float(ra) if ra and ra.isdigit() else self.backoff * 2 ** attempt)
                    continue
                if r.status_code in (401, 403, 404):
                    raise SourceUnavailable(f"{url}: HTTP {r.status_code} (not retried; no access-control bypass)")
                return r
            except (requests.ConnectionError, requests.Timeout) as e:
                err = str(e)[:200]
                self._sleep(self.backoff * 2 ** attempt)
        raise SourceUnavailable(f"{url}: failed after {self.retries} attempts: {err}")

    def get_text(self, url: str) -> str:
        return self._request("GET", url).text

    def download(self, url: str, dest: Path) -> Path:
        """Resumable download to dest (.part kept between attempts). Validates type, size, %PDF magic."""
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_suffix(dest.suffix + ".part")
        for attempt in range(self.retries):
            have = part.stat().st_size if part.exists() else 0
            headers = {"Range": f"bytes={have}-"} if have else {}
            r = self._request("GET", url, stream=True, headers=headers)
            ctype = (r.headers.get("Content-Type") or "").lower()
            if "html" in ctype or "json" in ctype:
                raise DownloadError(f"{url}: unexpected content-type {ctype}")
            if r.status_code == 200 and have:
                have = 0
                part.unlink(missing_ok=True)
            try:
                with open(part, "ab" if have and r.status_code == 206 else "wb") as f:
                    size = have if r.status_code == 206 else 0
                    for chunk in r.iter_content(1 << 16):
                        size += len(chunk)
                        if size > self.max_bytes:
                            part.unlink(missing_ok=True)
                            raise DownloadError(f"{url}: exceeds max size {self.max_bytes}")
                        f.write(chunk)
                break
            except (requests.ConnectionError, requests.Timeout, requests.exceptions.ChunkedEncodingError):
                self._sleep(self.backoff * 2 ** attempt)   # interrupted: loop resumes from .part
        else:
            raise DownloadError(f"{url}: interrupted repeatedly")
        with open(part, "rb") as f:
            if f.read(5) != b"%PDF-":
                part.unlink(missing_ok=True)
                raise DownloadError(f"{url}: not a PDF (bad magic)")
        part.replace(dest)
        return dest


_YEAR_RX = re.compile(r"(20[0-3]\d)")


def guess_year(*texts: str) -> int | None:
    for t in texts:
        ys = _YEAR_RX.findall(t or "")
        if ys:
            return int(ys[0])
    return None


class Vietstock:
    """Lists annual-report PDF links from the public company-documents page (best effort)."""

    def __init__(self, http: HttpClient, base="https://finance.vietstock.vn"):
        self.http, self.base = http, base.rstrip("/")
        self._down: str | None = None

    def list_candidates(self, ticker: str) -> list[Candidate]:
        if self._down:
            raise SourceUnavailable(self._down)
        url = f"{self.base}/{ticker}/tai-lieu.htm"
        try:
            html = self.http.get_text(url)
        except SourceUnavailable as e:
            self._down = str(e)          # circuit breaker: do not hammer an unavailable source
            raise
        return parse_document_links(html, ticker, url)


def parse_document_links(html: str, ticker: str, page_url: str) -> list[Candidate]:
    soup = BeautifulSoup(html, "html.parser")
    out, seen = [], set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        title = a.get_text(" ", strip=True)
        if ".pdf" not in href.lower():
            continue
        if not re.search(r"thường niên|thuong nien|annual", f"{title} {href}", re.I):
            continue
        u = urljoin(page_url, href)
        if urlparse(u).scheme not in ("http", "https") or u in seen:
            continue
        seen.add(u)
        out.append(Candidate(ticker=ticker, year=guess_year(title, href), url=u, title=title))
    return out


def load_manual_sources(path: Path, ticker: str) -> list[Candidate]:
    """CSV columns: ticker,year,url,source_category,publication_date  (user-supplied secondary sources)."""
    if not path.exists():
        return []
    out = []
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if (r.get("ticker") or "").strip().upper() == ticker and r.get("url"):
                try:
                    y = int(r["year"])
                except (KeyError, ValueError):
                    y = None
                out.append(Candidate(ticker=ticker, year=y, url=r["url"].strip(),
                                     source_category=(r.get("source_category") or "MANUAL").strip().upper(),
                                     publication_date=(r.get("publication_date") or None)))
    return out
