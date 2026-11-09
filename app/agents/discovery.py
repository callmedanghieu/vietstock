"""Agent 1 - Annual Report Discovery: resolve company, find, download, validate and cache reports."""
from __future__ import annotations

import logging
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname

from ..config import Settings, safe_join, validate_ticker
from ..models import ReportDoc
from ..services.pdf_parser import sha256_file
from ..services.vietstock import (Candidate, DownloadError, HttpClient, SourceUnavailable, Vietstock,
                                  load_manual_sources)
from ..storage.database import Database
from ..utils import fold

log = logging.getLogger(__name__)


def resolve_company(settings: Settings, ticker: str, year: int | None = None) -> dict:
    """Registry lookup with optional history (renames / exchange migrations / ticker changes)."""
    c = dict(settings.companies.get(ticker, {}))
    for h in c.get("history", []) or []:
        if year is not None and h.get("from_year", 0) <= year <= h.get("to_year", 9999):
            c.update({k: v for k, v in h.items() if k in ("name", "exchange", "aliases", "former_ticker")})
    c.setdefault("name", None)
    c.setdefault("exchange", "UNKNOWN")
    return c


def validate_report_pdf(path: Path, ticker: str, year: int, company: dict) -> tuple[str, list[str]]:
    """-> (VERIFIED | NEEDS_REVIEW | REJECTED, notes). Verifies PDF integrity, company identity and fiscal year."""
    import fitz
    notes: list[str] = []
    try:
        doc = fitz.open(path)
        n = len(doc)
        text = "\n".join(doc[i].get_text("text") for i in range(min(5, n)))
        doc.close()
    except Exception as e:
        return "REJECTED", [f"corrupt/unreadable PDF: {e}"]
    if n == 0:
        return "REJECTED", ["PDF has no pages"]
    if len(text.strip()) < 100:
        return "NEEDS_REVIEW", ["no text layer on first pages: identity/year cannot be machine-verified"]
    f = fold(text)
    names = [company.get("name")] + list(company.get("aliases") or [])
    ident = bool(re.search(rf"\b{re.escape(ticker.casefold())}\b", f)) or any(n_ and fold(n_) in f for n_ in names)
    m = re.search(r"ma (?:chung khoan|ck|co phieu)\s*[:\-]?\s*([a-z0-9]{3,4})\b", f)
    if m and m.group(1).upper() != ticker:
        return "REJECTED", [f"wrong company: cover states security code {m.group(1).upper()}"]
    years = {int(y) for y in re.findall(r"\b(20[0-3]\d)\b", f)}
    labelled = {int(y) for y in re.findall(r"(?:bao cao thuong nien|annual report|nam)\s*(?:nam\s*)?(20[0-3]\d)", f)}
    if labelled and year not in labelled and year not in years:
        return "REJECTED", [f"wrong year: document labelled {sorted(labelled)}, expected {year}"]
    status = "VERIFIED"
    if not ident:
        status, notes = "NEEDS_REVIEW", notes + ["ticker/company name not found on first pages"]
    if year not in years:
        status, notes = "NEEDS_REVIEW", notes + [f"fiscal year {year} not found on first pages"]
    if labelled and year not in labelled:
        status, notes = "NEEDS_REVIEW", notes + [f"first pages also label year(s) {sorted(labelled)}"]
    return status, notes


def _candidates(settings: Settings, ticker: str, vs: Vietstock | None, offline: bool, warnings: list[str]):
    """Priority: Vietstock (primary) -> manual/IR URLs (secondary, labelled) -> local inbox (labelled)."""
    cands: list[Candidate] = []
    if vs is not None and not offline:
        try:
            cands += vs.list_candidates(ticker)
        except SourceUnavailable as e:
            warnings.append(f"Vietstock unavailable: {e}")
    cands += load_manual_sources(settings.path(settings.get("sources.manual_sources_csv")), ticker)
    inbox = settings.path(settings.get("sources.inbox_dir"))
    if inbox.exists():
        for p in sorted(inbox.glob(f"{ticker}_*.pdf")):
            m = re.fullmatch(rf"{ticker}_(20\d\d)\.pdf", p.name)
            if m:
                cands.append(Candidate(ticker=ticker, year=int(m.group(1)), url=p.as_uri(), source_category="LOCAL_INBOX",
                                       title=p.name))
    return cands


