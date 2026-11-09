from decimal import Decimal as D

import pytest

from app.models import AggregateOwnership, Independence, MemberObservation, Status
from app.services import calculations as C


def mk(name, body="HDQT", ind=Independence.UNKNOWN, active=True, shares=None, pct=None, eff="2025-03-17",
       status=Status.VERIFIED, cat="personal", title="Thành viên"):
    m = MemberObservation(ticker="ABC", reporting_year=2024, person_name=name, governance_body=body,
                          reported_title=title, independence=ind, active_at_cutoff=active,
                          personal_shares_owned=shares, personal_ownership_percentage=None if pct is None else D(pct),
                          membership_effective_date=eff, ownership_effective_date=eff,
                          verification_status=status, reported_ownership_category=cat)
    if body == "BKS":
        m.zero_ownership_confirmed, m.zero_ownership_basis, m.ownership_information_status, _ = \
            C.classify_zero_ownership(m.personal_shares_owned, m.personal_ownership_percentage, cat)
    return m


# ---------------- zero-ownership classification
def test_zero_shares_confirmed():
    assert C.classify_zero_ownership(0, None)[:2] == (True, "ZERO_SHARES")


def test_zero_percentage_confirmed_without_share_count():
    conf, basis, st, _ = C.classify_zero_ownership(None, D("0.00"))
    assert (conf, basis, st) == (True, "ZERO_PERCENTAGE", Status.VERIFIED)


def test_blank_ownership_is_not_zero():
    conf, basis, st, _ = C.classify_zero_ownership(None, None)
    assert conf is None and basis is None and st == Status.UNAVAILABLE


def test_related_person_holdings_never_count():
    assert C.classify_zero_ownership(0, D(0), "related_person")[0] is None


def test_positive_holding_not_zero():
    assert C.classify_zero_ownership(2000, D("0.004"))[0] is False


def test_conflicting_zero_flags_review():
    assert C.classify_zero_ownership(0, D("1.5"))[2] == Status.NEEDS_REVIEW


# ---------------- Indicator 1
def board(n_ind, n_other):
    return [mk(f"Doc Lap {i}", ind=Independence.TRUE, title="Thành viên HĐQT độc lập") for i in range(n_ind)] + \
           [mk(f"Khac Nguoi {i}", title="Thành viên HĐQT") for i in range(n_other)]


def test_ined_ratio_valid():
    r = C.ined_ratio(board(2, 3))
    assert r.value == D(40) and (r.numerator, r.denominator) == (2, 5) and r.status == Status.VERIFIED


def test_non_executive_is_not_independent():
    r = C.ined_ratio([mk("A B", title="Thành viên HĐQT không điều hành"), mk("C D", title="Chủ tịch")])
    assert r.numerator == 0


def test_inactive_members_excluded_from_both_terms():
    ms = board(1, 1) + [mk("Cuu Thanh Vien", ind=Independence.TRUE, active=False, title="Thành viên HĐQT độc lập")]
    r = C.ined_ratio(ms)
    assert (r.numerator, r.denominator) == (1, 2)


def test_duplicate_director_counted_once():
    d = board(1, 1)
    r = C.ined_ratio(d + [d[0].model_copy()])
    assert r.denominator == 2 and any("duplicate" in n for n in r.notes)


def test_ined_no_active_members_is_unavailable_not_zero():
    r = C.ined_ratio([])
    assert r.value is None and r.status == Status.UNAVAILABLE


def test_ined_different_effective_dates_flagged():
    ms = board(1, 1)
    ms[1].membership_effective_date = "2025-04-01"
    assert C.ined_ratio(ms).status == Status.NEEDS_REVIEW


def test_ined_unverified_member_makes_indicator_needs_review():
    ms = board(1, 1)
    ms[0].verification_status = Status.NEEDS_REVIEW
    assert C.ined_ratio(ms).status == Status.NEEDS_REVIEW


def test_independent_flag_requires_title_support():
    m = mk("A B", ind=Independence.TRUE, title="Thành viên HĐQT")
    assert C.ined_ratio([m, mk("C D")]).status == Status.NEEDS_REVIEW


# ---------------- Indicator 2
def test_bks_ratio_mixed_bases():
    ms = [mk("A", "BKS", shares=0), mk("B", "BKS", pct="0.00"), mk("C", "BKS", shares=100, pct="0.001")]
    r = C.bks_zero_ratio(ms)
    assert (r.numerator, r.denominator) == (2, 3) and r.status == Status.VERIFIED
    assert round(r.value, 4) == D("66.6667")


