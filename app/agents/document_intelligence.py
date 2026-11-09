"""Agent 2 - Document Intelligence: parse, classify scanned/native, rank relevant pages."""
from __future__ import annotations

from pathlib import Path

from ..config import Settings
from ..models import PageData, ParsedDocument
from ..services.pdf_parser import load_or_parse


def run(settings: Settings, pdf_path: Path, document_id: str) -> ParsedDocument:
    return load_or_parse(
        pdf_path, document_id, settings.extracted_dir,
        scanned_threshold=settings.get("extraction.scanned_chars_per_page_threshold", 80),
        relevant_limit=settings.get("extraction.relevant_page_limit", 40))


def relevant_pages(doc: ParsedDocument, tag: str | None = None) -> list[PageData]:
    ps = [p for p in doc.pages if p.score > 0 and (tag is None or tag in p.tags)]
    return sorted(ps, key=lambda p: p.page)
