from decimal import Decimal

import pytest

from research_engine.frameworks import FrameworkRegistry
from research_engine.quality import run_quality
from research_engine.schemas import Provenance

from .factories import duration, fy, fy_end, instant, reported


@pytest.fixture(scope="module")
def generic(frameworks_dir):
    return FrameworkRegistry(frameworks_dir).get("generic")


@pytest.fixture(scope="module")
def banks(frameworks_dir):
    return FrameworkRegistry(frameworks_dir).get("banks")


def run(facts, framework, years=3):
    return run_quality(facts, framework=framework, company_id="test-co", historical_years=years)


def derived_value(derived, metric, label):
    [f] = [f for f in derived if f.metric_id == metric and f.period.label == label]
    return f


YTD_2025 = [
    reported("revenue", 100, duration(2025, "Q1", "2025-01-01", "2025-03-31")),
    reported("revenue", 210, duration(2025, "H1", "2025-01-01", "2025-06-30")),
    reported("revenue", 330, duration(2025, "9M", "2025-01-01", "2025-09-30")),
    reported("revenue", 460, fy(2025)),
]


def test_quarters_from_year_to_date(generic):
    _, derived, report = run(YTD_2025, generic)
    q2, q3, q4 = (derived_value(derived, "revenue", f"Q{n}-2025") for n in (2, 3, 4))
    assert (q2.value, q3.value, q4.value) == (Decimal(110), Decimal(120), Decimal(130))
    assert q4.formula == "annual - nine_month_ytd" and q4.provenance is Provenance.DERIVED
    assert str(q4.period.start) == "2025-10-01" and str(q4.period.end) == "2025-12-31"
    assert len(q4.inputs) == 2
    assert report.derivations["interim:Q4"] == 1


def test_q4_from_reported_quarters_when_no_ytd(generic):
    facts = [
        reported("revenue", 100, duration(2025, "Q1", "2025-01-01", "2025-03-31")),
        reported("revenue", 110, duration(2025, "Q2", "2025-04-01", "2025-06-30")),
        reported("revenue", 120, duration(2025, "Q3", "2025-07-01", "2025-09-30")),
        reported("revenue", 460, fy(2025)),
    ]
    _, derived, _ = run(facts, generic)
    q4 = derived_value(derived, "revenue", "Q4-2025")
    assert q4.value == Decimal(130) and len(q4.inputs) == 4


def test_reported_quarter_is_never_replaced(generic):
    facts = YTD_2025 + [reported("revenue", 999, duration(2025, "Q4", "2025-10-01", "2025-12-31"))]
    current, derived, _ = run(facts, generic)
    assert not [f for f in derived if f.period.label == "Q4-2025" and f.metric_id == "revenue"]
    assert [f.value for f in current if f.period.label == "Q4-2025"] == [Decimal(999)]


def test_non_additive_metric_not_differenced(generic):
    facts = [
        reported("diluted_eps", "1.00", duration(2025, "9M", "2025-01-01", "2025-09-30"), unit="USD/share"),
        reported("diluted_eps", "1.40", fy(2025), unit="USD/share"),
    ]
    _, derived, report = run(facts, generic)
    assert not derived
    assert report.derivations["interim_not_derivable_non_additive:diluted_eps"] == 1


def test_non_contiguous_periods_rejected(generic):
    facts = [
        reported("revenue", 330, duration(2025, "9M", "2025-02-01", "2025-09-30")),  # does not start with the year
        reported("revenue", 460, fy(2025)),
    ]
    _, derived, report = run(facts, generic)
    assert not derived and report.derivations["interim_periods_not_contiguous:revenue"] == 1


def test_framework_derivation_fills_gaps_with_lineage(generic):
    rev, cogs = reported("revenue", 1000, fy(2025)), reported("cost_of_revenue", 600, fy(2025))
    _, derived, _ = run([rev, cogs], generic)
    gp = derived_value(derived, "gross_profit", "FY2025")
    gm = derived_value(derived, "gross_margin", "FY2025")
    assert gp.value == Decimal(400) and set(gp.inputs) == {rev.fact_id, cogs.fact_id}
    assert gm.value == Decimal("0.4") and gm.unit == "pure" and gp.fact_id in gm.inputs  # chains through derived


def test_reported_wins_and_is_checked_against_definition(generic):
    base = [reported("revenue", 1_000_000_000, fy(2025)), reported("cost_of_revenue", 600_000_000, fy(2025))]
    _, derived, report = run(base + [reported("gross_profit", 400_000_000, fy(2025))], generic)
    assert not [f for f in derived if f.metric_id == "gross_profit"]
    assert report.checks["derivation:gross_profit"].passed == 1
    _, _, report = run(base + [reported("gross_profit", 420_000_000, fy(2025))], generic)
    assert report.checks["derivation:gross_profit"].failed == 1
    assert any(i.check == "derivation:gross_profit" for i in report.issues)


def test_mixed_currency_and_division_by_zero_are_counted(generic):
    _, derived, report = run([reported("revenue", 1000, fy(2025)), reported("cost_of_revenue", 600, fy(2025), unit="EUR")], generic)
    assert not [f for f in derived if f.metric_id == "gross_profit"]
    assert report.derivations["derivation_mixed_currency:gross_profit"] == 1
    _, _, report = run([reported("operating_income", 10, fy(2025)), reported("revenue", 0, fy(2025))], generic)
    assert report.derivations["derivation_division_by_zero:operating_margin"] == 1


def test_duration_ratio_uses_period_end_balance(banks):
    provision = reported("provision_for_credit_losses", 30, duration(2025, "Q1", "2025-01-01", "2025-03-31"))
    loans = reported("loans", 1000, instant(2025, "Q1", "2025-03-31"))
    _, derived, _ = run([provision, loans], banks)
    rate = derived_value(derived, "credit_loss_rate", "Q1-2025")
    assert rate.value == Decimal("0.03") and "not annualised" in rate.notes
