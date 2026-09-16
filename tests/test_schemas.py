from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from research_engine.schemas import (
    Assumption, AssumptionType, DocumentRecord, DocumentStatus, DocumentType, ExtractionMethod, FinancialFact,
    FiscalPeriodCode, Period, PeriodType, Provenance, SourceLocation, make_fact_id,
)

DOC = "doc_0123456789abcdef"
FY25 = Period(period_type=PeriodType.DURATION, start=date(2025, 1, 1), end=date(2025, 12, 31),
              fiscal_year=2025, fiscal_period=FiscalPeriodCode.FY)


def reported(**overrides):
    base = dict(
        fact_id=make_fact_id("nyse-tst", "revenue", FY25, Provenance.REPORTED, f"{DOC}:us-gaap:Revenues"),
        company_id="nyse-tst", metric_id="revenue", value=Decimal("1000"), unit="USD", currency="USD",
        period=FY25, provenance=Provenance.REPORTED, extraction_method=ExtractionMethod.XBRL,
        source=SourceLocation(document_id=DOC, xbrl_concept="us-gaap:Revenues"),
    )
    base.update(overrides)
    return FinancialFact(**base)


class TestPeriod:
    def test_label(self):
        assert FY25.label == "FY2025"
        q = Period(period_type=PeriodType.DURATION, start=date(2025, 4, 1), end=date(2025, 6, 30),
                   fiscal_year=2025, fiscal_period=FiscalPeriodCode.Q2)
        assert q.label == "Q2-2025"

    def test_instant_cannot_have_start(self):
        with pytest.raises(ValidationError, match="instant"):
            Period(period_type=PeriodType.INSTANT, start=date(2025, 1, 1), end=date(2025, 12, 31),
                   fiscal_year=2025, fiscal_period=FiscalPeriodCode.FY)

    def test_duration_needs_ordered_dates(self):
        with pytest.raises(ValidationError, match="before"):
            Period(period_type=PeriodType.DURATION, start=date(2026, 1, 1), end=date(2025, 12, 31),
                   fiscal_year=2025, fiscal_period=FiscalPeriodCode.FY)


class TestFinancialFact:
    def test_valid_reported(self):
        assert reported().value == Decimal("1000")

    def test_reported_requires_source(self):
        with pytest.raises(ValidationError, match="source location"):
            reported(source=None)

    def test_reported_cannot_have_formula(self):
        with pytest.raises(ValidationError, match="cannot have inputs"):
            reported(formula="a + b", inputs=("fact_0000000000000001",))

    def test_derived_rules(self):
        ok = reported(provenance=Provenance.DERIVED, source=None, extraction_method=ExtractionMethod.COMPUTED,
                      inputs=("fact_0000000000000001", "fact_0000000000000002"), formula="a - b",
                      metric_id="gross_profit")
        assert ok.provenance is Provenance.DERIVED
        with pytest.raises(ValidationError, match="require input"):
            reported(provenance=Provenance.DERIVED, source=None, extraction_method=ExtractionMethod.COMPUTED)
        with pytest.raises(ValidationError, match="not documents"):
            reported(provenance=Provenance.DERIVED, extraction_method=ExtractionMethod.COMPUTED,
                     inputs=("fact_0000000000000001",), formula="a")

    def test_unsafe_formula_rejected(self):
        with pytest.raises(ValidationError, match="disallowed"):
            reported(provenance=Provenance.DERIVED, source=None, extraction_method=ExtractionMethod.COMPUTED,
                     inputs=("fact_0000000000000001",), formula="__import__('os').system('x')")

    def test_bare_document_reference_is_not_lineage(self):
        with pytest.raises(ValidationError, match="page, table, or XBRL"):
            SourceLocation(document_id=DOC)

    def test_unit_currency_consistency_and_finiteness(self):
        with pytest.raises(ValidationError, match="inconsistent"):
            reported(unit="EUR")
        with pytest.raises(ValidationError, match="finite"):
            reported(value=Decimal("NaN"))

    def test_fact_ids_distinguish_restatements(self):
        a = make_fact_id("c", "revenue", FY25, Provenance.REPORTED, "doc_a:us-gaap:Revenues")
        b = make_fact_id("c", "revenue", FY25, Provenance.REPORTED, "doc_b:us-gaap:Revenues")
        assert a != b and a == make_fact_id("c", "revenue", FY25, Provenance.REPORTED, "doc_a:us-gaap:Revenues")


class TestAssumption:
    base = dict(assumption_id="rev_growth.fy2026", company_id="nyse-tst", description="Revenue growth",
                value=Decimal("0.05"), unit="ratio", period="FY2026")

    @pytest.mark.parametrize("atype, fragment", [
        (AssumptionType.CONSENSUS, "never inferred"),
        (AssumptionType.MANAGEMENT_GUIDANCE, "never inferred"),
        (AssumptionType.HISTORICAL, "source_fact_ids"),
        (AssumptionType.DERIVED, "source_fact_ids"),
        (AssumptionType.ANALYST_ASSUMPTION, "rationale"),
        (AssumptionType.SCENARIO, "scenario name"),
    ])
    def test_type_rules(self, atype, fragment):
        with pytest.raises(ValidationError, match=fragment):
            Assumption(**self.base, type=atype)

    def test_valid_variants(self):
        assert Assumption(**self.base, type=AssumptionType.CONSENSUS, source_document_id=DOC).is_set
        a = Assumption(**{**self.base, "value": None}, type=AssumptionType.ANALYST_ASSUMPTION, rationale="pending")
        assert not a.is_set
        Assumption(**self.base, type=AssumptionType.SCENARIO, scenario="downside", rationale="deposit beta shock")

    def test_scenario_binding_only_for_scenarios(self):
        with pytest.raises(ValidationError, match="only 'scenario'"):
            Assumption(**self.base, type=AssumptionType.ANALYST_ASSUMPTION, rationale="x", scenario="base")


class TestDocumentRecord:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def test_needs_exactly_one_location(self):
        with pytest.raises(ValidationError, match="exactly one"):
            DocumentRecord(document_id=DOC, company_id="c", document_type=DocumentType.OTHER,
                           status=DocumentStatus.REGISTERED, registered_at=self.now, updated_at=self.now)

    def test_retrieved_requires_hash(self):
        with pytest.raises(ValidationError, match="file_hash"):
            DocumentRecord(document_id=DOC, company_id="c", document_type=DocumentType.OTHER,
                           source_url="https://example.com/x", status=DocumentStatus.RETRIEVED,
                           registered_at=self.now, updated_at=self.now)
