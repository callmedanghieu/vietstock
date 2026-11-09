"""Progressive PDF extraction: native text -> table-aware (relevant pages only) -> OCR if available."""
from __future__ import annotations

import hashlib
import json
import logging
import shutil
import warnings
from pathlib import Path

from ..models import PageData, ParsedDocument
from ..utils import collapse, fold, nfc

log = logging.getLogger(__name__)
warnings.filterwarnings("ignore", category=FutureWarning)

# folded (diacritic-free) keyword sets used to rank pages
KEYWORDS = {
    "HDQT": ["hoi dong quan tri", "hdqt", "board of directors"],
    "BKS": ["ban kiem soat", "bks", "supervisory board"],
    "OWNERSHIP": ["so huu", "so co phieu", "ty le so huu", "co phieu nam giu", "shares owned"],
    "SHARE_CAPITAL": ["von dieu le", "co phieu dang luu hanh", "co phieu quy", "outstanding shares", "treasury"],
}
TABLE_HEADER_HINTS = ["ho va ten", "ho ten", "chuc vu", "full name", "position"]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def tag_page(text: str) -> tuple[list[str], int]:
    f = fold(text)
    tags, score = [], 0
    for tag, kws in KEYWORDS.items():
        hits = sum(f.count(k) for k in kws)
        if hits:
            tags.append(tag)
            score += min(hits, 10) * (3 if tag in ("HDQT", "BKS") else 1)
    if any(h in f for h in TABLE_HEADER_HINTS) and ("HDQT" in tags or "BKS" in tags):
        score += 8
    if "HDQT" in tags and "OWNERSHIP" in tags:
        score += 4
    if "BKS" in tags and "OWNERSHIP" in tags:
        score += 4
    return tags, score


def _clean_table(rows) -> list[list[str]]:
    out = []
    for r in rows or []:
        cells = [collapse((c or "").replace("\n", " ")) for c in r]
        if any(cells):
            out.append(cells)
    return out


def _ocr_available() -> bool:
    return shutil.which("tesseract") is not None


def parse_pdf(path: Path, document_id: str, scanned_threshold: int = 80,
              relevant_limit: int = 40) -> ParsedDocument:
    import fitz
    import pdfplumber

    sha = sha256_file(path)
    warns: list[str] = []
    doc = fitz.open(path)
    pages: list[PageData] = []
    try:
        for i, pg in enumerate(doc, start=1):
            txt = nfc(pg.get_text("text", sort=True) or "")
            pages.append(PageData(page=i, text=txt, scanned=len(txt.strip()) < scanned_threshold))
    finally:
        n = len(doc)
    avg = sum(len(p.text.strip()) for p in pages) / max(n, 1)
    is_scanned = bool(pages) and avg < scanned_threshold
    ocr_used = False
    if is_scanned:
        if _ocr_available():
            try:
                for i, p in enumerate(pages):
                    tp = doc[i].get_textpage_ocr(language="vie+eng", full=True)
                    p.text = nfc(doc[i].get_text("text", textpage=tp, sort=True))
                ocr_used = True
            except Exception as e:  # pragma: no cover - depends on tesseract
                warns.append(f"OCR failed: {e}")
        else:
            warns.append("SCANNED_NO_OCR: PDF has no usable text layer and Tesseract is not installed; "
                         "governance data cannot be extracted automatically (human/vision review required)")
    doc.close()

    for p in pages:
        p.tags, p.score = tag_page(p.text)
    ranked = sorted((p for p in pages if p.score > 0), key=lambda p: -p.score)[:relevant_limit]
    relevant = {p.page for p in ranked}
    if relevant:
        try:
            with pdfplumber.open(path) as pl:
                for p in pages:
                    if p.page not in relevant:
                        continue
                    page = pl.pages[p.page - 1]
                    for strat in ({}, {"vertical_strategy": "text", "horizontal_strategy": "text"}):
                        try:
                            tbls = [_clean_table(t) for t in page.extract_tables(strat or None)]
                        except Exception:
                            tbls = []
                        tbls = [t for t in tbls if len(t) >= 2 and max(map(len, t)) >= 3]
                        if tbls and not strat:
                            p.tables = tbls
                            break
                        if tbls and strat and not p.tables:
                            p.tables = tbls   # text-strategy tables only as fallback
        except Exception as e:
            warns.append(f"Table extraction failed: {e}")
    return ParsedDocument(document_id=document_id, sha256=sha, n_pages=n, is_scanned=is_scanned,
                          ocr_used=ocr_used, pages=pages, warnings=warns)


def load_or_parse(path: Path, document_id: str, cache_dir: Path, **kw) -> ParsedDocument:
    """Cache keyed by file checksum: unchanged PDFs are never re-analysed."""
    sha = sha256_file(path)
    cp = cache_dir / f"{document_id}_{sha[:16]}.json"
    if cp.exists():
        try:
            return ParsedDocument.model_validate_json(cp.read_text(encoding="utf-8"))
        except Exception:
            log.warning("corrupt parse cache %s, re-parsing", cp)
    parsed = parse_pdf(path, document_id, **kw)
    cp.write_text(parsed.model_dump_json(), encoding="utf-8")
    return parsed


def page_text_independent(path: Path, page: int) -> str:
    """Second, independent read of a page via pdfplumber (used by the verification agent)."""
    import pdfplumber
    with pdfplumber.open(path) as pl:
        if page < 1 or page > len(pl.pages):
            return ""
        return nfc(pl.pages[page - 1].extract_text() or "")
