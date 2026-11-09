from datetime import date
from decimal import Decimal as D

from app.agents import governance_extraction as ge
from app.agents.verification import verify_aggregates, verify_members
from app.config import load_settings
from app.models import Independence, Status
from tests.conftest import HDR, bks_page, hdqt_page, make_doc


def run(pages, settings, year=2024):
    return ge.extract(settings, "ABC", year, make_doc(pages))


def row(n, name, title, sh="", pct="", ap="", dep=""):
    return [str(n), name, title, sh, pct, ap, dep]


def by_name(res, name):
    return next(m for m in res.members if m.person_name == name)


def test_independent_director_explicit(settings):
    r = run([hdqt_page([row(1, "Trần Thị Bình", "Thành viên HĐQT độc lập", "0", "0,00%"),
                        row(2, "Lê Văn Cường", "Thành viên HĐQT", "5", "0,1%")])], settings)
    assert by_name(r, "Trần Thị Bình").independence == Independence.TRUE
    assert by_name(r, "Lê Văn Cường").independence == Independence.UNKNOWN


def test_non_executive_is_not_independent(settings):
    r = run([hdqt_page([row(1, "Lê Văn Cường", "Thành viên HĐQT không điều hành", "5", "0,1%")])], settings)
    assert by_name(r, "Lê Văn Cường").independence != Independence.TRUE


def test_not_independent_wording_is_false_only_when_explicit(settings):
    r = run([hdqt_page([row(1, "Lê Văn Cường", "Thành viên HĐQT không độc lập", "5", "0,1%")])], settings)
    assert by_name(r, "Lê Văn Cường").independence == Independence.FALSE


def test_member_appointed_mid_year_is_active(settings):
    r = run([hdqt_page([row(1, "Hoàng Văn Em", "Thành viên HĐQT", "3", "0,1%", ap="25/04/2024")])], settings)
    m = by_name(r, "Hoàng Văn Em")
    assert m.active_at_cutoff is True and m.appointment_date == date(2024, 4, 25)


def test_member_resigned_before_cutoff_is_inactive(settings):
    r = run([hdqt_page([row(1, "Vũ Văn Giang", "Thành viên HĐQT", "3", "0,1%", dep="25/04/2024")])], settings)
    assert by_name(r, "Vũ Văn Giang").active_at_cutoff is False


def test_member_appointed_after_cutoff_is_inactive(settings):
    r = run([hdqt_page([row(1, "Đỗ Thị Hà", "Thành viên HĐQT", "3", "0,1%", ap="01/06/2025")])], settings)
    assert by_name(r, "Đỗ Thị Hà").active_at_cutoff is False


def test_departure_after_cutoff_still_active_at_cutoff(settings):
    r = run([hdqt_page([row(1, "Vũ Văn Giang", "Thành viên HĐQT", "3", "0,1%", dep="30/06/2025")])], settings)
    assert by_name(r, "Vũ Văn Giang").active_at_cutoff is True


def test_former_members_section_excluded(settings):
    cur = hdqt_page([row(1, "Nguyễn Văn An", "Chủ tịch HĐQT", "1", "0,1%")])
    former = ("Thành viên HĐQT đã miễn nhiệm trong năm 2024:\nVũ Văn Giang", [[HDR, row(1, "Vũ Văn Giang", "Thành viên HĐQT", "9", "0,5%")]])
    r = run([cur, former], settings)
    assert by_name(r, "Vũ Văn Giang").active_at_cutoff is False
    assert by_name(r, "Nguyễn Văn An").active_at_cutoff is True


def test_post_year_end_effective_date_kept_and_attributed_to_report_year(settings):
    r = run([hdqt_page([row(1, "Nguyễn Văn An", "Chủ tịch HĐQT", "1", "0,1%")])], settings, year=2024)
    m = r.members[0]
    assert m.reporting_year == 2024 and m.membership_effective_date == "2025-03-17" == m.ownership_effective_date


def test_date_not_disclosed_is_not_invented(settings):
    pg = ("Hội đồng quản trị\nNguyễn Văn An", [[HDR, row(1, "Nguyễn Văn An", "Chủ tịch HĐQT", "1", "0,1%")]])
    m = run([pg], settings).members[0]
    assert m.membership_effective_date == "Not explicitly disclosed" and m.active_at_cutoff is True
    assert any("not explicitly disclosed" in n for n in m.review_notes)


def test_dates_present_without_composition_date_needs_review(settings):
    pg = ("Hội đồng quản trị\nNguyễn Văn An", [[HDR, row(1, "Nguyễn Văn An", "Chủ tịch HĐQT", "1", "0,1%", dep="01/02/2024")]])
    m = run([pg], settings).members[0]
    assert m.active_at_cutoff is None


