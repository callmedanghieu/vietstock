"""Configurable deterministic scoring. Empty thresholds => PENDING_RUBRIC (never invented scores)."""
from __future__ import annotations

from decimal import Decimal

from ..models import CompanyYearResult, IndicatorResult, Status

METRIC_ATTR = {"independent_director_ratio": "ined", "zero_ownership_bks_ratio": "bks",
               "board_ownership_ratio": "board_own"}
SCOREABLE = {Status.VERIFIED}


def _in_range(v: Decimal, t: dict) -> bool:
    lo, hi = t.get("min"), t.get("max")
    if lo is not None:
        lo = Decimal(str(lo))
        if v < lo or (v == lo and not t.get("min_inclusive", True)):
            return False
    if hi is not None:
        hi = Decimal(str(hi))
        if v > hi or (v == hi and not t.get("max_inclusive", True)):
            return False
    return True


def score_metric(cfg: dict, ind: IndicatorResult) -> tuple[float | None, str]:
    th = (cfg or {}).get("thresholds") or []
    if not th:
        return None, "PENDING_RUBRIC"
    if ind.status not in SCOREABLE or ind.value is None:
        return None, f"NOT_SCORED_{ind.status.value}"      # no automatic lowest score for missing data
    hits = [t for t in th if _in_range(ind.value, t)]
    if len(hits) != 1:
        return None, "RUBRIC_AMBIGUOUS" if hits else "RUBRIC_NO_MATCH"
    return float(hits[0]["points"]), "SCORED"


def score_company_year(scoring: dict, res: CompanyYearResult) -> CompanyYearResult:
    metrics = scoring.get("metrics", {})
    total, wsum, states = 0.0, 0.0, []
    for name, attr in METRIC_ATTR.items():
        cfg = metrics.get(name, {})
        pts, state = score_metric(cfg, getattr(res, attr))
        res.scores[name] = {"points": pts, "state": state}
        states.append(state)
        if pts is not None:
            w = float(cfg.get("weight", 1.0))
            total += pts * w
            wsum += w
    if all(s == "PENDING_RUBRIC" for s in states):
        res.score_status, res.overall_score = "PENDING_RUBRIC", None
    elif all(s == "SCORED" for s in states):
        res.score_status, res.overall_score = "SCORED", total
    else:
        res.score_status, res.overall_score = "INCOMPLETE", None   # partial scores are not summed silently
    return res
