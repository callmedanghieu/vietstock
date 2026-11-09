import warnings
from pathlib import Path

import pytest

warnings.filterwarnings("ignore", category=FutureWarning)

from app.config import load_settings  # noqa: E402
from app.models import ParsedDocument, PageData  # noqa: E402
from app.services.pdf_parser import tag_page  # noqa: E402
from tests.fixtures.make_fixtures import make_all  # noqa: E402


@pytest.fixture(scope="session")
def pdfs(tmp_path_factory) -> dict[str, Path]:
    return make_all(tmp_path_factory.mktemp("fx"))


@pytest.fixture
def settings(tmp_path):
    return load_settings(data_dir=tmp_path / "data")


HDR = ["STT", "Họ và tên", "Chức vụ", "Số cổ phiếu sở hữu", "Tỷ lệ sở hữu", "Ngày bổ nhiệm", "Ngày miễn nhiệm"]


def make_doc(pages: list[tuple[str, list[list[list[str]]]]], doc_id="ABC_2024") -> ParsedDocument:
    """pages: [(page_text, [table, ...])] -> ParsedDocument without needing a PDF."""
    out = []
    for i, (text, tables) in enumerate(pages, start=1):
        tags, score = tag_page(text + " " + " ".join(" ".join(c) for t in tables for r in t for c in [r]))
        out.append(PageData(page=i, text=text, tables=tables, tags=tags, score=max(score, 1)))
    return ParsedDocument(document_id=doc_id, sha256="x" * 64, n_pages=len(out), is_scanned=False, pages=out)


def hdqt_page(rows, date_text="Hội đồng quản trị tại ngày 17/03/2025", extra=""):
    return (f"{date_text}\n" + "\n".join(r[1] for r in rows) + extra, [[HDR] + rows])


def bks_page(rows, date_text="Ban Kiểm soát tại ngày 17/03/2025"):
    return (f"{date_text}\n" + "\n".join(r[1] for r in rows), [[HDR] + rows])
