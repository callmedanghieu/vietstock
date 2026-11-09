from datetime import date
from decimal import Decimal as D

import openpyxl
import pytest

from app.agents import outstanding_shares as osh
from app.agents.verification import verify_shares
from app.models import (AggregateOwnership, Independence, MemberObservation, OutstandingShares, Status)
from app.services import calculations as C
from app.services import excel_export as X
from app.storage.database import Database


# ---------------- outstanding shares
def test_last_trading_day_avoids_weekends_and_holidays():
    assert osh.last_trading_day(2022) == date(2022, 12, 30)      # Dec 31 2022 is Saturday
    assert osh.last_trading_day(2023) == date(2023, 12, 29)      # Sunday
    assert osh.last_trading_day(2024) == date(2024, 12, 31)
    assert osh.last_trading_day(2024, {date(2024, 12, 31)}) == date(2024, 12, 30)


def write_csv(settings, rows):
    p = settings.path(settings.get("outstanding_shares.csv_path"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("ticker,year,outstanding_shares,issued_shares,treasury_shares,source,source_category,effective_date,adjustments\n"
                 + "\n".join(rows), encoding="utf-8")


def test_missing_historical_shares_incomplete_not_invented(settings):
    obs, tasks = osh.run(settings, "ABC", [2024])
    assert obs[0].outstanding_shares is None and obs[0].verification_status == Status.INCOMPLETE and tasks


def test_verified_when_source_approved_and_effective_date_matches(settings):
    write_csv(settings, ["ABC,2024,1000000,1000000,0,Vietstock history,VIETSTOCK,2024-12-31,"])
    o = osh.run(settings, "ABC", [2024])[0][0]
    assert o.verification_status == Status.VERIFIED and o.outstanding_shares == 1_000_000


def test_treasury_adjustment_consistency(settings):
    write_csv(settings, ["ABC,2024,950000,1000000,50000,Company,COMPANY,2024-12-31,buyback",
                         "ABC,2023,1000000,1000000,50000,Company,COMPANY,2023-12-29,"])
    obs, _ = osh.run(settings, "ABC", [2023, 2024])
    by = {o.reporting_year: o for o in obs}
    assert by[2024].verification_status == Status.VERIFIED
    assert by[2023].verification_status == Status.NEEDS_REVIEW and any("issued - treasury" in n for n in by[2023].review_notes)


def test_current_share_count_substituted_for_historical_is_flagged(settings):
    write_csv(settings, ["ABC,2020,2000000,2000000,0,Vietstock today,VIETSTOCK,2026-11-09,"])
    o = osh.run(settings, "ABC", [2020])[0][0]
    assert o.verification_status == Status.NEEDS_REVIEW and any("final trading date" in n for n in o.review_notes)


def test_unapproved_source_not_verified(settings):
    write_csv(settings, ["ABC,2024,1000,1000,0,random blog,BLOG,2024-12-31,"])
    assert osh.run(settings, "ABC", [2024])[0][0].verification_status == Status.NEEDS_REVIEW


def test_stock_issuance_and_split_need_explicit_adjustments_note(settings):
    write_csv(settings, ["ABC,2024,2000000,2000000,0,Exchange,EXCHANGE,2024-12-31,2:1 split; issuance 100000 in Aug"])
    o = osh.run(settings, "ABC", [2024])[0][0]
    assert o.verification_status == Status.VERIFIED and "split" in o.adjustments


# ---------------- database
def mem(name="Nguyễn Văn An", body="HDQT", **kw):
    d = dict(ticker="ABC", reporting_year=2024, person_name=name, governance_body=body, reported_title="Thành viên HĐQT",
             active_at_cutoff=True, personal_shares_owned=100, personal_ownership_percentage=D("1.0"),
             membership_effective_date="2025-03-17", ownership_effective_date="2025-03-17",
             verification_status=Status.NEEDS_REVIEW)
    d.update(kw)
    return MemberObservation(**d)


def test_reprocessing_is_idempotent(settings):
    db = Database(settings.db_path)
    for _ in range(3):
        db.replace_extraction("ABC", 2024, [mem(), mem("Lê Văn Cường")], [])
    assert len(db.raw_members("ABC", 2024)) == 2


def test_manual_correction_recorded_raw_untouched(settings):
    db = Database(settings.db_path)
    m = mem(personal_ownership_percentage=None, personal_shares_owned=None)
    db.replace_extraction("ABC", 2024, [m], [])
    db.add_correction(m.key, "personal_ownership_percentage", "0.75", "reviewer1", "page 42 shows 0,75%")
    assert db.raw_members("ABC", 2024)[0].personal_ownership_percentage is None          # raw unchanged
    eff = db.effective_members("ABC", 2024)[0]
    assert eff.personal_ownership_percentage == D("0.75") and eff.verification_status == Status.VERIFIED
    assert eff.corrected_fields["personal_ownership_percentage"]["raw"] == "None"
    row = db.corrections("ABC", 2024)[0]
    assert row["reviewer"] == "reviewer1" and row["created_at"] and "page 42" in row["justification"]


def test_correction_requires_reviewer_and_justification_and_known_field(settings):
    db = Database(settings.db_path)
    m = mem()
    db.replace_extraction("ABC", 2024, [m], [])
    with pytest.raises(ValueError):
        db.add_correction(m.key, "personal_shares_owned", "5", "", "x")
    with pytest.raises(ValueError):
        db.add_correction(m.key, "person_name", "x", "r", "j")
    with pytest.raises(KeyError):
        db.add_correction("ABC:2024:HDQT:nobody", "personal_shares_owned", "5", "r", "j")


def test_correction_survives_reextraction_and_triggers_recalculation(settings):
    db = Database(settings.db_path)
    m = mem("Lý Văn Khoa", "BKS", personal_shares_owned=None, personal_ownership_percentage=None)
    db.replace_extraction("ABC", 2024, [m], [])
    db.add_correction(m.key, "personal_shares_owned", "0", "rev", "annual report p.9 states 0 shares")
    db.replace_extraction("ABC", 2024, [m], [])        # reprocess: raw regenerated, correction still layered
    eff = db.effective_members("ABC", 2024)[0]
    assert eff.zero_ownership_confirmed is True and eff.zero_ownership_basis == "ZERO_SHARES"
    assert C.bks_zero_ratio([eff]).value == 100


# ---------------- excel
def build(settings, bks_absent=False):
    ms = [mem("A An", title=None, reported_title="Thành viên HĐQT độc lập", independence=Independence.TRUE, verification_status=Status.VERIFIED),
          mem("B Binh", verification_status=Status.VERIFIED, personal_ownership_percentage=D("2.5"))]
    bk = [mem("C Cuong", "BKS", personal_shares_owned=0, personal_ownership_percentage=None, verification_status=Status.VERIFIED)]
    for b in bk:
        b.zero_ownership_confirmed, b.zero_ownership_basis, b.ownership_information_status, _ = C.classify_zero_ownership(0, None)
    res = C.compute_company_year("ABC", 2024, ms + bk, [], False, settings.research)
    from app.services.scoring import score_company_year
    score_company_year(settings.scoring, res)
    wb = X.build_workbook("ABC", [2023, 2024], {"name": "ABC Corp", "exchange": "HOSE"}, {}, {2023: "no report found"},
                          {2024: ms + bk}, {2024: []}, {2024: res}, {}, [], settings.scoring)
    return wb, res


def test_workbook_sheet_names_and_layout(settings):
    wb, _ = build(settings)
    assert wb.sheetnames == X.SHEETS
    for ws in wb.worksheets:
        assert ws.freeze_panes == "A2" and ws.auto_filter.ref


def test_summary_uses_live_formulas_and_percent_formats(settings):
    wb, res = build(settings)
    ws = wb["Summary"]
    r = 3   # 2024
    assert ws.cell(r, 4).value == 2024
    assert str(ws.cell(r, 5).value).startswith("=COUNTIFS('HĐQT Members'!")
    assert ws.cell(r, 7).value == f'=IF(E{r}>0,F{r}/E{r},"")'
    assert ws.cell(r, 7).number_format.endswith("%")
    assert ws.cell(r, 14).value.startswith("=SUMIFS(") and ws.cell(r, 15).value == pytest.approx(0.035)
    assert wb.calculation.fullCalcOnLoad is True


def test_missing_year_row_has_no_fabricated_values(settings):
    wb, _ = build(settings)
    ws = wb["Summary"]
    row = [c.value for c in ws[2]]
    assert row[3] == 2023 and all(row[i] is None for i in range(4, 21))
    assert row[21] == "UNAVAILABLE" and "no report found" in row[25]


def test_scoring_pending_shown_not_zero(settings):
    wb, _ = build(settings)
    ws = wb["Summary"]
    assert ws.cell(3, 18).value == "PENDING_RUBRIC" and ws.cell(3, 21).value == "PENDING_RUBRIC"
    rub = [[c.value for c in r] for r in wb["Scoring Rubric"].iter_rows(min_row=2)]
    assert any("PENDING_RUBRIC" in str(x) for r in rub for x in r)


def test_member_sheets_content_and_status_colors(settings):
    wb, _ = build(settings)
    h = wb["HĐQT Members"]
    assert h.max_row == 3 and h.cell(2, 5).value == "TRUE" and h.cell(3, 10).value == pytest.approx(0.025)
    assert h.cell(2, 17).fill.fgColor.rgb.endswith("C6EFCE")
    b = wb["BKS Members"]
    assert b.cell(2, 10).value == "Yes" and b.cell(2, 11).value == "ZERO_SHARES"


def test_workbook_saves_and_reloads(settings, tmp_path):
    wb, _ = build(settings)
    p = X.save(wb, tmp_path / "o" / "ABC_Corporate_Governance_2023_2024.xlsx")
    assert openpyxl.load_workbook(p).sheetnames == X.SHEETS
