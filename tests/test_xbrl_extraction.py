from decimal import Decimal

import pytest

from research_engine.calendar import FiscalCalendar
from research_engine.extraction import current_facts, extract_xbrl_facts
from research_engine.frameworks import FrameworkRegistry
from research_engine.schemas import Provenance
from research_engine.sources import SecCompanyFacts

from .conftest import SEC_FIXTURES

DOC = "doc_0123456789abcdef"


@pytest.fixture(scope="module")
def observations():
    return SecCompanyFacts().normalize((SEC_FIXTURES / "companyfacts_CIK0009999002.json").read_bytes(), DOC).observations


def run(observations, framework_name, **kwargs):
    fw = FrameworkRegistry(SEC_FIXTURES.parents[2] / "industry_frameworks").get(framework_name)
    return extract_xbrl_facts(observations, framework=fw, calendar=FiscalCalendar(12), company_id="nyse-exbk",
                              document_id=DOC, **kwargs)


def by(facts, metric, label):
    return [f for f in facts if f.metric_id == metric and f.period.label == label]


def test_comparatives_collapse_to_first_disclosure(observations):
    facts, _ = run(observations, "banks")
    [fy2023] = by(facts, "revenue", "FY2023")
    assert fy2023.source.filing_accession == "0009999002-24-000010"
    assert "repeated in 1 later filing" in fy2023.notes


def test_restatements_are_kept_not_overwritten(observations):
    facts, report = run(observations, "banks")
    fy2024 = sorted(by(facts, "revenue", "FY2024"), key=lambda f: f.source.filed_date)
    assert [f.value for f in fy2024] == [Decimal("55000000000"), Decimal("54500000000")]
    assert len(report.coverage["revenue"].restatements) == 1
    [current] = [f for f in current_facts(facts) if f.metric_id == "revenue" and f.period.label == "FY2024"]
    assert current.value == Decimal("54500000000")


def test_every_fact_is_reported_with_filing_lineage(observations):
    facts, _ = run(observations, "banks")
    assert facts and all(f.provenance is Provenance.REPORTED for f in facts)
    assert all(f.source.xbrl_concept and f.source.filing_accession and f.source.filed_date for f in facts)


def test_skip_reasons_are_counted_not_silent(observations):
    _, report = run(observations, "banks")
    for reason in ("nine_month_year_to_date", "form_not_accepted:8-K", "period_end_not_fiscal_quarter_end",
                   "unit_incompatible:deposits:shares", "unmapped_concept"):
        assert report.skipped[reason] >= 1, reason
    assert report.observations_total == sum(report.skipped.values()) + report.observations_mapped


def test_periods_and_units(observations):
    facts, _ = run(observations, "banks")
    assert by(facts, "revenue", "Q1-2025") and by(facts, "revenue", "H1-2025")
    [eps] = by(facts, "diluted_eps", "FY2025")
    assert eps.unit == "USD/share" and eps.currency == "USD"
    [assets_q1] = by(facts, "total_assets", "Q1-2025")
    assert assets_q1.period.start is None


def test_framework_drives_mapping(observations):
    _, bank = run(observations, "banks")
    _, generic = run(observations, "generic")
    assert "net_interest_income" in bank.coverage and "net_interest_income" not in generic.coverage
    # generic ranks Revenues above RevenueFromContract...: the lower-ranked concept is shadowed, not merged
    assert generic.coverage["revenue"].shadowed_concepts["us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax"] == 1
    assert "average_interest_earning_assets" in bank.metrics_requiring_documents
    assert "net_interest_margin" in bank.derivation_only_metrics


def test_currency_mismatch_flagged(observations):
    _, report = run(observations, "banks", expected_currency="EUR")
    assert report.currency_mismatches["revenue:USD"] >= 1


def test_fact_ids_stable_across_runs(observations):
    a, _ = run(observations, "banks")
    b, _ = run(observations, "banks")
    assert [f.fact_id for f in a] == [f.fact_id for f in b]
    assert len({f.fact_id for f in a}) == len(a)
