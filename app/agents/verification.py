"""Agent 5 - Independent Verification.

Does not trust Agent 3: re-reads each cited page from the original PDF with a *different* parser
(pdfplumber text vs PyMuPDF used for extraction) and applies deterministic checks.
Model confidence is never used as proof.
"""
from __future__ import annotations

import hashlib
import re
from datetime import date
from decimal import Decimal
from pathlib import Path

from ..models import (AggregateOwnership, CompanyYearResult, ExtractionResult, Independence, MemberObservation,
                      OutstandingShares, ReviewTask, Status)
from ..services.pdf_parser import page_text_independent
from ..utils import fold


def _flat(s: str) -> str:
    return re.sub(r"[\s|]+", "", fold(s))


def _task(m: MemberObservation, field: str, value, why: str, fix: str) -> ReviewTask:
    return ReviewTask(ticker=m.ticker, fiscal_year=m.reporting_year, field=f"{m.governance_body}:{m.person_name}:{field}",
                      extracted_value=str(value), source_page=m.evidence.page if m.evidence else None,
                      evidence=m.evidence.quote if m.evidence else "", ambiguity=why, suggested_resolution=fix)


def verify_members(pdf_path: Path | None, result: ExtractionResult) -> list[ReviewTask]:
    tasks: list[ReviewTask] = []
    cache: dict[int, str] = {}

    def page(p: int) -> str:
        if pdf_path is None:
            return ""
        if p not in cache:
            try:
                cache[p] = _flat(page_text_independent(pdf_path, p))
            except Exception:
                cache[p] = ""
        return cache[p]

    seen: dict[str, MemberObservation] = {}
    for m in result.members:
        problems: list[tuple[str, str, str]] = []   # (field, why, fix)
        if not m.evidence:
            problems.append(("evidence", "no source evidence", "locate the source page manually"))
        else:
            txt = page(m.evidence.page)
            if pdf_path is not None:
                if not txt:
                    problems.append(("source_page", "cited page unreadable by independent parser", "open PDF page"))
                else:
                    if _flat(m.person_name) not in txt:
                        problems.append(("person_name", "name not found on cited page by independent read",
                                         "check for name split across lines / OCR error"))
                    for raw, f in ((m.ownership_raw_shares, "personal_shares_owned"),
                                   (m.ownership_raw_pct, "personal_ownership_percentage")):
                        if raw and _flat(raw) not in txt:
                            problems.append((f, f"reported value '{raw}' not found on cited page", "verify against PDF"))
            if m.independence == Independence.TRUE and "doclap" not in _flat(m.evidence.quote) \
                    and "independent" not in _flat(m.evidence.quote):
                problems.append(("independence", "independent flag not supported by quoted title",
                                 "confirm title in report"))
        if m.personal_shares_owned is not None and (not isinstance(m.personal_shares_owned, int) or m.personal_shares_owned < 0):
            problems.append(("personal_shares_owned", "invalid share count", "correct manually"))
        p_ = m.personal_ownership_percentage
        if p_ is not None and (p_ < 0 or p_ > 100):
            problems.append(("personal_ownership_percentage", f"percentage {p_} outside 0-100 (unit error?)", "check units"))
        if m.governance_body == "BKS" and m.zero_ownership_confirmed:
            ok = (m.zero_ownership_basis == "ZERO_SHARES" and m.personal_shares_owned == 0) or \
                 (m.zero_ownership_basis == "ZERO_PERCENTAGE" and p_ is not None and p_ == 0)
            if not ok or m.reported_ownership_category != "personal":
                problems.append(("zero_ownership", "zero-ownership basis inconsistent with extracted values", "re-check"))
        if m.active_at_cutoff is None:
            problems.append(("active_at_cutoff", "membership at measurement date could not be established",
                             "determine appointment/departure dates"))
        for n in m.review_notes:
            if n.startswith("REVIEW:"):
                problems.append(("flag", n[7:].strip(), "resolve flagged ambiguity"))
        prev = seen.get(f"{m.ticker}:{m.reporting_year}:{fold(m.person_name)}")
        if prev and prev.governance_body != m.governance_body:
            problems.append(("person_name", f"same person appears in {prev.governance_body} and {m.governance_body}",
                             "confirm they are the same individual / dual role"))
        seen[f"{m.ticker}:{m.reporting_year}:{fold(m.person_name)}"] = m
        if problems:
            m.verification_status = Status.NEEDS_REVIEW
            for f, why, fix in problems:
                m.review_notes.append(f"VERIFY: {f}: {why}")
                tasks.append(_task(m, f, getattr(m, f, "") if hasattr(m, f) else "", why, fix))
        else:
            m.verification_status = Status.VERIFIED
        if m.personal_shares_owned is None and p_ is None and m.ownership_information_status == Status.PENDING:
            m.ownership_information_status = Status.UNAVAILABLE
        elif m.ownership_information_status == Status.PENDING:
            m.ownership_information_status = m.verification_status
    return tasks


