"""Project configuration: company identity, sources and research settings."""

from __future__ import annotations

import ipaddress
import re
from datetime import date
from pathlib import Path
from typing import Iterator, Literal, Optional
from urllib.parse import urlparse

from pydantic import Field, field_validator, model_validator

from .common import CURRENCY_PATTERN, FISCAL_PERIOD_LABEL_PATTERN, SLUG_PATTERN, StrictModel, slugify
from .document import DocumentType
from .framework import ValuationFamily


def validate_public_https_url(v: str) -> str:
    """Static URL checks. The downloader additionally checks resolved IPs (DNS rebinding)."""
    parsed = urlparse(v)
    if parsed.scheme != "https":
        raise ValueError(f"only https URLs are accepted (got scheme {parsed.scheme!r} in {v!r})")
    host = parsed.hostname
    if not host:
        raise ValueError(f"URL has no host: {v!r}")
    if parsed.username or parsed.password:
        raise ValueError("URLs with embedded credentials are rejected; use environment variables")
    if host == "localhost" or host.endswith((".local", ".internal", ".localhost")):
        raise ValueError(f"refusing internal host {host!r}")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return v
    if not ip.is_global:
        raise ValueError(f"refusing non-public IP address {host!r}")
    return v


class SourceRef(StrictModel):
    url: Optional[str] = None
    path: Optional[Path] = None
    label: Optional[str] = None
    fiscal_period: Optional[str] = Field(default=None, pattern=FISCAL_PERIOD_LABEL_PATTERN)
    publication_date: Optional[date] = None

    @field_validator("url", mode="before")
    @classmethod
    def _blank_url(cls, v):
        return None if isinstance(v, str) and not v.strip() else v

    @field_validator("url")
    @classmethod
    def _safe_url(cls, v: Optional[str]) -> Optional[str]:
        return None if v is None else validate_public_https_url(v)

    @model_validator(mode="after")
    def _one_location(self) -> "SourceRef":
        if (self.url is None) == (self.path is None):
            raise ValueError("a source needs exactly one of 'url' or 'path'")
        return self

    @property
    def location(self) -> str:
        return self.url if self.url is not None else str(self.path)


_SINGLE_SOURCES: dict[str, DocumentType] = {
    "investor_relations": DocumentType.INVESTOR_RELATIONS_SITE,
    "optional_consensus": DocumentType.CONSENSUS_ESTIMATES,
    "optional_market_data": DocumentType.MARKET_DATA,
}
_LIST_SOURCES: dict[str, DocumentType] = {
    "structured_filings": DocumentType.STRUCTURED_FILING,
    "annual_reports": DocumentType.ANNUAL_REPORT,
    "quarterly_reports": DocumentType.QUARTERLY_REPORT,
    "earnings_releases": DocumentType.EARNINGS_RELEASE,
    "earnings_presentations": DocumentType.INVESTOR_PRESENTATION,
    "earnings_transcripts": DocumentType.EARNINGS_TRANSCRIPT,
    "regulatory_filings": DocumentType.REGULATORY_FILING,
    "other_documents": DocumentType.OTHER,
}
NON_PRIMARY_TYPES = frozenset(_SINGLE_SOURCES.values())


def _is_blank_ref(v) -> bool:
    return isinstance(v, dict) and not any(str(v.get(k) or "").strip() for k in ("url", "path"))


class SourcesConfig(StrictModel):
    investor_relations: Optional[SourceRef] = None
    optional_consensus: Optional[SourceRef] = None
    optional_market_data: Optional[SourceRef] = None
    structured_filings: tuple[SourceRef, ...] = ()
    annual_reports: tuple[SourceRef, ...] = ()
    quarterly_reports: tuple[SourceRef, ...] = ()
    earnings_releases: tuple[SourceRef, ...] = ()
    earnings_presentations: tuple[SourceRef, ...] = ()
    earnings_transcripts: tuple[SourceRef, ...] = ()
    regulatory_filings: tuple[SourceRef, ...] = ()
    other_documents: tuple[SourceRef, ...] = ()

    @model_validator(mode="before")
    @classmethod
    def _drop_blank(cls, data):
        # Templates legitimately contain `url: ""` placeholders; treat them as absent.
        if not isinstance(data, dict):
            return data
        cleaned = dict(data)
        for key in _SINGLE_SOURCES:
            if _is_blank_ref(cleaned.get(key)):
                cleaned[key] = None
        for key in _LIST_SOURCES:
            value = cleaned.get(key)
            if value is None:
                cleaned[key] = ()
            elif isinstance(value, list):
                cleaned[key] = [item for item in value if not _is_blank_ref(item)]
        return cleaned

    @model_validator(mode="after")
    def _unique(self) -> "SourcesConfig":
        seen: set[str] = set()
        for _, ref in self.iter_documents():
            if ref.location in seen:
                raise ValueError(f"source listed more than once: {ref.location}")
            seen.add(ref.location)
        return self

    def iter_documents(self) -> Iterator[tuple[DocumentType, SourceRef]]:
        for key, doc_type in _SINGLE_SOURCES.items():
            ref = getattr(self, key)
            if ref is not None:
                yield doc_type, ref
        for key, doc_type in _LIST_SOURCES.items():
            for ref in getattr(self, key):
                yield doc_type, ref


