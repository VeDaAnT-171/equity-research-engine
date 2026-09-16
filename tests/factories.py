"""Builders for canonical facts in unit tests. All values are synthetic."""

from datetime import date
from decimal import Decimal

from research_engine.schemas import (
    ExtractionMethod, FinancialFact, FiscalPeriodCode, Period, PeriodType, Provenance, SourceLocation, make_fact_id,
)

DOC = "doc_0123456789abcdef"
COMPANY = "test-co"
_accession = iter(range(1, 10_000_000))


def duration(fy, code, start, end):
    return Period(period_type=PeriodType.DURATION, start=date.fromisoformat(start), end=date.fromisoformat(end),
                  fiscal_year=fy, fiscal_period=FiscalPeriodCode(code))


def instant(fy, code, end):
    return Period(period_type=PeriodType.INSTANT, end=date.fromisoformat(end), fiscal_year=fy,
                  fiscal_period=FiscalPeriodCode(code))


def reported(metric, value, period, unit="USD", filed="2026-02-01"):
    currency = unit[:3] if unit[:3].isupper() and unit[:3].isalpha() and unit not in ("pure",) else None
    n = next(_accession)
    accession = f"0000000001-26-{n:06d}"
    return FinancialFact(
        fact_id=make_fact_id(COMPANY, metric, period, Provenance.REPORTED, f"{metric}|{accession}"),
        company_id=COMPANY, metric_id=metric, value=Decimal(str(value)), unit=unit, currency=currency,
        period=period, provenance=Provenance.REPORTED, extraction_method=ExtractionMethod.XBRL,
        source=SourceLocation(document_id=DOC, xbrl_concept=f"us-gaap:{metric.title().replace('_', '')}",
                              filing_accession=accession, filing_form="10-K", filed_date=date.fromisoformat(filed)),
    )


def fy(year, month_end=12):
    return duration(year, "FY", f"{year}-01-01", f"{year}-12-31")


def fy_end(year):
    return instant(year, "FY", f"{year}-12-31")
