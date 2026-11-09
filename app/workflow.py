"""Deterministic orchestration (state machine). Per-year failures are isolated and recorded; stages resume."""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

import yaml

from .agents import discovery, document_intelligence, governance_extraction, outstanding_shares, verification
from .config import Settings, validate_ticker
from .models import CompanyYearResult, Status
from .services import calculations, excel_export, scoring
from .services.vietstock import HttpClient
from .storage.database import Database

log = logging.getLogger(__name__)


def method_hash(settings: Settings) -> str:
    return hashlib.sha256(json.dumps(settings.research, sort_keys=True, default=str).encode()).hexdigest()[:12]


class Workflow:
    def __init__(self, settings: Settings, db: Database | None = None, http: HttpClient | None = None):
        self.s = settings
        self.db = db or Database(settings.db_path)
        self.http = http

    # ------------------------------------------------------------------ full pipeline
    def process(self, ticker: str, years: list[int], offline=False, force=False, only_missing=False) -> dict:
        ticker = validate_ticker(ticker)
        run = self.db.start_run({"ticker": ticker, "years": years, "offline": offline, "force": force,
                                 "method": method_hash(self.s)})
        summary = {"ticker": ticker, "years": {}, "warnings": []}
        todo = years
        if only_missing:
            todo = [y for y in years if not (self.db.stage_ok(ticker, y, "extract") and self.db.get_report(ticker, y))]
        try:
            docs, missing, warns = discovery.run(self.s, self.db, ticker, todo, http=self.http, offline=offline, force=force)
        except Exception as e:                      # pragma: no cover - unexpected
            log.exception("discovery failed")
            docs, missing, warns = {}, {y: f"discovery crashed: {e}" for y in todo}, [str(e)]
        summary["warnings"] += warns
        for y in todo:
            if y in missing:
                self.db.set_stage(ticker, y, "discovery", "MISSING", missing[y])
            else:
                self.db.set_stage(ticker, y, "discovery", "DONE", docs[y].sha256 or "")
        for y in todo:
            if y in missing:
                summary["years"][y] = f"MISSING_REPORT: {missing[y]}"
                continue
            try:
                summary["years"][y] = self._process_year(ticker, y, docs[y], force)
            except Exception as e:                  # a failure in one year never stops the others
                log.exception("year %s failed", y)
                self.db.set_stage(ticker, y, "extract", "FAILED", f"{type(e).__name__}: {e}")
                summary["years"][y] = f"FAILED: {type(e).__name__}: {e}"
        self._shares(ticker, years)
        self.recalculate(ticker, years)
        self.db.finish_run(run, summary)
        return summary

    def _process_year(self, ticker: str, y: int, doc, force: bool) -> str:
        key = f"{doc.sha256}:{method_hash(self.s)}"
        row = self.db.conn.execute("SELECT detail FROM stage_status WHERE ticker=? AND fiscal_year=? AND stage='extract' "
                                   "AND status='DONE'", (ticker, y)).fetchone()
        if row and row["detail"] == key and not force:
            return "SKIPPED (unchanged document and methodology)"
        pdf = Path(doc.local_path)
        parsed = document_intelligence.run(self.s, pdf, doc.document_id)
        self.db.save_pages(doc.document_id, parsed.pages)
        self.db.set_stage(ticker, y, "parse", "DONE", f"pages={parsed.n_pages} scanned={parsed.is_scanned}")
        if doc.validation_status == "REJECTED":
            raise RuntimeError("document rejected by validation")
        ext = governance_extraction.extract(self.s, ticker, y, parsed)
        verification.verify_members(pdf, ext)
        verification.verify_aggregates(pdf, ext)
        self.db.replace_extraction(ticker, y, ext.members, ext.aggregates)
        self.db.set_stage(ticker, y, "extract", "DONE" if ext.status in ("OK", "NO_DATA") else "UNAVAILABLE", key)
        # structure info is needed for NOT_APPLICABLE BKS: keep it as a stage detail
        self.db.set_stage(ticker, y, "structure", "DONE", json.dumps(ext.structure.model_dump(), ensure_ascii=False))
        for w in ext.warnings:
            log.warning("%s %s: %s", ticker, y, w)
        return f"{ext.status}: {len(ext.members)} member rows, {len(ext.aggregates)} aggregate rows" + (
            f" | warnings: {'; '.join(ext.warnings)}" if ext.warnings else "")

    def _shares(self, ticker, years):
        obs, tasks = outstanding_shares.run(self.s, ticker, years)
        for o in obs:
            self.db.save_shares(o)
            self.db.set_stage(ticker, o.reporting_year, "shares", "DONE" if o.verification_status == Status.VERIFIED else "INCOMPLETE")

    # ------------------------------------------------------------------ calculation (cheap, always re-run)
    def recalculate(self, ticker: str, years: list[int]) -> dict[int, CompanyYearResult]:
        scoring_cfg = self.s.scoring
        self.db.save_scoring_config(yaml.safe_dump(scoring_cfg, allow_unicode=True))
        out = {}
        for y in years:
            members = self.db.effective_members(ticker, y)
            aggs = self.db.effective_aggregates(ticker, y)
            ran = self.db.conn.execute("SELECT status FROM stage_status WHERE ticker=? AND fiscal_year=? AND stage='extract'",
                                       (ticker, y)).fetchone()
            if not members and not (ran and ran["status"] in ("DONE", "UNAVAILABLE")):
                self.db.replace_tasks(ticker, y, [])
                continue
            st = self.db.conn.execute("SELECT detail FROM stage_status WHERE ticker=? AND fiscal_year=? AND stage='structure'",
                                      (ticker, y)).fetchone()
            bks_absent = bool(st and json.loads(st["detail"]).get("bks_absent_declared"))
            res = calculations.compute_company_year(ticker, y, members, aggs, bks_absent, self.s.research)
            scoring.score_company_year(scoring_cfg, res)
            self.db.save_result(res)
            tasks = verification.tasks_from_state(members, aggs) + verification.review_indicators(res)
            sh = self.db.get_shares(ticker, y)
            st_task = verification.shares_task(sh) if sh else None
            if st_task:
                tasks.append(st_task)
            self.db.replace_tasks(ticker, y, tasks)
            out[y] = res
        return out

    # ------------------------------------------------------------------ export
    def export(self, ticker: str, years: list[int]) -> Path:
        ticker = validate_ticker(ticker)
        docs, missing, members, aggs, results, shares = {}, {}, {}, {}, {}, {}
        for y in years:
            d = self.db.get_report(ticker, y)
            if d:
                docs[y] = d
            else:
                r = self.db.conn.execute("SELECT detail FROM stage_status WHERE ticker=? AND fiscal_year=? AND stage='discovery'",
                                         (ticker, y)).fetchone()
                missing[y] = r["detail"] if r else "not processed"
            members[y] = self.db.effective_members(ticker, y)
            aggs[y] = self.db.effective_aggregates(ticker, y)
            r = self.db.get_result(ticker, y)
            if r:
                results[y] = r
            shares[y] = self.db.get_shares(ticker, y)
        comp = discovery.resolve_company(self.s, ticker)
        wb = excel_export.build_workbook(ticker, years, comp, docs, missing, members, aggs, results, shares,
                                         self.db.open_tasks(ticker), self.s.scoring)
        return excel_export.save(wb, self.s.output_dir / f"{ticker}_Corporate_Governance_{years[0]}_{years[-1]}.xlsx")

    def status(self, ticker: str) -> list[dict]:
        return [dict(r) for r in self.db.stages(ticker)]