class CompanyIdentifiers(StrictModel):
    cik: Optional[str] = None
    lei: Optional[str] = Field(default=None, pattern=r"^[A-Z0-9]{18}[0-9]{2}$")
    isin: Optional[str] = Field(default=None, pattern=r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")

    @field_validator("cik", mode="before")
    @classmethod
    def _cik(cls, v):
        if v is None or (isinstance(v, str) and not v.strip()):
            return None
        s = str(v).strip()
        if not s.isdigit() or len(s) > 10:
            raise ValueError("CIK must be 1-10 digits")
        return s.zfill(10)


class CompanyProfile(StrictModel):
    name: str = Field(min_length=1)
    ticker: str
    exchange: str = Field(min_length=1)
    country: str = Field(min_length=1)
    sector: Optional[str] = None  # hint only; classification is evidence-based
    industry_framework: Optional[str] = Field(default=None, pattern=SLUG_PATTERN)  # explicit override
    identifiers: CompanyIdentifiers = Field(default_factory=CompanyIdentifiers)
    reporting_currency: Optional[str] = Field(default=None, pattern=CURRENCY_PATTERN)
    fiscal_year_end_month: Optional[int] = Field(default=None, ge=1, le=12)
    # How fiscal years are labelled: by the calendar year they end in (most issuers) or begin in.
    fiscal_year_convention: Literal["end_year", "start_year"] = "end_year"
    company_id: Optional[str] = Field(default=None, pattern=SLUG_PATTERN)

    @field_validator("ticker", mode="before")
    @classmethod
    def _ticker(cls, v):
        s = str(v).strip().upper()
        if not re.fullmatch(r"[A-Z0-9][A-Z0-9.\-]{0,11}", s):
            raise ValueError(f"invalid ticker {v!r}")
        return s

    @property
    def resolved_company_id(self) -> str:
        return self.company_id or slugify(self.exchange, self.ticker)


class ResearchSettings(StrictModel):
    historical_years: int = Field(default=10, ge=1, le=30)
    forecast_years: int = Field(default=5, ge=1, le=15)
    valuation_methods: tuple[ValuationFamily, ...] = (
        ValuationFamily.RELATIVE, ValuationFamily.INTRINSIC, ValuationFamily.SCENARIO,
    )

    @field_validator("valuation_methods")
    @classmethod
    def _families(cls, v):
        if not v:
            raise ValueError("at least one valuation family is required")
        if len(set(v)) != len(v):
            raise ValueError("duplicate valuation families")
        return v


class ProjectConfig(StrictModel):
    schema_version: Literal["1"] = "1"
    company: CompanyProfile
    sources: SourcesConfig
    research: ResearchSettings = Field(default_factory=ResearchSettings)

    @field_validator("schema_version", mode="before")
    @classmethod
    def _version_str(cls, v):
        return str(v)

    @model_validator(mode="after")
    def _has_primary_sources(self) -> "ProjectConfig":
        if not any(t not in NON_PRIMARY_TYPES for t, _ in self.sources.iter_documents()):
            raise ValueError(
                "no primary source documents configured: add at least one of structured_filings, "
                "annual_reports, quarterly_reports, earnings_releases, earnings_presentations, "
                "earnings_transcripts, regulatory_filings or other_documents"
            )
        return self

    @property
    def company_id(self) -> str:
        return self.company.resolved_company_id