def test_bks_missing_ownership_makes_incomplete_not_zero():
    ms = [mk("A", "BKS", shares=0), mk("B", "BKS")]
    r = C.bks_zero_ratio(ms)
    assert r.status == Status.INCOMPLETE and r.numerator == 1 and r.denominator == 2


def test_bks_not_applicable_when_structure_has_no_bks():
    r = C.bks_zero_ratio([], bks_absent_declared=True)
    assert r.status == Status.NOT_APPLICABLE and r.value is None


def test_bks_missing_table_without_declaration_is_unavailable():
    assert C.bks_zero_ratio([]).status == Status.UNAVAILABLE


def test_bks_related_person_ownership_not_counted_as_zero():
    ms = [mk("A", "BKS", shares=0, cat="related_person"), mk("B", "BKS", shares=0)]
    r = C.bks_zero_ratio(ms)
    assert r.numerator == 1 and r.status == Status.INCOMPLETE


# ---------------- Indicator 3
def own_board(pcts):
    return [mk(f"Thanh Vien {i}", pct=p) for i, p in enumerate(pcts)]


def test_board_ownership_is_sum_of_individual_percentages():
    r = C.board_ownership(own_board(["2.40", "0", "1.00", "0.02", "0.60"]), [])
    assert r.value == D("4.02") and r.calculated_sum == D("4.02") and r.status == Status.VERIFIED


def test_reported_precision_preserved():
    r = C.board_ownership(own_board(["0.0015", "0.0020"]), [])
    assert r.value == D("0.0035")


def agg(p, covers=True, st=Status.VERIFIED):
    return AggregateOwnership(ticker="ABC", reporting_year=2024, reported_pct=D(p), covers_all_active_board=covers,
                              verification_status=st)


def test_aggregate_equal_to_sum_not_double_counted():
    r = C.board_ownership(own_board(["2", "1"]), [agg("3")])
    assert r.value == D("3") and r.reported_aggregate == D("3") and r.calculated_sum == D("3")


def test_conflicting_aggregate_and_sum_flagged_and_neither_overwritten():
    r = C.board_ownership(own_board(["2", "1"]), [agg("5")])
    assert r.status == Status.NEEDS_REVIEW and r.calculated_sum == D("3") and r.reported_aggregate == D("5")


def test_missing_individual_pct_marks_sum_incomplete():
    r = C.board_ownership(own_board(["2", None]), [])
    assert r.status == Status.INCOMPLETE and r.calculated_sum is None and r.value is None


def test_verified_aggregate_resolves_missing_individual_values():
    r = C.board_ownership(own_board(["2", None]), [agg("3.5")])
    assert r.value == D("3.5") and r.calculated_sum is None and r.status == Status.VERIFIED


def test_aggregate_not_covering_all_board_is_ignored():
    r = C.board_ownership(own_board(["2", None]), [agg("3.5", covers=False)])
    assert r.value is None


def test_aggregate_only_policy():
    r = C.board_ownership(own_board(["2", "1"]), [agg("3.01")], policy="aggregate_only", tolerance_pp=0.05)
    assert r.value == D("3.01")


def test_shares_are_never_converted_to_percentages():
    ms = [mk("A B", shares=1_000_000, pct=None), mk("C D", shares=500_000, pct=None)]
    r = C.board_ownership(ms, [])
    assert r.value is None and r.status == Status.INCOMPLETE


def test_inactive_board_member_excluded_from_sum():
    ms = own_board(["2", "1"]) + [mk("Da Roi", pct="9", active=False)]
    assert C.board_ownership(ms, []).value == D("3")


def test_zero_percent_member_counts_as_zero_but_missing_does_not():
    assert C.board_ownership(own_board(["0", "0"]), []).value == D(0)


def test_implausible_ratio_flagged_not_corrected():
    r = C.board_ownership(own_board(["70", "50"]), [])
    assert r.value == D(120) and r.status == Status.NEEDS_REVIEW


def test_ownership_different_effective_dates_flagged():
    ms = own_board(["1", "1"])
    ms[1].ownership_effective_date = "2024-12-31"
    assert C.board_ownership(ms, []).status == Status.NEEDS_REVIEW


def test_related_person_category_not_summed():
    ms = own_board(["1", "1"])
    ms[1].reported_ownership_category = "related_person"
    r = C.board_ownership(ms, [])
    assert r.value is None


def test_completeness_label():
    ok = C.ined_ratio(board(1, 1))
    assert C.completeness(ok, ok, ok) == "COMPLETE"
    assert C.completeness(ok, C.bks_zero_ratio([]), ok) == "INCOMPLETE"
