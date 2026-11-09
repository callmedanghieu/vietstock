"""End-to-end tests over SYNTHETIC fixture PDFs (fictional companies). No network, no LLM."""
from decimal import Decimal as D
from pathlib import Path

import openpyxl
import pytest

from app import main as cli
from app.agents import governance_extraction
from app.config import load_settings
from app.models import Status
from app.storage.database import Database
from app.workflow import Workflow
from tests.fixtures.make_fixtures import make_clean_for


def stage(settings, ticker, name, src: Path, year: int):
    inbox = settings.path(settings.get("sources.inbox_dir"))
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / f"{ticker}_{year}.pdf").write_bytes(src.read_bytes())


@pytest.fixture
def wf(settings):
    return Workflow(settings, Database(settings.db_path))


def test_one_company_one_year(wf, settings, pdfs):
    stage(settings, "ABC", "", pdfs["ABC_2024.pdf"], 2024)
    summ = wf.process("ABC", [2024], offline=True)
    assert summ["years"][2024].startswith("OK")
    r = wf.db.get_result("ABC", 2024)
    assert (r.ined.numerator, r.ined.denominator, r.ined.value) == (2, 5, D(40)) and r.ined.status == Status.VERIFIED
    assert (r.bks.numerator, r.bks.denominator) == (2, 3) and r.bks.status == Status.VERIFIED
    assert r.board_own.value == D("4.02") and r.board_own.reported_aggregate == D("4.02")
    assert r.board_own.status == Status.VERIFIED and r.ined.effective_date == "2025-03-17"
    assert r.score_status == "PENDING_RUBRIC" and r.overall_score is None
    # every observation carries a page citation and was verified against the cited page
    ms = wf.db.effective_members("ABC", 2024)
    assert all(m.evidence and m.evidence.page in (2, 3) for m in ms)
    assert all(m.reporting_year == 2024 for m in ms)
    p = wf.export("ABC", [2024])
    assert p.name == "ABC_Corporate_Governance_2024_2024.xlsx"
    ws = openpyxl.load_workbook(p)["Summary"]
    assert ws.cell(2, 22).value == "VERIFIED" and ws.cell(2, 15).value == pytest.approx(0.0402)


def test_eight_years_with_missing_corrupt_scanned_and_noBKS(wf, settings, pdfs, tmp_path):
    for y, name in ((2024, "ABC_2024.pdf"), (2023, "ABC_2023.pdf"), (2022, "ABC_2022.pdf"), (2021, "ABC_2021.pdf")):
        stage(settings, "ABC", "", pdfs[name], y)
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"%PDF-1.4 not really a pdf")
    stage(settings, "ABC", "", bad, 2019)
    summ = wf.process("ABC", list(range(2018, 2026)), offline=True)["years"]
    assert summ[2018].startswith("MISSING_REPORT") and summ[2025].startswith("MISSING_REPORT")
    assert summ[2024].startswith("OK") and summ[2023].startswith("OK") and summ[2022].startswith("OK")
    assert summ[2021].startswith("UNAVAILABLE") or "Scanned" in summ[2021]
    # failure/rejection in one year did not prevent later years
    assert wf.db.get_result("ABC", 2024) is not None
    r22 = wf.db.get_result("ABC", 2022)
    assert r22.bks.status == Status.NOT_APPLICABLE and r22.bks.value is None
    r23 = wf.db.get_result("ABC", 2023)
    assert r23.bks.status == Status.INCOMPLETE and (r23.bks.numerator, r23.bks.denominator) == (1, 2)
    # 2023: Binh's ownership is '-' => missing, sum incomplete, verified aggregate (5.00) resolves
    assert r23.board_own.calculated_sum is None and r23.board_own.reported_aggregate == D("5.00")
    assert r23.board_own.value == D("5.00")
    p = wf.export("ABC", list(range(2018, 2026)))
    wb = openpyxl.load_workbook(p)
    rows = {r[3].value: r for r in wb["Summary"].iter_rows(min_row=2)}
    assert len(rows) == 8 and rows[2018][4].value is None and rows[2018][21].value == "UNAVAILABLE"
    src = {r[1].value: r for r in wb["Sources"].iter_rows(min_row=2)}
    assert "REPORT NOT AVAILABLE" in src[2018][10].value
    assert wb["Review Queue"].max_row > 1


def test_reprocessing_is_idempotent_and_skips_unchanged(wf, settings, pdfs):
    stage(settings, "ABC", "", pdfs["ABC_2024.pdf"], 2024)
    wf.process("ABC", [2024], offline=True)
    n1 = len(wf.db.raw_members("ABC", 2024))
    s2 = wf.process("ABC", [2024], offline=True)
    assert s2["years"][2024].startswith("SKIPPED")
    wf.process("ABC", [2024], offline=True, force=True)
    assert len(wf.db.raw_members("ABC", 2024)) == n1
    cnt = wf.db.conn.execute("SELECT COUNT(*) c FROM member_observations").fetchone()["c"]
    assert cnt == n1


