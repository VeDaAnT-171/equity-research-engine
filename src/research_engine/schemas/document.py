from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Optional

from pydantic import Field, model_validator

from .common import DOCUMENT_ID_PATTERN, FISCAL_PERIOD_LABEL_PATTERN, SLUG_PATTERN, StrictModel


class DocumentType(str, Enum):
    ANNUAL_REPORT = "annual_report"
    QUARTERLY_REPORT = "quarterly_report"
    EARNINGS_RELEASE = "earnings_release"
    INVESTOR_PRESENTATION = "investor_presentation"
    EARNINGS_TRANSCRIPT = "earnings_transcript"
    PROXY = "proxy"
    REGULATORY_FILING = "regulatory_filing"
    STRUCTURED_FILING = "structured_filing"  # XBRL / machine-readable filings
    SUPPLEMENT = "supplement"
    INVESTOR_RELATIONS_SITE = "investor_relations_site"
    CONSENSUS_ESTIMATES = "consensus_estimates"
    MARKET_DATA = "market_data"
    OTHER = "other"


class DocumentStatus(str, Enum):
    REGISTERED = "registered"
    RETRIEVED = "retrieved"
    PARSED = "parsed"
    EXTRACTED = "extracted"
    FAILED = "failed"


ALLOWED_TRANSITIONS: dict[DocumentStatus, frozenset[DocumentStatus]] = {
    DocumentStatus.REGISTERED: frozenset({DocumentStatus.RETRIEVED, DocumentStatus.FAILED}),
    DocumentStatus.RETRIEVED: frozenset({DocumentStatus.PARSED, DocumentStatus.FAILED}),
    DocumentStatus.PARSED: frozenset({DocumentStatus.EXTRACTED, DocumentStatus.FAILED}),
    # re-parse after a parser upgrade
    DocumentStatus.EXTRACTED: frozenset({DocumentStatus.PARSED, DocumentStatus.FAILED}),
    # explicit retry
    DocumentStatus.FAILED: frozenset({DocumentStatus.REGISTERED}),
}

_HAS_CONTENT = {DocumentStatus.RETRIEVED, DocumentStatus.PARSED, DocumentStatus.EXTRACTED}


class DocumentRecord(StrictModel):
    document_id: str = Field(pattern=DOCUMENT_ID_PATTERN)
    company_id: str = Field(pattern=SLUG_PATTERN)
    document_type: DocumentType
    source_url: Optional[str] = None
    local_source_path: Optional[str] = None
    raw_path: Optional[str] = None
    publication_date: Optional[date] = None
    fiscal_period: Optional[str] = Field(default=None, pattern=FISCAL_PERIOD_LABEL_PATTERN)
    retrieval_timestamp: Optional[datetime] = None
    file_hash: Optional[str] = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    content_type: Optional[str] = None
    size_bytes: Optional[int] = Field(default=None, ge=0)
    status: DocumentStatus
    parser_version: Optional[str] = None
    error: Optional[str] = None
    registered_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def _consistency(self) -> "DocumentRecord":
        if (self.source_url is None) == (self.local_source_path is None):
            raise ValueError("a document must have exactly one of source_url / local_source_path")
        if self.status in _HAS_CONTENT and not (self.file_hash and self.raw_path and self.retrieval_timestamp):
            raise ValueError(f"status '{self.status.value}' requires file_hash, raw_path and retrieval_timestamp")
        if self.status in {DocumentStatus.PARSED, DocumentStatus.EXTRACTED} and not self.parser_version:
            raise ValueError(f"status '{self.status.value}' requires parser_version")
        if self.status is DocumentStatus.FAILED and not self.error:
            raise ValueError("failed documents must carry an error message")
        return self