def run(settings: Settings, db: Database, ticker: str, years: list[int], http: HttpClient | None = None,
        offline: bool = False, force: bool = False) -> tuple[dict[int, ReportDoc], dict[int, str], list[str]]:
    """-> (docs by year, missing{year: reason}, warnings)."""
    ticker = validate_ticker(ticker)
    warnings: list[str] = []
    h = settings.get("sources.http", {})
    http = http or HttpClient(h.get("timeout_s", 30), h.get("retries", 3), h.get("backoff_s", 2.0),
                              h.get("min_interval_s", 1.5), h.get("max_bytes", 200 * 1024 * 1024))
    vs = Vietstock(http, settings.get("sources.vietstock_base", "https://finance.vietstock.vn"))
    docs: dict[int, ReportDoc] = {}
    missing: dict[int, str] = {}
    comp = resolve_company(settings, ticker)
    db.upsert_company(ticker, comp["name"], comp["exchange"])
    cands = None
    for y in years:
        c = resolve_company(settings, ticker, y)
        cached = db.get_report(ticker, y)
        if cached and not force and cached.local_path and Path(cached.local_path).exists() \
                and cached.validation_status != "REJECTED" and sha256_file(Path(cached.local_path)) == cached.sha256:
            docs[y] = cached
            continue
        if cands is None:
            cands = _candidates(settings, ticker, vs, offline, warnings)
        pool = [x for x in cands if x.year == y] + [x for x in cands if x.year is None]
        if not pool:
            missing[y] = "no candidate report found in any source (nothing fabricated)"
            continue
        dest = safe_join(settings.reports_dir, ticker, f"{ticker}_{y}.pdf")
        best: ReportDoc | None = None
        seen_sha: dict[str, str] = {}
        for cand in pool:
            tmp = dest.with_name(f"{dest.stem}.cand{len(seen_sha)}.pdf")
            try:
                if cand.url.startswith("file://"):
                    tmp.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(url2pathname(urlparse(cand.url).path), tmp)
                else:
                    http.download(cand.url, tmp)
            except (SourceUnavailable, DownloadError, OSError) as e:
                warnings.append(f"{ticker} {y}: download failed ({cand.source_category}): {e}")
                tmp.unlink(missing_ok=True)
                continue
            sha = sha256_file(tmp)
            if sha in seen_sha:
                tmp.unlink(missing_ok=True)
                warnings.append(f"{ticker} {y}: duplicate PDF (same SHA-256) at {cand.url} ignored")
                continue
            status, notes = validate_report_pdf(tmp, ticker, y, c)
            if status == "REJECTED":
                warnings.append(f"{ticker} {y}: candidate rejected - {'; '.join(notes)}")
                tmp.unlink(missing_ok=True)
                continue
            seen_sha[sha] = cand.url
            doc = ReportDoc(ticker=ticker, company_name=c["name"], exchange=c["exchange"], fiscal_year=y,
                            source_url=cand.url, source_category=cand.source_category,
                            publication_date=cand.publication_date,
                            downloaded_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                            local_path=str(tmp), sha256=sha, validation_status=status, validation_notes=notes)
            if best is None or (best.validation_status != "VERIFIED" and status == "VERIFIED"):
                if best and best.local_path != doc.local_path:
                    Path(best.local_path).unlink(missing_ok=True)
                best = doc
            else:
                Path(doc.local_path).unlink(missing_ok=True)
                best.validation_notes.append(f"DUPLICATE_CANDIDATE ignored: {cand.url}")
        if best is None:
            missing[y] = "all candidates failed download or validation"
            continue
        if len(seen_sha) > 1:
            best.validation_notes.append(f"{len(seen_sha)} distinct PDFs found for year; best kept, others discarded")
            best.validation_status = "NEEDS_REVIEW" if best.validation_status == "VERIFIED" else best.validation_status
        Path(best.local_path).replace(dest)
        best.local_path = str(dest)
        db.upsert_report(best)
        docs[y] = best
    return docs, missing, warnings
