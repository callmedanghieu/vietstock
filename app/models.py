"""Structured contracts shared by agents. Percentages are PERCENTAGE VALUES (2.5 == 2.5%)."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class Status(str, Enum):
    VERIFIED = "VERIFIED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    UNAVAILABLE = "UNAVAILABLE"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    INCOMPLETE = "INCOMPLETE"
    PENDING = "PENDING"


class Independence(str, Enum):
    TRUE = "TRUE"
    FALSE = "FALSE"      # only when the report explicitly says "not independent"
    UNKNOWN = "UNKNOWN"


class Evidence(BaseModel):
    document_id: str
    page: int
    quote: str
    table_ref: Optional[str] = None


class ReportDoc(BaseModel):
    ticker: str
    company_name: Optional[str] = None
    exchange: Optional[str] = None
    fiscal_year: int
    report_type: str = "annual_report"
    source_url: Optional[str] = None
    source_category: str = "UNKNOWN"      # VIETSTOCK | COMPANY_IR | EXCHANGE | MANUAL | LOCAL_INBOX
    publication_date: Optional[str] = None
    downloaded_at: Optional[str] = None
    local_path: Optional[str] = None
    sha256: Optional[str] = None
    validation_status: str = "PENDING"    # VERIFIED | NEEDS_REVIEW | REJECTED | PENDING
    validation_notes: list[str] = Field(default_factory=list)

    @property
    def document_id(self) -> str:
        return f"{self.ticker}_{self.fiscal_year}"


class PageData(BaseModel):
    page: int
    text: str = ""
    tables: list[list[list[str]]] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)   # HDQT, BKS, OWNERSHIP, SHARE_CAPITAL
    score: int = 0
    scanned: bool = False


class ParsedDocument(BaseModel):
    document_id: str
    sha256: str
    n_pages: int
    is_scanned: bool
    ocr_used: bool = False
    pages: list[PageData]
    warnings: list[str] = Field(default_factory=list)


class MemberObservation(BaseModel):
    """One person in one governance body for one fiscal-year report (raw extraction + validation)."""
    ticker: str
    reporting_year: int
    person_name: str
    governance_body: str                   # HDQT | BKS
    reported_title: Optional[str] = None
    independence: Independence = Independence.UNKNOWN
    appointment_date: Optional[date] = None
    departure_date: Optional[date] = None
    appointment_note: Optional[str] = None
    in_former_section: bool = False
    active_at_cutoff: Optional[bool] = None
    personal_shares_owned: Optional[int] = None
    personal_ownership_percentage: Optional[Decimal] = None
    ownership_raw_shares: Optional[str] = None
    ownership_raw_pct: Optional[str] = None
    reported_ownership_category: str = "personal"   # personal | related_person | combined | unknown
    membership_effective_date: Optional[str] = None  # ISO date or NOT_DISCLOSED
    ownership_effective_date: Optional[str] = None
    zero_ownership_confirmed: Optional[bool] = None
    zero_ownership_basis: Optional[str] = None       # ZERO_SHARES | ZERO_PERCENTAGE
    ownership_information_status: Status = Status.PENDING
    evidence: Optional[Evidence] = None
    verification_status: Status = Status.PENDING
    review_notes: list[str] = Field(default_factory=list)
    corrected_fields: dict = Field(default_factory=dict)   # field -> {"raw":..., "corrected":...}

    @property
    def key(self) -> str:
        from .utils import name_key
        return f"{self.ticker}:{self.reporting_year}:{self.governance_body}:{name_key(self.person_name)}"


class AggregateOwnership(BaseModel):
    ticker: str
    reporting_year: int
    reported_pct: Optional[Decimal] = None
    raw_text: str = ""
    covers_all_active_board: bool = False
    effective_date: Optional[str] = None
    evidence: Optional[Evidence] = None
    verification_status: Status = Status.PENDING
    review_notes: list[str] = Field(default_factory=list)


class OutstandingShares(BaseModel):
    ticker: str
    reporting_year: int
    final_trading_date: Optional[date] = None
    outstanding_shares: Optional[int] = None
    issued_shares: Optional[int] = None
    treasury_shares: Optional[int] = None
    adjustments: Optional[str] = None
    source: Optional[str] = None
    source_category: Optional[str] = None
    effective_date: Optional[date] = None
    verification_status: Status = Status.INCOMPLETE
    review_notes: list[str] = Field(default_factory=list)


class ReviewTask(BaseModel):
    ticker: str
    fiscal_year: int
    field: str
    extracted_value: str = ""
    source_page: Optional[int] = None
    evidence: str = ""
    ambiguity: str = ""
    suggested_resolution: str = ""
    status: str = "OPEN"


class BodyStructure(BaseModel):
    """What the report says about governance structure for a year."""
    has_hdqt_table: bool = False
    has_bks_table: bool = False
    bks_absent_declared: bool = False       # report states no Ban Kiểm soát (e.g. audit committee model)
    bks_absent_quote: Optional[str] = None
    membership_date: Optional[str] = None
    membership_date_page: Optional[int] = None


class ExtractionResult(BaseModel):
    ticker: str
    year: int
    members: list[MemberObservation] = Field(default_factory=list)
    aggregates: list[AggregateOwnership] = Field(default_factory=list)
    structure: BodyStructure = Field(default_factory=BodyStructure)
    warnings: list[str] = Field(default_factory=list)
    status: str = "OK"


class IndicatorResult(BaseModel):
    name: str
    value: Optional[Decimal] = None          # percentage value
    numerator: Optional[int] = None
    denominator: Optional[int] = None
    status: Status = Status.PENDING
    effective_date: Optional[str] = None
    reported_aggregate: Optional[Decimal] = None
    calculated_sum: Optional[Decimal] = None
    notes: list[str] = Field(default_factory=list)
    inputs: list[dict] = Field(default_factory=list)   # raw values used, per person


class CompanyYearResult(BaseModel):
    ticker: str
    year: int
    ined: IndicatorResult
    bks: IndicatorResult
    board_own: IndicatorResult
    scores: dict = Field(default_factory=dict)
    overall_score: Optional[float] = None
    score_status: str = "PENDING_RUBRIC"
    completeness: str = "INCOMPLETE"
    notes: list[str] = Field(default_factory=list)
