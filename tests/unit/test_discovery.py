import requests

from app.agents import discovery
from app.config import safe_join, validate_ticker
from app.services.vietstock import DownloadError, HttpClient, SourceUnavailable, parse_document_links
from app.storage.database import Database

import pytest


class Resp:
    def __init__(self, status=200, body=b"", headers=None, fail_after=None):
        self.status_code, self._b, self.headers = status, body, headers or {}
        self.fail_after = fail_after

    @property
    def text(self):
        return self._b.decode("utf-8", "ignore")

    def iter_content(self, n):
        step = min(n, 4096)
        for i in range(0, len(self._b), step):
            if self.fail_after is not None and i >= self.fail_after:
                raise requests.ConnectionError("dropped")
            yield self._b[i:i + step]


class FakeSession:
    def __init__(self, routes):
        self.routes, self.headers, self.calls = routes, {}, []

    def request(self, method, url, timeout=None, stream=False, headers=None, **kw):
        self.calls.append((url, dict(headers or {})))
        r = self.routes.get(url)
        if r is None:
            return Resp(404)
        return r(headers or {}) if callable(r) else r


def client(routes):
    return HttpClient(retries=2, backoff=0, min_interval=0, session=FakeSession(routes), sleep=lambda s: None), routes


PDF_HDR = {"Content-Type": "application/pdf"}


def pdf_resp(path):
    return Resp(200, path.read_bytes(), PDF_HDR)


VS_PAGE = lambda links: ("<html>" + "".join(f'<a href="{u}">{t}</a>' for u, t in links) + "</html>").encode()


def test_ticker_validation_and_path_safety(tmp_path):
    assert validate_ticker(" fpt ") == "FPT"
    for bad in ("", "F", "FPT/../x", "TOOLONGTICKER", "F P"):
        with pytest.raises(ValueError):
            validate_ticker(bad)
    with pytest.raises(ValueError):
        safe_join(tmp_path, "..", "etc")


def test_parse_vietstock_links():
    html = VS_PAGE([("/files/abc_bctn_2024.pdf", "Báo cáo thường niên 2024"), ("/files/other.pdf", "Điều lệ"),
                    ("http://x/y.html", "Báo cáo thường niên 2023")]).decode()
    c = parse_document_links(html, "ABC", "https://finance.vietstock.vn/ABC/tai-lieu.htm")
    assert len(c) == 1 and c[0].year == 2024 and c[0].url.startswith("https://finance.vietstock.vn/files/")


def test_discovery_downloads_validates_and_caches(settings, pdfs):
    routes = {"https://finance.vietstock.vn/ABC/tai-lieu.htm": Resp(200, VS_PAGE([("https://d/abc24.pdf", "Báo cáo thường niên 2024")])),
              "https://d/abc24.pdf": pdf_resp(pdfs["ABC_2024.pdf"])}
    http, _ = client(routes)
    db = Database(settings.db_path)
    docs, missing, warns = discovery.run(settings, db, "ABC", [2024], http=http)
    d = docs[2024]
    assert d.validation_status == "VERIFIED" and d.source_category == "VIETSTOCK" and len(d.sha256) == 64
    assert d.local_path.endswith("ABC/ABC_2024.pdf") and not missing
    n = len(http.s.calls)
    docs2, _, _ = discovery.run(settings, db, "ABC", [2024], http=http)     # cached: no new network calls
    assert docs2[2024].sha256 == d.sha256 and len(http.s.calls) == n


def test_missing_year_is_reported_not_fabricated(settings, pdfs):
    routes = {"https://finance.vietstock.vn/ABC/tai-lieu.htm": Resp(200, VS_PAGE([("https://d/abc24.pdf", "Báo cáo thường niên 2024")])),
              "https://d/abc24.pdf": pdf_resp(pdfs["ABC_2024.pdf"])}
    http, _ = client(routes)
    docs, missing, _ = discovery.run(settings, Database(settings.db_path), "ABC", [2023, 2024], http=http)
    assert 2023 in missing and 2023 not in docs and 2024 in docs


def test_source_unavailable_falls_back_to_labelled_local_inbox(settings, pdfs):
    inbox = settings.path(settings.get("sources.inbox_dir"))
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / "ABC_2024.pdf").write_bytes(pdfs["ABC_2024.pdf"].read_bytes())
    http, _ = client({})   # everything 404 -> SourceUnavailable
    docs, missing, warns = discovery.run(settings, Database(settings.db_path), "ABC", [2024], http=http)
    assert docs[2024].source_category == "LOCAL_INBOX" and any("Vietstock unavailable" in w for w in warns)


