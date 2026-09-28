"""Canonical financial fact schema with lineage enforced by construction."""

from __future__ import annotations

import hashlib
from datetime import date
from decimal import Decimal
from enum import Enum

from pydantic import Field, model_validator

from ..expressions import referenced_names
from .common import (
    CURRENCY_PATTERN,
    DOCUMENT_ID_PATTERN,
    FACT_ID_PATTERN,
    METRIC_ID_PATTERN,
    SLUG_PATTERN,
    XBRL_CONCEPT_PATTERN,
    StrictModel,
)


class PeriodType(str, Enum):
    DURATION = "duration"  # income statement / cash flow: a span of time
    INSTANT = "instant"    # balance sheet: a point in time


class FiscalPeriodCode(str, Enum):
    FY = "FY"
    Q1 = "Q1"
    Q2 = "Q2"
    Q3 = "Q3"
    Q4 = "Q4"
    H1 = "H1"
    H2 = "H2"
    M9 = "9M"  # nine-month year-to-date (10-Q cash flow statements report cumulative periods)


class Period(StrictModel):
    period_type: PeriodType
    start: date | None = None
    end: date
    fiscal_year: int = Field(ge=1900, le=2200)
    fiscal_period: FiscalPeriodCode

    @model_validator(mode="after")
    def _shape(self) -> Period:
        if self.period_type is PeriodType.DURATION:
            if self.start is None:
                raise ValueError("duration periods require a start date")
            if self.start >= self.end:
                raise ValueError("period start must be before period end")
        elif self.start is not None:
            raise ValueError("instant periods (balance-sheet values) must not have a start date")
        return self

    @property
    def label(self) -> str:
        if self.fiscal_period is FiscalPeriodCode.FY:
            return f"FY{self.fiscal_year}"
        return f"{self.fiscal_period.value}-{self.fiscal_year}"


class Provenance(str, Enum):
    REPORTED = "reported"
    DERIVED = "derived"


class ExtractionMethod(str, Enum):
    XBRL = "xbrl"
    PDF_TABLE = "pdf_table"
    HTML_TABLE = "html_table"
    TEXT = "text"
    CSV = "csv"
    MANUAL = "manual"
    COMPUTED = "computed"


class SourceLocation(StrictModel):
    document_id: str = Field(pattern=DOCUMENT_ID_PATTERN)
    page: int | None = Field(default=None, ge=1)
    table: str | None = None
    xbrl_concept: str | None = Field(default=None, pattern=XBRL_CONCEPT_PATTERN)
    xbrl_context: str | None = None
    # Filing that originally disclosed the value (a companyfacts snapshot aggregates many filings)
    filing_accession: str | None = Field(default=None, pattern=r"^\d{10}-\d{2}-\d{6}$")
    filing_form: str | None = None
    filed_date: date | None = None
    source_text: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def _pinned(self) -> SourceLocation:
        if not (self.page or self.table or self.xbrl_concept):
            raise ValueError(
                "a source location must pin the value to a page, table, or XBRL concept; "
                "a bare document reference is not traceable lineage"
            )
        return self


class FinancialFact(StrictModel):
    fact_id: str = Field(pattern=FACT_ID_PATTERN)
    company_id: str = Field(pattern=SLUG_PATTERN)
    metric_id: str = Field(pattern=METRIC_ID_PATTERN)
    value: Decimal
    unit: str = Field(min_length=1)          # e.g. USD, USD/share, shares, pure
    currency: str | None = Field(default=None, pattern=CURRENCY_PATTERN)
    period: Period
    provenance: Provenance
    extraction_method: ExtractionMethod
    source: SourceLocation | None = None  # reported facts
    inputs: tuple[str, ...] = ()             # derived facts: input fact_ids
    formula: str | None = None            # derived facts
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    notes: str | None = None

    @model_validator(mode="after")
    def _lineage(self) -> FinancialFact:
        if not self.value.is_finite():
            raise ValueError("value must be a finite number")
        if self.provenance is Provenance.REPORTED:
            if self.source is None:
                raise ValueError("reported facts must carry a source location")
            if self.inputs or self.formula:
                raise ValueError("reported facts cannot have inputs or a formula; record a separate derived fact")
            if self.extraction_method is ExtractionMethod.COMPUTED:
                raise ValueError("reported facts cannot use extraction_method 'computed'")
        else:
            if self.source is not None:
                raise ValueError("derived facts reference input facts, not documents")
            if not self.inputs or not self.formula:
                raise ValueError("derived facts require input fact ids and a formula")
            if self.extraction_method is not ExtractionMethod.COMPUTED:
                raise ValueError("derived facts must use extraction_method 'computed'")
            referenced_names(self.formula)
        if self.currency and not self.unit.startswith(self.currency):
            raise ValueError(f"unit {self.unit!r} is inconsistent with currency {self.currency!r}")
        return self


def make_fact_id(company_id: str, metric_id: str, period: Period, provenance: Provenance, lineage_key: str) -> str:
    """Deterministic id. Same metric/period from different documents (e.g. a restatement)
    yields different ids, so both versions coexist; nothing is overwritten."""
    raw = "|".join([
        company_id, metric_id, period.period_type.value, str(period.start or ""), str(period.end),
        period.label, provenance.value, lineage_key,
    ])
    return "fact_" + hashlib.sha256(raw.encode()).hexdigest()[:16]
