from __future__ import annotations

from datetime import date, datetime
from enum import Enum

from pydantic import Field, model_validator

from .common import (
    DOCUMENT_ID_PATTERN,
    FISCAL_PERIOD_LABEL_PATTERN,
    SLUG_PATTERN,
    StrictModel,
)


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
    SUPERSEDED = "superseded"  # a newer retrieval of the same source replaced this version


_S = DocumentStatus
ALLOWED_TRANSITIONS: dict[DocumentStatus, frozenset[DocumentStatus]] = {
    _S.REGISTERED: frozenset({_S.RETRIEVED, _S.FAILED}),
    _S.RETRIEVED: frozenset({_S.PARSED, _S.FAILED, _S.SUPERSEDED}),
    _S.PARSED: frozenset({_S.EXTRACTED, _S.FAILED, _S.SUPERSEDED}),
    # re-parse after a parser upgrade
    _S.EXTRACTED: frozenset({_S.PARSED, _S.FAILED, _S.SUPERSEDED}),
    # explicit retry
    _S.FAILED: frozenset({_S.REGISTERED}),
    _S.SUPERSEDED: frozenset(),  # terminal: kept for lineage of facts extracted from it
}

_HAS_CONTENT = {_S.RETRIEVED, _S.PARSED, _S.EXTRACTED, _S.SUPERSEDED}


class DocumentRecord(StrictModel):
    document_id: str = Field(pattern=DOCUMENT_ID_PATTERN)
    company_id: str = Field(pattern=SLUG_PATTERN)
    document_type: DocumentType
    source_url: str | None = None
    local_source_path: str | None = None
    raw_path: str | None = None
    publication_date: date | None = None
    fiscal_period: str | None = Field(default=None, pattern=FISCAL_PERIOD_LABEL_PATTERN)
    retrieval_timestamp: datetime | None = None
    file_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    content_type: str | None = None
    size_bytes: int | None = Field(default=None, ge=0)
    status: DocumentStatus
    parser_version: str | None = None
    error: str | None = None
    supersedes: str | None = Field(default=None, pattern=DOCUMENT_ID_PATTERN)
    registered_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def _consistency(self) -> DocumentRecord:
        if (self.source_url is None) == (self.local_source_path is None):
            raise ValueError("a document must have exactly one of source_url / local_source_path")
        if self.status in _HAS_CONTENT and not (self.file_hash and self.raw_path and self.retrieval_timestamp):
            raise ValueError(f"status '{self.status.value}' requires file_hash, raw_path and retrieval_timestamp")
        if self.supersedes == self.document_id:
            raise ValueError("a document cannot supersede itself")
        if self.status in {DocumentStatus.PARSED, DocumentStatus.EXTRACTED} and not self.parser_version:
            raise ValueError(f"status '{self.status.value}' requires parser_version")
        if self.status is DocumentStatus.FAILED and not self.error:
            raise ValueError("failed documents must carry an error message")
        return self
