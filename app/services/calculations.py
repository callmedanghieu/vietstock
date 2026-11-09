"""Deterministic indicator calculations (no LLM). Percentages are percentage values (2.5 == 2.5%).

Missing data never becomes zero. Raw inputs are echoed in IndicatorResult.inputs.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Iterable

from ..models import (AggregateOwnership, CompanyYearResult, Independence, IndicatorResult,
                      MemberObservation, Status)
from ..utils import NOT_DISCLOSED, name_key

HUNDRED = Decimal(100)
_BAD = (Status.NEEDS_REVIEW, Status.UNAVAILABLE, Status.PENDING)


def classify_zero_ownership(shares: int | None, pct: Decimal | None, category: str = "personal"):
    """-> (confirmed: bool|None, basis: str|None, status, notes). None = cannot be determined (NOT zero)."""
    notes: list[str] = []
    if category != "personal":
        return None, None, Status.NEEDS_REVIEW, [f"ownership category '{category}' is not personal; not used"]
    if shares is not None and (not isinstance(shares, int) or shares < 0):
        return None, None, Status.NEEDS_REVIEW, ["invalid share count"]
    if pct is not None and (pct < 0 or pct > HUNDRED):
        return None, None, Status.NEEDS_REVIEW, ["ownership percentage outside 0-100"]
    if shares is None and pct is None:
        return None, None, Status.UNAVAILABLE, ["ownership not disclosed - missing is NOT zero"]
    if shares is not None and shares > 0 and pct is not None and pct == 0:
        return False, None, Status.NEEDS_REVIEW, ["shares>0 but percentage reported as 0"]
    if shares == 0 and pct is not None and pct > 0:
        return None, None, Status.NEEDS_REVIEW, ["0 shares but percentage>0: conflicting"]
    if shares == 0:
        return True, "ZERO_SHARES", Status.VERIFIED, notes
    if pct is not None and pct == 0:
        return True, "ZERO_PERCENTAGE", Status.VERIFIED, ["0.00% reported; share count not inferred"]
    return False, None, Status.VERIFIED, notes


def _dedupe(members: Iterable[MemberObservation]):
    seen, out, dups = set(), [], []
    for m in members:
        if m.key in seen:
            dups.append(m)
        else:
            seen.add(m.key)
            out.append(m)
    return out, dups


def _worst(statuses: Iterable[Status]) -> Status:
    s = set(statuses)
    for st in (Status.NEEDS_REVIEW, Status.UNAVAILABLE, Status.PENDING):
        if st in s:
            return st
    return Status.VERIFIED


def _dates(ms: list[MemberObservation], attr: str):
    ds = sorted({getattr(m, attr) or NOT_DISCLOSED for m in ms})
    return ds


def _eff(ds: list[str], notes: list[str], label: str) -> str | None:
    if not ds:
        return None
    if len(ds) > 1:
        notes.append(f"{label}: members carry different effective dates {ds}")
    return "; ".join(ds)


def active(members: list[MemberObservation], body: str) -> list[MemberObservation]:
    ms = [m for m in members if m.governance_body == body]
    return [m for m in ms if m.active_at_cutoff is True]


def ined_ratio(members: list[MemberObservation], settings_unstated_nonind: bool = True) -> IndicatorResult:
    r = IndicatorResult(name="independent_director_ratio")
    act, dups = _dedupe(active(members, "HDQT"))
    if dups:
        r.notes.append(f"{len(dups)} duplicate director record(s) ignored")
    undetermined = [m for m in members if m.governance_body == "HDQT" and m.active_at_cutoff is None]
    if not act:
        r.status = Status.UNAVAILABLE
        r.notes.append("no active HĐQT members established")
        return r
    ind = [m for m in act if m.independence == Independence.TRUE]
    r.numerator, r.denominator = len(ind), len(act)
    r.value = Decimal(len(ind)) / Decimal(len(act)) * HUNDRED
    r.effective_date = _eff(_dates(act, "membership_effective_date"), r.notes, "membership")
    r.inputs = [{"name": m.person_name, "title": m.reported_title, "independence": m.independence.value,
                 "active": m.active_at_cutoff, "status": m.verification_status.value} for m in act]
    st = [m.verification_status for m in act]
    if undetermined:
        r.notes.append(f"{len(undetermined)} member(s) with undetermined activity status")
        st.append(Status.NEEDS_REVIEW)
    unk = [m for m in act if m.independence == Independence.UNKNOWN]
    if unk:
        if settings_unstated_nonind and all(m.reported_title for m in unk):
            r.notes.append(f"{len(unk)} member(s) without explicit 'độc lập' counted as non-independent "
                           "(config independence.unstated_as_non_independent_when_titles_listed)")
        else:
            st.append(Status.NEEDS_REVIEW)
            r.notes.append("independence unknown for some members")
    if any(m.independence == Independence.TRUE and "độc lập" not in (m.reported_title or "").casefold()
           and "independent" not in (m.reported_title or "").casefold() for m in act):
        st.append(Status.NEEDS_REVIEW)
        r.notes.append("independent flag without 'độc lập' in reported title")
    if len({m.membership_effective_date for m in act}) > 1:
        st.append(Status.NEEDS_REVIEW)
    r.status = _worst(st)
    _sanity(r)
    return r


def bks_zero_ratio(members: list[MemberObservation], bks_absent_declared: bool = False) -> IndicatorResult:
    r = IndicatorResult(name="zero_ownership_bks_ratio")
    all_bks = [m for m in members if m.governance_body == "BKS"]
    if bks_absent_declared and not all_bks:
        r.status = Status.NOT_APPLICABLE
        r.notes.append("Report declares no Ban Kiểm soát (alternative legal governance structure)")
        return r
    act, dups = _dedupe(active(members, "BKS"))
    if dups:
        r.notes.append(f"{len(dups)} duplicate BKS record(s) ignored")
    if not act:
        r.status = Status.UNAVAILABLE
        r.notes.append("no active BKS members established")
        return r
    zero = [m for m in act if m.zero_ownership_confirmed is True]
    r.numerator, r.denominator = len(zero), len(act)
    r.value = Decimal(len(zero)) / Decimal(len(act)) * HUNDRED
    r.effective_date = _eff(_dates(act, "ownership_effective_date"), r.notes, "ownership")
    r.inputs = [{"name": m.person_name, "shares": m.personal_shares_owned,
                 "pct": None if m.personal_ownership_percentage is None else str(m.personal_ownership_percentage),
                 "zero_confirmed": m.zero_ownership_confirmed, "basis": m.zero_ownership_basis,
                 "info_status": m.ownership_information_status.value} for m in act]
    st = [m.verification_status for m in act]
    unknown = [m for m in act if m.zero_ownership_confirmed is None]
    if unknown:
        r.status = Status.INCOMPLETE
        r.notes.append(f"{len(unknown)} active member(s) have missing/unverified ownership; value is a lower bound "
                       "(missing is NOT zero)")
        _sanity(r)
        return r
    if len({m.ownership_effective_date for m in act}) > 1:
        st.append(Status.NEEDS_REVIEW)
    r.status = _worst(st)
    _sanity(r)
    return r


def board_ownership(members: list[MemberObservation], aggregates: list[AggregateOwnership],
                    policy: str = "individual_sum_preferred", tolerance_pp: float = 0.05) -> IndicatorResult:
    r = IndicatorResult(name="board_ownership_ratio")
    act, dups = _dedupe(active(members, "HDQT"))
    if dups:
        r.notes.append(f"{len(dups)} duplicate director record(s) ignored")
    personal = [m for m in act if m.reported_ownership_category == "personal"]
    nonpersonal = [m for m in act if m.reported_ownership_category != "personal"]
    pcts = [m.personal_ownership_percentage for m in personal]
    missing = [m for m in act if m.personal_ownership_percentage is None or m in nonpersonal]
    sum_complete = bool(act) and not missing
    if act:
        r.calculated_sum = sum((p for p in pcts if p is not None), Decimal(0)) if sum_complete else None
        r.inputs = [{"name": m.person_name, "shares": m.personal_shares_owned,
                     "pct": None if m.personal_ownership_percentage is None else str(m.personal_ownership_percentage),
                     "category": m.reported_ownership_category} for m in act]
        r.effective_date = _eff(_dates(act, "ownership_effective_date"), r.notes, "ownership")
        if missing:
            known = sum((p for p in pcts if p is not None), Decimal(0))
            r.notes.append(f"calculated sum INCOMPLETE: {len(missing)} active member(s) lack personal ownership % "
                           f"(partial known sum {known}%, NOT used as final)")
    agg = next((a for a in aggregates if a.covers_all_active_board and a.reported_pct is not None), None)
    if agg:
        r.reported_aggregate = agg.reported_pct
    st = [m.verification_status for m in act]
    if len({m.ownership_effective_date for m in act}) > 1:
        st.append(Status.NEEDS_REVIEW)
        r.notes.append("members carry different ownership effective dates")

    calc, rep = r.calculated_sum, r.reported_aggregate
    conflict = (calc is not None and rep is not None and abs(calc - rep) > Decimal(str(tolerance_pp)))
    if conflict:
        st.append(Status.NEEDS_REVIEW)
        r.notes.append(f"individual sum {calc}% conflicts with reported aggregate {rep}% (tolerance {tolerance_pp}pp); "
                       "neither value overwritten")
    final, used = None, None
    order = {"individual_sum_preferred": ["calc", "agg"], "aggregate_preferred": ["agg", "calc"],
             "individual_sum_only": ["calc"], "aggregate_only": ["agg"]}[policy]
    for src in order:
        if src == "calc" and calc is not None:
            final, used = calc, "individual sum"
            break
        if src == "agg" and rep is not None and (agg and agg.verification_status != Status.NEEDS_REVIEW):
            final, used = rep, "reported aggregate"
            break
    if final is None and rep is not None and policy in ("individual_sum_preferred", "aggregate_preferred"):
        st.append(Status.NEEDS_REVIEW)
    r.value = final
    if used:
        r.notes.append(f"final value taken from {used} (policy {policy})")
    if not act and final is None:
        r.status = Status.UNAVAILABLE
        r.notes.append("no active HĐQT members established")
    elif final is None:
        r.status = Status.INCOMPLETE
    else:
        r.status = _worst(st)
        if used == "reported aggregate" and missing:
            r.notes.append("aggregate used to resolve missing individual percentages")
    r.numerator, r.denominator = None, len(act) or None
    _sanity(r)
    return r


def _sanity(r: IndicatorResult) -> None:
    if r.numerator is not None and r.denominator is not None:
        if r.denominator <= 0 or r.numerator > r.denominator:
            r.status = Status.NEEDS_REVIEW
            r.notes.append("invalid numerator/denominator")
    if r.value is not None and (r.value < 0 or r.value > HUNDRED):
        r.status = Status.NEEDS_REVIEW
        r.notes.append("implausible ratio outside 0-100; flagged, not corrected")


def completeness(*inds: IndicatorResult) -> str:
    sts = {i.status for i in inds}
    if sts <= {Status.VERIFIED, Status.NOT_APPLICABLE}:
        return "COMPLETE"
    if Status.NEEDS_REVIEW in sts:
        return "NEEDS_REVIEW"
    return "INCOMPLETE"


def compute_company_year(ticker: str, year: int, members: list[MemberObservation],
                         aggregates: list[AggregateOwnership], bks_absent: bool, research: dict) -> CompanyYearResult:
    own = research.get("ownership", {})
    unst = research.get("independence", {}).get("unstated_as_non_independent_when_titles_listed", True)
    i1 = ined_ratio(members, unst)
    i2 = bks_zero_ratio(members, bks_absent)
    i3 = board_ownership(members, aggregates, own.get("board_source_policy", "individual_sum_preferred"),
                         own.get("aggregate_tolerance_pp", 0.05))
    return CompanyYearResult(ticker=ticker, year=year, ined=i1, bks=i2, board_own=i3,
                             completeness=completeness(i1, i2, i3))