def test_fiscal_year_end_policy_is_configurable(tmp_path):
    s = load_settings(data_dir=tmp_path, overrides={"measurement.membership_date_policy": "fiscal_year_end"})
    r = run([hdqt_page([row(1, "Đỗ Thị Hà", "Thành viên HĐQT", "3", "0,1%", ap="01/02/2025")])], s)
    m = r.members[0]
    assert m.active_at_cutoff is False                 # appointed after 31/12/2024
    assert m.membership_effective_date == "2025-03-17"   # disclosed date preserved, not replaced
    assert any("configured cutoff" in n for n in m.review_notes)


def test_same_member_in_two_tables_counted_once(settings):
    a = hdqt_page([row(1, "Nguyễn Văn An", "Chủ tịch HĐQT", "1.000", "2%")])
    b = hdqt_page([row(1, "Nguyễn Văn An", "Chủ tịch HĐQT", "1.000", "2%")], date_text="Sở hữu của HĐQT tại ngày 17/03/2025")
    r = run([a, b], settings)
    assert len([m for m in r.members if m.person_name == "Nguyễn Văn An"]) == 1


def test_conflicting_ownership_tables_flagged_not_overwritten(settings):
    a = hdqt_page([row(1, "Nguyễn Văn An", "Chủ tịch HĐQT", "1.000", "2%")])
    b = hdqt_page([row(1, "Nguyễn Văn An", "Chủ tịch HĐQT", "2.000", "4%")], date_text="Sở hữu của HĐQT tại ngày 17/03/2025")
    m = run([a, b], settings).members[0]
    assert m.personal_shares_owned == 1000 and any("conflicting ownership" in n for n in m.review_notes)


def test_missing_appointment_date_does_not_fabricate_one(settings):
    m = run([hdqt_page([row(1, "Nguyễn Văn An", "Chủ tịch HĐQT", "1", "0,1%")])], settings).members[0]
    assert m.appointment_date is None and m.departure_date is None


# ---------- BKS
def bks(rows, settings):
    r = run([bks_page(rows)], settings)
    return {m.person_name: m for m in r.members}


def test_bks_exactly_zero_shares(settings):
    m = bks([row(1, "Nguyễn Thị Hoa", "Thành viên BKS", "0", "")], settings)["Nguyễn Thị Hoa"]
    assert (m.zero_ownership_confirmed, m.zero_ownership_basis) == (True, "ZERO_SHARES")


def test_bks_zero_percentage_with_unknown_shares(settings):
    m = bks([row(1, "Mai Thị Lan", "Thành viên BKS", "", "0,00%")], settings)["Mai Thị Lan"]
    assert (m.zero_ownership_confirmed, m.zero_ownership_basis, m.personal_shares_owned) == (True, "ZERO_PERCENTAGE", None)


def test_bks_blank_ownership_not_zero(settings):
    m = bks([row(1, "Lý Văn Khoa", "Thành viên BKS", "", "")], settings)["Lý Văn Khoa"]
    assert m.zero_ownership_confirmed is None and m.ownership_information_status == Status.UNAVAILABLE


def test_bks_dash_treated_as_missing_not_zero(settings):
    m = bks([row(1, "Lý Văn Khoa", "Thành viên BKS", "-", "-")], settings)["Lý Văn Khoa"]
    assert m.zero_ownership_confirmed is None and m.personal_shares_owned is None


def test_no_supervisory_board_declared(settings):
    pg = ("Công ty áp dụng mô hình không có Ban Kiểm soát; Ủy ban kiểm toán thay thế Ban Kiểm soát.\nHội đồng quản trị tại ngày 31/12/2024",
          [[HDR, row(1, "Nguyễn Văn An", "Chủ tịch HĐQT", "1", "0,1%")]])
    r = run([pg], settings)
    assert r.structure.bks_absent_declared and not any(m.governance_body == "BKS" for m in r.members)


# ---------- ownership categories
def test_related_person_ownership_not_personal(settings):
    hdr = ["STT", "Họ và tên", "Chức vụ", "Số cổ phiếu người có liên quan", "Tỷ lệ"]
    pg = ("Hội đồng quản trị tại ngày 17/03/2025\nNguyễn Văn An", [[hdr, ["1", "Nguyễn Văn An", "Chủ tịch HĐQT", "900.000", "1,8%"]]])
    m = run([pg], settings).members[0]
    assert m.personal_shares_owned is None and m.reported_ownership_category in ("related_person",)