def verify_aggregates(pdf_path: Path | None, result: ExtractionResult) -> list[ReviewTask]:
    tasks = []
    for a in result.aggregates:
        a.verification_status = Status.VERIFIED
        if pdf_path is not None and a.evidence:
            txt = _flat(page_text_independent(pdf_path, a.evidence.page))
            nums = re.findall(r"\d[\d.,]*", a.raw_text)
            if nums and _flat(nums[-1]) not in txt:
                a.verification_status = Status.NEEDS_REVIEW
                a.review_notes.append("aggregate figure not found on cited page")
        if not a.covers_all_active_board:
            a.verification_status = Status.NEEDS_REVIEW
            a.review_notes.append("cannot confirm figure covers all active Board members; not used as final")
        if a.reported_pct is not None and not (0 <= a.reported_pct <= 100):
            a.verification_status = Status.NEEDS_REVIEW
            a.review_notes.append("aggregate outside 0-100")
        if a.verification_status != Status.VERIFIED:
            tasks.append(ReviewTask(ticker=a.ticker, fiscal_year=a.reporting_year, field="HDQT:aggregate_ownership",
                                    extracted_value=str(a.reported_pct), source_page=a.evidence.page if a.evidence else None,
                                    evidence=a.raw_text, ambiguity="; ".join(a.review_notes),
                                    suggested_resolution="confirm scope of the aggregate in the report"))
    return tasks


def review_indicators(res: CompanyYearResult) -> list[ReviewTask]:
    tasks = []
    for ind in (res.ined, res.bks, res.board_own):
        if ind.status in (Status.NEEDS_REVIEW, Status.INCOMPLETE, Status.UNAVAILABLE):
            tasks.append(ReviewTask(ticker=res.ticker, fiscal_year=res.year, field=f"indicator:{ind.name}",
                                    extracted_value="" if ind.value is None else str(ind.value),
                                    ambiguity=f"{ind.status.value}: " + "; ".join(ind.notes),
                                    suggested_resolution="resolve member-level tasks, then recalculate"))
    return tasks


def verify_shares(obs: OutstandingShares, approved: list[str]) -> list[ReviewTask]:
    """Deterministic checks for historical outstanding shares; never substitutes current shares."""
    tasks = []
    if obs.outstanding_shares is None and (obs.issued_shares is None):
        obs.verification_status = Status.INCOMPLETE
        obs.review_notes.append("historical outstanding shares not available; not invented")
    else:
        bad = []
        if obs.outstanding_shares is None and obs.issued_shares is not None:
            bad.append("outstanding not given; only issued shares")
        if obs.source_category not in approved:
            bad.append(f"source category {obs.source_category!r} not approved")
        if obs.effective_date is None:
            bad.append("effective date missing")
        elif obs.final_trading_date and obs.effective_date != obs.final_trading_date:
            bad.append(f"effective date {obs.effective_date} != final trading date {obs.final_trading_date}")
        if obs.outstanding_shares is not None and obs.issued_shares is not None:
            exp = obs.issued_shares - (obs.treasury_shares or 0)
            if exp != obs.outstanding_shares:
                bad.append(f"outstanding {obs.outstanding_shares} != issued - treasury ({exp})")
        if obs.outstanding_shares is not None and obs.outstanding_shares <= 0:
            bad.append("non-positive outstanding shares")
        obs.verification_status = Status.NEEDS_REVIEW if bad else Status.VERIFIED
        obs.review_notes += bad
    t = shares_task(obs)
    return [t] if t else []


def shares_task(obs: OutstandingShares) -> ReviewTask | None:
    """Derived from stored status (idempotent; does not re-run verification)."""
    if obs.verification_status == Status.VERIFIED:
        return None
    return ReviewTask(ticker=obs.ticker, fiscal_year=obs.reporting_year, field="outstanding_shares",
                      extracted_value=str(obs.outstanding_shares), ambiguity="; ".join(dict.fromkeys(obs.review_notes)),
                      suggested_resolution="supply a verified historical figure with source and effective date")


def tasks_from_state(members: list[MemberObservation], aggs: list[AggregateOwnership]) -> list[ReviewTask]:
    """Derive open review tasks from current (corrections-applied) state, so resolved items drop out."""
    tasks = []
    for m in members:
        if m.verification_status != Status.NEEDS_REVIEW:
            continue
        notes = [n for n in m.review_notes if n.startswith(("REVIEW:", "VERIFY:"))] or ["flagged for review"]
        for n in notes:
            field = n.split(":", 2)[1].strip() if n.startswith("VERIFY:") else "flag"
            tasks.append(ReviewTask(ticker=m.ticker, fiscal_year=m.reporting_year,
                                    field=f"{m.governance_body}:{m.person_name}:{field}:{hashlib.sha1(n.encode()).hexdigest()[:8]}",
                                    extracted_value=f"shares={m.personal_shares_owned}; pct={m.personal_ownership_percentage}",
                                    source_page=m.evidence.page if m.evidence else None,
                                    evidence=m.evidence.quote if m.evidence else "", ambiguity=n.split(":", 1)[1].strip(),
                                    suggested_resolution="check the cited PDF page; apply a recorded manual correction or confirm"))
    for a in aggs:
        if a.verification_status == Status.NEEDS_REVIEW:
            tasks.append(ReviewTask(ticker=a.ticker, fiscal_year=a.reporting_year, field="HDQT:aggregate_ownership",
                                    extracted_value=str(a.reported_pct), source_page=a.evidence.page if a.evidence else None,
                                    evidence=a.raw_text, ambiguity="; ".join(a.review_notes),
                                    suggested_resolution="confirm the aggregate's scope in the report"))
    return tasks
