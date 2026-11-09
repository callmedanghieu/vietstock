"""SQLite persistence. Raw observations are never edited in place; corrections live in `manual_corrections`
and are layered on top when computing final values (see apply_corrections)."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from ..models import (AggregateOwnership, CompanyYearResult, Independence, MemberObservation, OutstandingShares,
                      ReportDoc, ReviewTask, Status)
from ..services.calculations import classify_zero_ownership
from ..utils import parse_one_date

SCHEMA = """
CREATE TABLE IF NOT EXISTS companies(ticker TEXT PRIMARY KEY, name TEXT, exchange TEXT, notes TEXT);
CREATE TABLE IF NOT EXISTS report_documents(document_id TEXT PRIMARY KEY, ticker TEXT, fiscal_year INT, sha256 TEXT,
  validation_status TEXT, json TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS document_pages(document_id TEXT, page INT, tags TEXT, score INT, scanned INT,
  PRIMARY KEY(document_id, page));
CREATE TABLE IF NOT EXISTS member_observations(obs_key TEXT PRIMARY KEY, ticker TEXT, fiscal_year INT, body TEXT,
  json TEXT, extracted_at TEXT);
CREATE TABLE IF NOT EXISTS aggregate_observations(obs_key TEXT PRIMARY KEY, ticker TEXT, fiscal_year INT, json TEXT);
CREATE TABLE IF NOT EXISTS outstanding_share_observations(ticker TEXT, fiscal_year INT, json TEXT,
  PRIMARY KEY(ticker, fiscal_year));
CREATE TABLE IF NOT EXISTS verification_decisions(id INTEGER PRIMARY KEY AUTOINCREMENT, obs_key TEXT, status TEXT,
  notes TEXT, decided_at TEXT);
CREATE TABLE IF NOT EXISTS manual_corrections(id INTEGER PRIMARY KEY AUTOINCREMENT, obs_key TEXT, field TEXT,
  raw_value TEXT, corrected_value TEXT, reviewer TEXT NOT NULL, justification TEXT NOT NULL, created_at TEXT);
CREATE TABLE IF NOT EXISTS review_tasks(id INTEGER PRIMARY KEY AUTOINCREMENT, ticker TEXT, fiscal_year INT, field TEXT,
  json TEXT, status TEXT DEFAULT 'OPEN', UNIQUE(ticker, fiscal_year, field));
CREATE TABLE IF NOT EXISTS scoring_configs(hash TEXT PRIMARY KEY, yaml TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS research_runs(id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT, finished_at TEXT,
  params TEXT, summary TEXT);
CREATE TABLE IF NOT EXISTS stage_status(ticker TEXT, fiscal_year INT, stage TEXT, status TEXT, detail TEXT,
  updated_at TEXT, PRIMARY KEY(ticker, fiscal_year, stage));
CREATE TABLE IF NOT EXISTS final_results(ticker TEXT, fiscal_year INT, json TEXT, computed_at TEXT,
  PRIMARY KEY(ticker, fiscal_year));
"""

CORRECTABLE = {"personal_shares_owned", "personal_ownership_percentage", "independence", "active_at_cutoff",
               "appointment_date", "departure_date", "verification_status"}
AGG_CORRECTABLE = {"reported_pct", "covers_all_active_board", "verification_status"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self):
        self.conn.close()

    # ---- companies / docs
    def upsert_company(self, ticker, name=None, exchange=None, notes=None):
        self.conn.execute("INSERT INTO companies VALUES(?,?,?,?) ON CONFLICT(ticker) DO UPDATE SET "
                          "name=COALESCE(excluded.name,name), exchange=COALESCE(excluded.exchange,exchange), "
                          "notes=COALESCE(excluded.notes,notes)", (ticker, name, exchange, notes))
        self.conn.commit()

    def upsert_report(self, d: ReportDoc):
        self.conn.execute("INSERT OR REPLACE INTO report_documents VALUES(?,?,?,?,?,?,?)",
                          (d.document_id, d.ticker, d.fiscal_year, d.sha256, d.validation_status,
                           d.model_dump_json(), now()))
        self.conn.commit()

    def get_report(self, ticker, year) -> ReportDoc | None:
        r = self.conn.execute("SELECT json FROM report_documents WHERE document_id=?", (f"{ticker}_{year}",)).fetchone()
        return ReportDoc.model_validate_json(r["json"]) if r else None

    def list_reports(self, ticker) -> list[ReportDoc]:
        rows = self.conn.execute("SELECT json FROM report_documents WHERE ticker=? ORDER BY fiscal_year", (ticker,))
        return [ReportDoc.model_validate_json(r["json"]) for r in rows]

    def save_pages(self, doc_id, pages):
        self.conn.execute("DELETE FROM document_pages WHERE document_id=?", (doc_id,))
        self.conn.executemany("INSERT INTO document_pages VALUES(?,?,?,?,?)",
                              [(doc_id, p.page, ",".join(p.tags), p.score, int(p.scanned)) for p in pages if p.score > 0])
        self.conn.commit()

    # ---- extraction (idempotent per ticker/year)
    def replace_extraction(self, ticker, year, members: list[MemberObservation], aggs: list[AggregateOwnership]):
        c = self.conn
        c.execute("DELETE FROM member_observations WHERE ticker=? AND fiscal_year=?", (ticker, year))
        c.execute("DELETE FROM aggregate_observations WHERE ticker=? AND fiscal_year=?", (ticker, year))
        ts = now()
        for m in members:
            c.execute("INSERT OR REPLACE INTO member_observations VALUES(?,?,?,?,?,?)",
                      (m.key, ticker, year, m.governance_body, m.model_dump_json(), ts))
            self._decision(m.key, m.verification_status.value, "; ".join(m.review_notes)[:2000])
        for i, a in enumerate(aggs):
            c.execute("INSERT OR REPLACE INTO aggregate_observations VALUES(?,?,?,?)",
                      (f"{ticker}:{year}:HDQT:aggregate:{i}", ticker, year, a.model_dump_json()))
        c.commit()

    def _decision(self, key, status, notes):
        last = self.conn.execute("SELECT status,notes FROM verification_decisions WHERE obs_key=? ORDER BY id DESC LIMIT 1",
                                 (key,)).fetchone()
        if not last or last["status"] != status or last["notes"] != notes:
            self.conn.execute("INSERT INTO verification_decisions(obs_key,status,notes,decided_at) VALUES(?,?,?,?)",
                              (key, status, notes, now()))

    def raw_members(self, ticker, year) -> list[MemberObservation]:
        rows = self.conn.execute("SELECT json FROM member_observations WHERE ticker=? AND fiscal_year=? ORDER BY rowid",
                                 (ticker, year))
        return [MemberObservation.model_validate_json(r["json"]) for r in rows]

    def raw_aggregates(self, ticker, year) -> list[AggregateOwnership]:
        rows = self.conn.execute("SELECT json FROM aggregate_observations WHERE ticker=? AND fiscal_year=? ORDER BY rowid",
                                 (ticker, year))
        return [AggregateOwnership.model_validate_json(r["json"]) for r in rows]

    # ---- corrections (never touch raw rows)
    def add_correction(self, obs_key, field, value, reviewer, justification):
        if not reviewer or not justification:
            raise ValueError("reviewer and justification are required")
        is_agg = ":aggregate" in obs_key
        if field not in (AGG_CORRECTABLE if is_agg else CORRECTABLE):
            raise ValueError(f"field {field!r} is not correctable")
        row = self.conn.execute("SELECT 1 FROM member_observations WHERE obs_key=?", (obs_key,)).fetchone() if not is_agg \
            else self.conn.execute("SELECT 1 FROM aggregate_observations WHERE obs_key=?", (obs_key,)).fetchone()
        if not row:
            raise KeyError(f"unknown observation {obs_key}")
        self.conn.execute("INSERT INTO manual_corrections(obs_key,field,raw_value,corrected_value,reviewer,justification,created_at)"
                          " VALUES(?,?,?,?,?,?,?)", (obs_key, field, "", str(value), reviewer, justification, now()))
        self.conn.commit()

    def corrections(self, ticker, year) -> list[sqlite3.Row]:
        prefix = f"{ticker}:{year}:"
        return list(self.conn.execute("SELECT * FROM manual_corrections WHERE obs_key LIKE ? ORDER BY id", (prefix + "%",)))

    def effective_members(self, ticker, year) -> list[MemberObservation]:
        return apply_corrections(self.raw_members(ticker, year), self.corrections(ticker, year))

    def effective_aggregates(self, ticker, year) -> list[AggregateOwnership]:
        aggs = self.raw_aggregates(ticker, year)
        ids = [r["obs_key"] for r in self.conn.execute(
            "SELECT obs_key FROM aggregate_observations WHERE ticker=? AND fiscal_year=? ORDER BY rowid", (ticker, year))]
        for c in self.corrections(ticker, year):
            if c["obs_key"] in ids:
                a = aggs[ids.index(c["obs_key"])]
                v = c["corrected_value"]
                if c["field"] == "reported_pct":
                    a.reported_pct = Decimal(v)
                elif c["field"] == "covers_all_active_board":
                    a.covers_all_active_board = v.lower() in ("1", "true", "yes")
                elif c["field"] == "verification_status":
                    a.verification_status = Status(v)
                a.review_notes.append(f"MANUAL: {c['field']}={v} by {c['reviewer']} ({c['justification']})")
        return aggs

    # ---- shares, tasks, stages, results
    def save_shares(self, o: OutstandingShares):
        self.conn.execute("INSERT OR REPLACE INTO outstanding_share_observations VALUES(?,?,?)",
                          (o.ticker, o.reporting_year, o.model_dump_json()))
        self.conn.commit()

    def get_shares(self, ticker, year) -> OutstandingShares | None:
        r = self.conn.execute("SELECT json FROM outstanding_share_observations WHERE ticker=? AND fiscal_year=?",
                              (ticker, year)).fetchone()
        return OutstandingShares.model_validate_json(r["json"]) if r else None

    def replace_tasks(self, ticker, year, tasks: list[ReviewTask]):
        """Open tasks are regenerated each run; tasks already RESOLVED stay resolved."""
        self.conn.execute("DELETE FROM review_tasks WHERE ticker=? AND fiscal_year=? AND status='OPEN'", (ticker, year))
        for t in tasks:
            self.conn.execute("INSERT OR IGNORE INTO review_tasks(ticker,fiscal_year,field,json,status) VALUES(?,?,?,?,'OPEN')",
                              (ticker, year, t.field, t.model_dump_json()))
        self.conn.commit()

    def open_tasks(self, ticker=None) -> list[ReviewTask]:
        q, a = "SELECT json FROM review_tasks WHERE status='OPEN'", ()
        if ticker:
            q, a = q + " AND ticker=?", (ticker,)
        return [ReviewTask.model_validate_json(r["json"]) for r in self.conn.execute(q + " ORDER BY ticker,fiscal_year,id", a)]

    def set_stage(self, ticker, year, stage, status, detail=""):
        self.conn.execute("INSERT OR REPLACE INTO stage_status VALUES(?,?,?,?,?,?)",
                          (ticker, year, stage, status, detail[:1000], now()))
        self.conn.commit()

    def stages(self, ticker) -> list[sqlite3.Row]:
        return list(self.conn.execute("SELECT * FROM stage_status WHERE ticker=? ORDER BY fiscal_year, stage", (ticker,)))

    def stage_ok(self, ticker, year, stage) -> bool:
        r = self.conn.execute("SELECT status FROM stage_status WHERE ticker=? AND fiscal_year=? AND stage=?",
                              (ticker, year, stage)).fetchone()
        return bool(r and r["status"] == "DONE")

    def save_result(self, r: CompanyYearResult):
        self.conn.execute("INSERT OR REPLACE INTO final_results VALUES(?,?,?,?)",
                          (r.ticker, r.year, r.model_dump_json(), now()))
        self.conn.commit()

    def get_result(self, ticker, year) -> CompanyYearResult | None:
        r = self.conn.execute("SELECT json FROM final_results WHERE ticker=? AND fiscal_year=?", (ticker, year)).fetchone()
        return CompanyYearResult.model_validate_json(r["json"]) if r else None

    def save_scoring_config(self, yaml_text: str) -> str:
        h = hashlib.sha256(yaml_text.encode()).hexdigest()[:16]
        self.conn.execute("INSERT OR IGNORE INTO scoring_configs VALUES(?,?,?)", (h, yaml_text, now()))
        self.conn.commit()
        return h

    def start_run(self, params: dict) -> int:
        cur = self.conn.execute("INSERT INTO research_runs(started_at,params) VALUES(?,?)",
                                (now(), json.dumps(params, default=str)))
        self.conn.commit()
        return cur.lastrowid

    def finish_run(self, run_id: int, summary: dict):
        self.conn.execute("UPDATE research_runs SET finished_at=?, summary=? WHERE id=?",
                          (now(), json.dumps(summary, default=str), run_id))
        self.conn.commit()


def apply_corrections(members: list[MemberObservation], corrections) -> list[MemberObservation]:
    """Layer manual corrections over raw observations (raw rows stay untouched in the DB)."""
    by_key = {m.key: m for m in members}
    for c in corrections:
        m = by_key.get(c["obs_key"])
        if not m:
            continue
        f, v = c["field"], c["corrected_value"]
        raw = getattr(m, f)
        if f == "personal_shares_owned":
            new = None if v in ("", "None", "null") else int(v)
        elif f == "personal_ownership_percentage":
            new = None if v in ("", "None", "null") else Decimal(v)
        elif f == "independence":
            new = Independence(v)
        elif f == "active_at_cutoff":
            new = v.lower() in ("1", "true", "yes")
        elif f in ("appointment_date", "departure_date"):
            new = parse_one_date(v) or (None if v in ("", "None", "null") else None)
        elif f == "verification_status":
            new = Status(v)
        else:
            continue
        m.corrected_fields.setdefault(f, {"raw": str(raw), "corrected": str(new)})["corrected"] = str(new)
        setattr(m, f, new)
        m.review_notes.append(f"MANUAL: {f} {raw!r} -> {new!r} by {c['reviewer']} at {c['created_at']}: {c['justification']}")
        if f != "verification_status":
            m.verification_status = Status.VERIFIED       # human-reviewed value
    for m in members:
        if m.governance_body == "BKS" and m.corrected_fields:
            conf, basis, st, _ = classify_zero_ownership(m.personal_shares_owned, m.personal_ownership_percentage,
                                                         m.reported_ownership_category)
            m.zero_ownership_confirmed, m.zero_ownership_basis, m.ownership_information_status = conf, basis, st
    return members