def test_source_unavailable_and_no_fallback_gives_missing(settings):
    http, _ = client({})
    docs, missing, warns = discovery.run(settings, Database(settings.db_path), "ABC", [2024], http=http)
    assert not docs and 2024 in missing and warns


def test_offline_mode_never_touches_network(settings):
    http, _ = client({})
    discovery.run(settings, Database(settings.db_path), "ABC", [2024], http=http, offline=True)
    assert http.s.calls == []


def test_wrong_year_and_wrong_company_rejected(settings, pdfs):
    c = discovery.resolve_company(settings, "ABC")
    assert discovery.validate_report_pdf(pdfs["WRONGYEAR_2024.pdf"], "ABC", 2024, c)[0] == "REJECTED"
    assert discovery.validate_report_pdf(pdfs["WRONGCO_2024.pdf"], "ABC", 2024, c)[0] == "REJECTED"
    assert discovery.validate_report_pdf(pdfs["ABC_2024.pdf"], "ABC", 2024, c)[0] == "VERIFIED"


def test_scanned_pdf_validation_needs_review(settings, pdfs):
    assert discovery.validate_report_pdf(pdfs["ABC_2021.pdf"], "ABC", 2021, {})[0] == "NEEDS_REVIEW"


def test_corrupt_pdf_rejected(tmp_path):
    p = tmp_path / "x.pdf"
    p.write_bytes(b"%PDF-1.4 garbage")
    assert discovery.validate_report_pdf(p, "ABC", 2024, {})[0] == "REJECTED"


def test_wrong_year_candidate_rejected_during_discovery(settings, pdfs):
    routes = {"https://finance.vietstock.vn/ABC/tai-lieu.htm": Resp(200, VS_PAGE([("https://d/w.pdf", "Báo cáo thường niên 2024")])),
              "https://d/w.pdf": pdf_resp(pdfs["WRONGYEAR_2024.pdf"])}
    http, _ = client(routes)
    docs, missing, warns = discovery.run(settings, Database(settings.db_path), "ABC", [2024], http=http)
    assert 2024 in missing and any("rejected" in w for w in warns)


def test_duplicate_pdfs_deduplicated_by_sha(settings, pdfs):
    data = pdf_resp(pdfs["ABC_2024.pdf"])
    routes = {"https://finance.vietstock.vn/ABC/tai-lieu.htm": Resp(200, VS_PAGE([
        ("https://d/a.pdf", "Báo cáo thường niên 2024"), ("https://d/b.pdf", "Báo cáo thường niên 2024 (bản 2)")])),
        "https://d/a.pdf": data, "https://d/b.pdf": data}
    http, _ = client(routes)
    docs, _, warns = discovery.run(settings, Database(settings.db_path), "ABC", [2024], http=http)
    assert docs[2024].validation_status == "VERIFIED" and any("duplicate PDF" in w for w in warns)


def test_interrupted_download_resumes_with_range(tmp_path, pdfs):
    body = pdfs["ABC_2024.pdf"].read_bytes()
    state = {"n": 0}

    def route(headers):
        state["n"] += 1
        if state["n"] == 1:
            return Resp(200, body, PDF_HDR, fail_after=len(body) // 2)
        start = int(headers["Range"].split("=")[1].rstrip("-"))
        return Resp(206, body[start:], PDF_HDR)
    http, _ = client({"https://d/x.pdf": route})
    http.download("https://d/x.pdf", tmp_path / "x.pdf")
    assert (tmp_path / "x.pdf").read_bytes() == body and state["n"] == 2


def test_html_or_non_pdf_download_refused(tmp_path):
    http, _ = client({"https://d/a": Resp(200, b"<html>login</html>", {"Content-Type": "text/html"}),
                      "https://d/b": Resp(200, b"NOTPDF", {"Content-Type": "application/pdf"})})
    for u in ("https://d/a", "https://d/b"):
        with pytest.raises(DownloadError):
            http.download(u, tmp_path / "o.pdf")
    assert not (tmp_path / "o.pdf").exists()


def test_403_is_not_retried_and_no_bypass():
    http, _ = client({"https://d/x": Resp(403)})
    with pytest.raises(SourceUnavailable):
        http.get_text("https://d/x")
    assert len(http.s.calls) == 1


def test_company_history_supports_rename_and_exchange_migration(settings):
    settings.companies["ABC"] = {"name": "New Name", "exchange": "HOSE",
                                 "history": [{"from_year": 2018, "to_year": 2020, "name": "Old Name", "exchange": "HNX"}]}
    assert discovery.resolve_company(settings, "ABC", 2019)["exchange"] == "HNX"
    assert discovery.resolve_company(settings, "ABC", 2024)["name"] == "New Name"