def test_methodology_change_invalidates_cache(wf, settings, pdfs):
    stage(settings, "ABC", "", pdfs["ABC_2024.pdf"], 2024)
    wf.process("ABC", [2024], offline=True)
    settings.research["measurement"]["membership_date_policy"] = "fiscal_year_end"
    s = wf.process("ABC", [2024], offline=True)
    assert s["years"][2024].startswith("OK")                 # re-extracted under new policy
    m = [m for m in wf.db.effective_members("ABC", 2024) if m.person_name == "Vũ Văn Giang"][0]
    assert m.membership_effective_date == "2025-03-17"       # disclosed date still preserved


def test_resume_after_partial_failure(wf, settings, pdfs, monkeypatch):
    stage(settings, "ABC", "", pdfs["ABC_2024.pdf"], 2024)
    stage(settings, "ABC", "", pdfs["ABC_2023.pdf"], 2023)
    real = governance_extraction.extract

    def flaky(s, t, y, d):
        if y == 2023:
            raise RuntimeError("simulated crash")
        return real(s, t, y, d)
    monkeypatch.setattr(governance_extraction, "extract", flaky)
    s1 = wf.process("ABC", [2023, 2024], offline=True)["years"]
    assert s1[2023].startswith("FAILED") and s1[2024].startswith("OK")
    assert wf.db.get_result("ABC", 2024) is not None and wf.db.get_result("ABC", 2023) is None
    monkeypatch.setattr(governance_extraction, "extract", real)
    s2 = wf.process("ABC", [2023, 2024], offline=True)["years"]
    assert s2[2024].startswith("SKIPPED") and s2[2023].startswith("OK")
    assert wf.db.get_result("ABC", 2023) is not None


def test_manual_correction_then_recalculation(wf, settings, pdfs):
    stage(settings, "ABC", "", pdfs["ABC_2023.pdf"], 2023)
    wf.process("ABC", [2023], offline=True)
    before = wf.db.get_result("ABC", 2023)
    assert before.bks.status == Status.INCOMPLETE
    khoa = next(m for m in wf.db.raw_members("ABC", 2023) if m.person_name == "Lý Văn Khoa")
    raw_before = khoa.model_dump_json()
    rc = cli.main(["--data-dir", str(settings.data_dir), "--correct", khoa.key, "personal_shares_owned", "0",
                   "--reviewer", "tester", "--justification", "PDF p.3 states 0 shares"])
    assert rc == 0
    after = wf.db.get_result("ABC", 2023)
    assert after.bks.status == Status.VERIFIED and after.bks.value == D(100)
    assert next(m for m in wf.db.raw_members("ABC", 2023) if m.person_name == "Lý Văn Khoa").model_dump_json() == raw_before
    assert wf.db.corrections("ABC", 2023)[0]["reviewer"] == "tester"


def test_multiple_companies_with_wrong_company_pdf_rejected(wf, settings, pdfs, tmp_path):
    for t in ("AAA", "BBB"):
        p = tmp_path / f"{t}.pdf"
        make_clean_for(t, p)
        stage(settings, t, "", p, 2024)
    stage(settings, "CCC", "", pdfs["ABC_2024.pdf"], 2024)      # CCC_2024.pdf actually belongs to ABC
    out = {t: wf.process(t, [2024], offline=True)["years"][2024] for t in ("AAA", "BBB", "CCC")}
    assert out["AAA"].startswith("OK") and out["BBB"].startswith("OK")
    assert out["CCC"].startswith("MISSING_REPORT")
    assert wf.db.get_result("AAA", 2024).ined.value == D(40) and wf.db.get_result("CCC", 2024) is None


def test_cli_end_to_end_status_unresolved_and_export_only(settings, pdfs, capsys):
    stage(settings, "ABC", "", pdfs["ABC_2023.pdf"], 2023)
    common = ["--data-dir", str(settings.data_dir)]
    rc = cli.main(["--ticker", "abc", "--start-year", "2022", "--end-year", "2023", "--cached-only"] + common)
    assert rc == 1                                           # 2022 report missing -> non-zero exit
    out = capsys.readouterr().out
    assert "ABC 2022: MISSING_REPORT" in out and "wrote" in out
    assert cli.main(["--ticker", "ABC", "--status"] + common) == 0
    assert "extract" in capsys.readouterr().out
    assert cli.main(["--list-unresolved"] + common) == 0
    assert capsys.readouterr().out.strip()
    assert cli.main(["--ticker", "ABC", "--start-year", "2022", "--end-year", "2023", "--export-only"] + common) == 0
    assert cli.main(["--ticker", "A/../B"] + common) == 2
    assert cli.main(["--ticker", "ABC", "--start-year", "2025", "--end-year", "2020"] + common) == 2
    assert cli.main(common) == 2


def test_scanned_report_is_unavailable_never_zero(wf, settings, pdfs):
    stage(settings, "ABC", "", pdfs["ABC_2021.pdf"], 2021)
    wf.process("ABC", [2021], offline=True)
    r = wf.db.get_result("ABC", 2021)
    assert r.ined.value is None and r.ined.status == Status.UNAVAILABLE and r.board_own.value is None
