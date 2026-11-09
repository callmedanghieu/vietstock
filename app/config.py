"""Configuration loading. All methodology switches live in config/research.yaml."""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
TICKER_RE = re.compile(r"^[A-Z0-9]{3,4}$")


def validate_ticker(ticker: str) -> str:
    t = (ticker or "").strip().upper()
    if not TICKER_RE.match(t):
        raise ValueError(f"Invalid ticker {ticker!r}: expected 3-4 letters/digits")
    return t


def safe_join(base: Path, *parts: str) -> Path:
    """Join and refuse anything that escapes `base` (directory traversal guard)."""
    base = base.resolve()
    p = base.joinpath(*parts).resolve()
    if base != p and base not in p.parents:
        raise ValueError(f"Path escapes base directory: {p}")
    return p


class Settings:
    def __init__(self, research: dict, scoring: dict, companies: dict, data_dir: Path, root: Path = ROOT):
        self.research = research
        self.scoring = scoring
        self.companies = companies
        self.root = root
        self.data_dir = data_dir
        self.reports_dir = data_dir / "reports"
        self.extracted_dir = data_dir / "extracted"
        self.reviewed_dir = data_dir / "reviewed"
        self.output_dir = data_dir / "output"
        self.db_path = data_dir / "governance.sqlite"
        for d in (self.reports_dir, self.extracted_dir, self.reviewed_dir, self.output_dir):
            d.mkdir(parents=True, exist_ok=True)

    def get(self, dotted: str, default: Any = None) -> Any:
        cur: Any = self.research
        for k in dotted.split("."):
            if not isinstance(cur, dict) or k not in cur:
                return default
            cur = cur[k]
        return cur

    def path(self, rel: str) -> Path:
        """Configured data paths are relative to the data directory (never the repo root)."""
        p = Path(rel)
        return p if p.is_absolute() else self.data_dir / p


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def load_settings(config_dir: Path | None = None, data_dir: Path | None = None,
                  overrides: dict | None = None) -> Settings:
    cdir = config_dir or ROOT / "config"
    research = _load(cdir / "research.yaml")
    for dotted, val in (overrides or {}).items():  # e.g. {"measurement.membership_date_policy": "fiscal_year_end"}
        cur = research
        keys = dotted.split(".")
        for k in keys[:-1]:
            cur = cur.setdefault(k, {})
        cur[keys[-1]] = val
    if research.get("ownership", {}).get("derive_pct_from_shares"):
        raise ValueError("ownership.derive_pct_from_shares=true is not implemented (spec forbids automatic derivation)")
    scoring = _load(cdir / "scoring.yaml")
    companies = _load(cdir / "companies.yaml") if (cdir / "companies.yaml").exists() else {}
    dd = data_dir or Path(os.environ.get("VGA_DATA_DIR", ROOT / "data"))
    return Settings(research, scoring, companies, Path(dd).resolve())
