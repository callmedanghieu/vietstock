"""Agent 4 - Historical Outstanding Shares (supplementary data; NEVER the denominator of Indicator 3).

No live exchange/Vietstock data is fabricated. Values come from an explicit provider:
 * CSV supplied by the researcher (data/reviewed/outstanding_shares.csv), each row carrying source+effective date, or
 * a VietstockSharesProvider hook (needs network access).
Without data the observation is INCOMPLETE.
"""
from __future__ import annotations

import csv
from datetime import date, timedelta
from pathlib import Path

from ..config import Settings
from ..models import OutstandingShares, ReviewTask, Status
from .verification import verify_shares


def last_trading_day(year: int, holidays: set[date] | None = None) -> date:
    """Last weekday of the year that is not a configured exchange holiday."""
    d = date(year, 12, 31)
    hol = holidays or set()
    while d.weekday() >= 5 or d in hol:
        d -= timedelta(days=1)
    return d


def _int(s):
    s = (s or "").strip().replace(",", "").replace(".", "").replace(" ", "")
    return int(s) if s.isdigit() else None


def load_csv(path: Path) -> dict[tuple[str, int], dict]:
    """columns: ticker,year,outstanding_shares,issued_shares,treasury_shares,source,source_category,effective_date,adjustments"""
    out = {}
    if not path.exists():
        return out
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                out[(r["ticker"].strip().upper(), int(r["year"]))] = r
            except (KeyError, ValueError):
                continue
    return out


def run(settings: Settings, ticker: str, years: list[int]) -> tuple[list[OutstandingShares], list[ReviewTask]]:
    holidays = {date.fromisoformat(h) for h in settings.get("outstanding_shares.holidays", [])}
    rows = load_csv(settings.path(settings.get("outstanding_shares.csv_path", "reviewed/outstanding_shares.csv")))
    approved = settings.get("outstanding_shares.approved_sources", [])
    res, tasks = [], []
    for y in years:
        o = OutstandingShares(ticker=ticker, reporting_year=y, final_trading_date=last_trading_day(y, holidays))
        o.review_notes.append("final trading date derived from weekday calendar + configured holidays; "
                              "confirm against exchange calendar")
        r = rows.get((ticker, y))
        if r:
            o.outstanding_shares, o.issued_shares, o.treasury_shares = (_int(r.get("outstanding_shares")),
                                                                       _int(r.get("issued_shares")), _int(r.get("treasury_shares")))
            if o.outstanding_shares is None and o.issued_shares is not None:
                pass  # verify_shares flags: outstanding must be explicit or derivable w/ treasury
            o.source, o.source_category = r.get("source"), (r.get("source_category") or "").upper()
            o.adjustments = r.get("adjustments") or None
            try:
                o.effective_date = date.fromisoformat((r.get("effective_date") or "").strip())
            except ValueError:
                o.effective_date = None
        tasks += verify_shares(o, approved)
        res.append(o)
    return res, tasks
