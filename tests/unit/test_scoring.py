from decimal import Decimal as D

import yaml

from app.config import ROOT
from app.models import CompanyYearResult, IndicatorResult, Status
from app.services.scoring import score_company_year, score_metric


def res(v1="40", v2="50", v3="3", st=Status.VERIFIED):
    mk = lambda n, v: IndicatorResult(name=n, value=None if v is None else D(v), status=st)
    return CompanyYearResult(ticker="ABC", year=2024, ined=mk("a", v1), bks=mk("b", v2), board_own=mk("c", v3))


def test_shipped_config_is_pending_and_scores_nothing():
    cfg = yaml.safe_load((ROOT / "config/scoring.yaml").read_text(encoding="utf-8"))
    r = score_company_year(cfg, res())
    assert r.score_status == "PENDING_RUBRIC" and r.overall_score is None
    assert all(v["points"] is None for v in r.scores.values())


# The rubric below is a TEST-ONLY illustration of boundary handling. It is NOT the research rubric.
TEST_CFG = {"metrics": {
    "independent_director_ratio": {"weight": 1, "thresholds": [
        {"min": None, "max": 33.33, "max_inclusive": False, "points": 0},
        {"min": 33.33, "max": 50, "min_inclusive": True, "max_inclusive": False, "points": 1},
        {"min": 50, "max": None, "min_inclusive": True, "points": 2}]},
    "zero_ownership_bks_ratio": {"weight": 2, "thresholds": [{"min": 0, "max": 100, "points": 1}]},
    "board_ownership_ratio": {"weight": 1, "thresholds": [{"min": 0, "max": 100, "points": 1}]}}}


def test_exact_boundaries():
    cfg = TEST_CFG["metrics"]["independent_director_ratio"]
    pick = lambda v: score_metric(cfg, IndicatorResult(name="x", value=D(v), status=Status.VERIFIED))
    assert pick("33.32") == (0.0, "SCORED")
    assert pick("33.33") == (1.0, "SCORED")      # lower bound inclusive
    assert pick("49.99") == (1.0, "SCORED")
    assert pick("50") == (2.0, "SCORED")         # upper bound of previous band exclusive


def test_overall_weighted_sum():
    r = score_company_year(TEST_CFG, res("50", "10", "1"))
    assert r.score_status == "SCORED" and r.overall_score == 2 * 1 + 1 * 2 + 1 * 1


def test_incomplete_indicator_gets_no_score_and_no_automatic_lowest():
    r = score_company_year(TEST_CFG, res(st=Status.INCOMPLETE))
    assert r.scores["independent_director_ratio"] == {"points": None, "state": "NOT_SCORED_INCOMPLETE"}
    assert r.overall_score is None and r.score_status == "INCOMPLETE"


def test_raw_values_preserved_independently_of_scores():
    r = score_company_year(TEST_CFG, res("50"))
    assert r.ined.value == D("50")