def test_personal_and_related_columns_kept_separate(settings):
    hdr = ["STT", "Họ và tên", "Chức vụ", "Số cổ phiếu sở hữu cá nhân", "Tỷ lệ sở hữu cá nhân", "Số cổ phiếu người có liên quan"]
    pg = ("Hội đồng quản trị tại ngày 17/03/2025\nNguyễn Văn An",
          [[hdr, ["1", "Nguyễn Văn An", "Chủ tịch HĐQT", "1.000", "0,5%", "700.000"]]])
    m = run([pg], settings).members[0]
    assert m.personal_shares_owned == 1000 and m.personal_ownership_percentage == D("0.5")
    assert m.reported_ownership_category == "personal" and any("related_person" in n for n in m.review_notes)


def test_missing_board_member_percentage_stays_none(settings):
    m = run([hdqt_page([row(1, "Nguyễn Văn An", "Chủ tịch HĐQT", "", "")])], settings).members[0]
    assert m.personal_ownership_percentage is None and m.personal_shares_owned is None


def test_percent_unit_is_percentage_value(settings):
    m = run([hdqt_page([row(1, "Nguyễn Văn An", "Chủ tịch HĐQT", "1", "2,5%")])], settings).members[0]
    assert m.personal_ownership_percentage == D("2.5")


# ---------- aggregates
def test_aggregate_total_row_extracted(settings):
    r = run([hdqt_page([row(1, "Nguyễn Văn An", "Chủ tịch HĐQT", "1", "2,5%"), ["", "Tổng cộng", "", "1", "2,5%", "", ""]])], settings)
    assert len(r.aggregates) == 1 and r.aggregates[0].reported_pct == D("2.5") and r.aggregates[0].covers_all_active_board
    assert len(r.members) == 1                      # total row is not a person


def test_narrative_aggregate_extracted(settings):
    pg = ("Hội đồng quản trị\nTổng tỷ lệ sở hữu của các thành viên HĐQT là 5,00% vốn điều lệ tại ngày 17/03/2025.\n", [])
    r = run([pg], settings)
    assert r.aggregates and r.aggregates[0].reported_pct == D("5.00") and r.aggregates[0].effective_date == "2025-03-17"


def test_bks_narrative_not_taken_as_board_aggregate(settings):
    pg = ("Ban kiểm soát nắm giữ 1,2% cổ phần của công ty.\nHội đồng quản trị", [])
    assert not run([pg], settings).aggregates


def test_scanned_pdf_yields_no_data(settings):
    d = make_doc([("", [])])
    d.is_scanned = True
    r = ge.extract(settings, "ABC", 2024, d)
    assert r.status == "UNAVAILABLE" and not r.members


# ---------- text fallback & table continuation
def test_table_spanning_pages_uses_previous_header(settings):
    p1 = hdqt_page([row(1, "Nguyễn Văn An", "Chủ tịch HĐQT", "1", "0,1%")])
    p2 = ("tiếp theo", [[row(2, "Lê Văn Cường", "Thành viên HĐQT", "2", "0,2%")]])
    p2 = ("Hội đồng quản trị (tiếp theo) tại ngày 17/03/2025", p2[1])
    r = run([p1, p2], settings)
    assert {m.person_name for m in r.members} == {"Nguyễn Văn An", "Lê Văn Cường"}


def test_text_fallback_flags_review(settings):
    text = "Hội đồng quản trị tại ngày 31/12/2024\nNguyễn Văn An  Chủ tịch HĐQT  1.000.000  2,00%\n"
    r = run([(text, [])], settings)
    assert len(r.members) == 1 and r.members[0].personal_shares_owned == 1_000_000
    assert any(n.startswith("REVIEW") for n in r.members[0].review_notes)


# ---------- verification agent (without a PDF: deterministic checks only)
def test_verification_marks_clean_members_verified_and_flagged_ones_for_review(settings):
    r = run([hdqt_page([row(1, "Nguyễn Văn An", "Chủ tịch HĐQT", "1", "0,1%"),
                        row(2, "Lê Văn Cường", "Thành viên HĐQT", "5", "150%")])], settings)
    tasks = verify_members(None, r)
    st = {m.person_name: m.verification_status for m in r.members}
    assert st["Nguyễn Văn An"] == Status.VERIFIED and st["Lê Văn Cường"] == Status.NEEDS_REVIEW
    assert any("outside 0-100" in t.ambiguity for t in tasks)


def test_verification_aggregate_not_covering_board_needs_review(settings):
    pg = ("Hội đồng quản trị\nÔng Nguyễn Văn An - Chủ tịch HĐQT nắm giữ 5,00% cổ phần.\n", [])
    r = run([pg], settings)
    verify_aggregates(None, r)
    assert r.aggregates[0].verification_status == Status.NEEDS_REVIEW
