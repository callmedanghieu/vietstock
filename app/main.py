"""CLI:  python -m app.main --ticker FPT --start-year 2018 --end-year 2025"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import yaml

from .config import load_settings, validate_ticker
from .storage.database import Database
from .workflow import Workflow


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="app.main", description="Vietnamese corporate-governance research pipeline")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--ticker")
    g.add_argument("--tickers", nargs="+")
    p.add_argument("--start-year", type=int)
    p.add_argument("--end-year", type=int)
    p.add_argument("--cached-only", action="store_true", help="never touch the network; use cached/local PDFs only")
    p.add_argument("--force", action="store_true", help="re-download and re-extract even if unchanged")
    p.add_argument("--reprocess-missing", action="store_true", help="only years without a completed extraction")
    p.add_argument("--retry-downloads", action="store_true", help="retry discovery/download for years with no report")
    p.add_argument("--export-only", action="store_true", help="recalculate + export Excel without extraction")
    p.add_argument("--status", action="store_true", help="show per-year stage status")
    p.add_argument("--list-unresolved", action="store_true", help="list open review tasks")
    p.add_argument("--membership-date-policy", choices=["report_disclosed", "fiscal_year_end"])
    p.add_argument("--board-source-policy", choices=["individual_sum_preferred", "aggregate_preferred",
                                                     "individual_sum_only", "aggregate_only"])
    p.add_argument("--scoring-config", type=Path, help="alternative scoring yaml")
    p.add_argument("--config-dir", type=Path)
    p.add_argument("--data-dir", type=Path)
    c = p.add_argument_group("manual correction (recorded; raw observations are never overwritten)")
    c.add_argument("--correct", nargs=3, metavar=("OBS_KEY", "FIELD", "VALUE"))
    c.add_argument("--reviewer")
    c.add_argument("--justification")
    p.add_argument("-v", "--verbose", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO, format="%(levelname)s %(message)s")
    over = {}
    if a.membership_date_policy:
        over["measurement.membership_date_policy"] = a.membership_date_policy
    if a.board_source_policy:
        over["ownership.board_source_policy"] = a.board_source_policy
    s = load_settings(a.config_dir, a.data_dir, over)
    if a.scoring_config:
        s.scoring = yaml.safe_load(a.scoring_config.read_text(encoding="utf-8"))
    tickers = a.tickers or ([a.ticker] if a.ticker else [])
    try:
        tickers = [validate_ticker(t) for t in tickers]
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    y0 = a.start_year or s.get("years.start", 2018)
    y1 = a.end_year or s.get("years.end", 2025)
    if y0 > y1:
        print("error: start-year > end-year", file=sys.stderr)
        return 2
    years = list(range(y0, y1 + 1))
    wf = Workflow(s, Database(s.db_path))
    if a.correct:
        if not (a.reviewer and a.justification):
            print("error: --reviewer and --justification are required for --correct", file=sys.stderr)
            return 2
        wf.db.add_correction(*a.correct[:2], a.correct[2], a.reviewer, a.justification)
        key_parts = a.correct[0].split(":")
        wf.recalculate(key_parts[0], [int(key_parts[1])])
        print("correction recorded; results recalculated")
        return 0
    if not tickers:
        if a.list_unresolved:
            tickers = [None]
        else:
            print("error: provide --ticker or --tickers", file=sys.stderr)
            return 2
    rc = 0
    for t in tickers:
        if a.status:
            for r in wf.status(t):
                print(f"{t} {r['fiscal_year']} {r['stage']:<10} {r['status']:<11} {r['detail'][:90]}")
        elif a.list_unresolved:
            for task in wf.db.open_tasks(t):
                print(f"{task.ticker} {task.fiscal_year} p{task.source_page} {task.field} :: {task.ambiguity}")
        elif a.export_only:
            wf.recalculate(t, years)
            print(f"wrote {wf.export(t, years)}")
        else:
            if a.retry_downloads:
                years_t = [y for y in years if not wf.db.get_report(t, y)] or years
            else:
                years_t = years
            summ = wf.process(t, years_t, offline=a.cached_only, force=a.force, only_missing=a.reprocess_missing)
            for w in summ["warnings"]:
                print(f"warning: {w}")
            for y, r in summ["years"].items():
                print(f"{t} {y}: {r}")
            print(f"wrote {wf.export(t, years)}")
            if any(str(v).startswith(("FAILED", "MISSING")) for v in summ["years"].values()):
                rc = 1
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
