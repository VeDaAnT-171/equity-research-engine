from decimal import Decimal

import pytest

from research_engine.analysis import run_analytics
from research_engine.forecast import run_forecast
from research_engine.frameworks import FrameworkRegistry
from research_engine.schemas.assumption import Assumption, AssumptionType
from research_engine.schemas.forecast import ScenarioSpec

from .factories import COMPANY, fy, fy_end, reported

DOC = "doc_0123456789abcdef"

# A five-year history with clean, constant ratios so every projected figure is checkable by hand:
# operating margin 20%, pre-tax conversion 90%, effective tax rate 25%, flat share count.
HISTORY = {
    "revenue":              [1000, 1100, 1200, 1300, 1400],
    "cost_of_revenue":      [600, 660, 720, 780, 840],
    "operating_income":     [200, 220, 240, 260, 280],
    "pre_tax_income":       [180, 198, 216, 234, 252],
    "income_tax_expense":   [45, 49.5, 54, 58.5, 63],
    "net_income":           [135, 148.5, 162, 175.5, 189],
    "diluted_shares":       [100, 100, 100, 100, 100],
    "operating_cash_flow":  [220, 240, 260, 280, 300],
    "capital_expenditure":  [50, 55, 60, 65, 70],
}
BALANCES = {"total_equity": [800, 900, 1000, 1100, 1200]}
YEARS = [2021, 2022, 2023, 2024, 2025]


@pytest.fixture
def framework(frameworks_dir):
    return FrameworkRegistry(frameworks_dir).get("generic")


@pytest.fixture
def facts():
    out = []
    for metric, values in HISTORY.items():
        unit = "shares" if metric == "diluted_shares" else "USD"
        out += [reported(metric, v, fy(y), unit=unit) for y, v in zip(YEARS, values, strict=True)]
    for metric, values in BALANCES.items():
        out += [reported(metric, v, fy_end(y)) for y, v in zip(YEARS, values, strict=True)]
    return out


@pytest.fixture
def analytics(framework, facts):
    return run_analytics(framework, facts, company_id=COMPANY, quality_report=None).values


def _run(framework, facts, analytics, **kw):
    return run_forecast(framework, company_id=COMPANY, facts=facts, analytics=analytics, **kw)


def _value(result, metric, year, scenario="base"):
    return result.by_scenario[scenario][metric][year].value


def _analyst(aid, value, **kw):
    return Assumption(assumption_id=aid, company_id=COMPANY, description=kw.pop("description", aid),
                      value=None if value is None else Decimal(str(value)),
                      unit=kw.pop("unit", "ratio"), type=kw.pop("type", AssumptionType.ANALYST_ASSUMPTION),
                      rationale=kw.pop("rationale", "stated for the test"), **kw)


# ---- seeding and the base case ---------------------------------------------------------------

def test_horizon_starts_after_the_last_reported_year(framework, facts, analytics):
    result = _run(framework, facts, analytics, forecast_years=3)
    assert result.base_year == 2025
    assert result.forecast_years == (2026, 2027, 2028)
    assert _value(result, "revenue", 2025) == Decimal(1400)
    assert result.by_scenario["base"]["revenue"][2025].method == "actual"


def test_seeded_history_produces_a_complete_base_case(framework, facts, analytics):
    result = _run(framework, facts, analytics, forecast_years=1)
    # median trailing growth of revenue is 1/12; margins are constant, so the chain is exact
    assert _value(result, "revenue", 2026) == Decimal(1400) * (1 + Decimal(1) / 12)
    assert _value(result, "operating_income", 2026).quantize(Decimal("0.0001")) == Decimal("303.3333")
    assert _value(result, "pre_tax_income", 2026).quantize(Decimal("0.01")) == Decimal("273.00")
    assert _value(result, "income_tax_expense", 2026).quantize(Decimal("0.01")) == Decimal("68.25")
    assert _value(result, "net_income", 2026).quantize(Decimal("0.01")) == Decimal("204.75")
    assert _value(result, "diluted_eps", 2026).quantize(Decimal("0.0001")) == Decimal("2.0475")
    assert not result.not_projected, dict(result.not_projected)
    assert not result.unseeded, result.unseeded


def test_every_seeded_assumption_carries_the_facts_it_came_from(framework, facts, analytics):
    result = _run(framework, facts, analytics, forecast_years=1)
    assert result.seeded
    for a in result.seeded:
        assert a.type is AssumptionType.HISTORICAL
        assert a.source_fact_ids, a.assumption_id


def test_growth_compounds_across_the_horizon(framework, facts, analytics):
    result = _run(framework, facts, analytics, forecast_years=3)
    rate = 1 + Decimal(1) / 12
    assert _value(result, "revenue", 2028) == Decimal(1400) * rate * rate * rate


def test_each_projection_records_the_assumption_and_rung_behind_it(framework, facts, analytics):
    result = _run(framework, facts, analytics, forecast_years=1)
    revenue = result.by_scenario["base"]["revenue"][2026]
    assert revenue.method == "growth"
    assert revenue.assumption_ids == ("growth.revenue",)
    assert revenue.assumption_types == ("historical",)
    net_income = result.by_scenario["base"]["net_income"][2026]
    assert net_income.method == "derivation"
    assert set(net_income.input_value_ids) == {
        result.by_scenario["base"]["pre_tax_income"][2026].value_id,
        result.by_scenario["base"]["income_tax_expense"][2026].value_id,
    }


# ---- the precedence ladder, end to end -------------------------------------------------------

def test_management_guidance_overrides_seeded_history(framework, facts, analytics):
    guidance = _analyst("growth.revenue", "0.20", type=AssumptionType.MANAGEMENT_GUIDANCE,
                        source_document_id=DOC, rationale=None)
    result = _run(framework, facts, analytics, forecast_years=1, analyst_assumptions=[guidance])
    assert _value(result, "revenue", 2026) == Decimal("1680.00")
    assert result.by_scenario["base"]["revenue"][2026].assumption_types == ("management_guidance",)


def test_the_analyst_outranks_management(framework, facts, analytics):
    supplied = [
        _analyst("growth.revenue", "0.20", type=AssumptionType.MANAGEMENT_GUIDANCE,
                 source_document_id=DOC, rationale=None),
        _analyst("growth.revenue", "0.05"),
    ]
    result = _run(framework, facts, analytics, forecast_years=1, analyst_assumptions=supplied)
    assert _value(result, "revenue", 2026) == Decimal("1470.00")
    assert result.by_scenario["base"]["revenue"][2026].assumption_types == ("analyst_assumption",)


def test_a_year_pinned_assumption_applies_only_to_that_year(framework, facts, analytics):
    supplied = [_analyst("growth.revenue", "0"), _analyst("growth.revenue", "0.50", period="FY2027")]
    result = _run(framework, facts, analytics, forecast_years=3)
    baseline = _value(result, "revenue", 2027)
    pinned = _run(framework, facts, analytics, forecast_years=3, analyst_assumptions=supplied)
    assert _value(pinned, "revenue", 2026) == Decimal(1400)
    assert _value(pinned, "revenue", 2027) == Decimal(2100)
    assert baseline != _value(pinned, "revenue", 2027)


# ---- scenarios -------------------------------------------------------------------------------

def test_scenarios_are_projected_independently(framework, facts, analytics):
    scenarios = [ScenarioSpec(id="bull", name="Bull"), ScenarioSpec(id="bear", name="Bear")]
    supplied = [
        _analyst("growth.revenue", "0.20", type=AssumptionType.SCENARIO, scenario="bull"),
        _analyst("growth.revenue", "-0.10", type=AssumptionType.SCENARIO, scenario="bear"),
    ]
    result = _run(framework, facts, analytics, forecast_years=1, analyst_assumptions=supplied,
                  scenarios=scenarios)
    assert set(result.by_scenario) == {"base", "bull", "bear"}
    assert _value(result, "revenue", 2026, "bull") == Decimal("1680.00")
    assert _value(result, "revenue", 2026, "bear") == Decimal("1260.00")
    assert _value(result, "revenue", 2026, "base") == Decimal(1400) * (1 + Decimal(1) / 12)
    # the scenario flows all the way through the chain, not just the metric it touches
    assert _value(result, "net_income", 2026, "bull") > _value(result, "net_income", 2026, "bear")


def test_scenario_values_are_distinct_rows(framework, facts, analytics):
    scenarios = [ScenarioSpec(id="bull", name="Bull")]
    supplied = [_analyst("growth.revenue", "0.20", type=AssumptionType.SCENARIO, scenario="bull")]
    result = _run(framework, facts, analytics, forecast_years=1, analyst_assumptions=supplied,
                  scenarios=scenarios)
    ids = {v.value_id for v in result.values}
    assert len(ids) == len(result.values)
    assert {v.scenario for v in result.values} == {"base", "bull"}


# ---- refusal ---------------------------------------------------------------------------------

def test_a_declared_but_unset_assumption_stops_the_chain(framework, facts, analytics):
    supplied = [_analyst("growth.revenue", None)]
    result = _run(framework, facts, analytics, forecast_years=2, analyst_assumptions=supplied)
    assert "revenue" not in result.by_scenario["base"] or 2026 not in result.by_scenario["base"]["revenue"]
    # the root cause is named once, in the first year; later years report the cascade, not a repeat
    assert result.not_projected["revenue:assumption_unset:growth.revenue"] == 1
    assert result.not_projected["revenue:prior_year_not_projected:revenue"] == 1
    # everything downstream is refused too, and says which input it was waiting on
    assert result.not_projected["operating_income:input_not_projected:revenue"] == 2
    assert result.not_projected["net_income:input_not_projected:income_tax_expense"] == 2


def test_a_metric_without_a_base_year_actual_is_not_projected(framework, analytics):
    partial = [reported("revenue", v, fy(y)) for y, v in zip(YEARS, HISTORY["revenue"], strict=True)]
    result = _run(framework, partial, [], forecast_years=1)
    assert result.not_projected["total_equity:base_year_actual_missing:total_equity"] == 1
    assert _value(result, "revenue", 2026) == Decimal(1400) * (1 + Decimal(1) / 12)


def test_nothing_is_invented_when_history_is_too_short(framework):
    facts = [reported("revenue", 100, fy(2025))]
    result = _run(framework, facts, [], forecast_years=1)
    assert "growth.revenue" in result.unseeded
    assert result.not_projected["revenue:assumption_missing:growth.revenue"] == 1
    assert all(v.is_actual for v in result.values)


def test_no_annual_facts_is_a_loud_failure(framework):
    from research_engine.errors import ForecastError
    with pytest.raises(ForecastError, match="no full-year facts"):
        _run(framework, [], [], forecast_years=1)


def test_every_value_downstream_of_a_fallback_carries_it(frameworks_dir):
    """A revenue line built from a trend-extrapolated income line is not the modelled revenue line.

    The mark has to reach every value computed from the fallback, however far downstream — and
    none that is independent of it — or a reader lifting EPS from the table cannot tell.
    """
    from research_engine.analysis import run_analytics
    from research_engine.frameworks import FrameworkRegistry

    from .factories import fy, fy_end, reported
    banks = FrameworkRegistry(frameworks_dir).get("banks")
    facts = []
    for i, year in enumerate(range(2021, 2026)):
        k = 1 + i / 20
        facts += [reported("net_interest_income", 50 * k, fy(year)), reported("noninterest_income", 40 * k, fy(year)),
                  reported("noninterest_expense", 55 * k, fy(year)), reported("provision_for_credit_losses", 5 * k, fy(year)),
                  reported("income_tax_expense", 6 * k, fy(year)), reported("pre_tax_income", 30 * k, fy(year)),
                  reported("net_income", 24 * k, fy(year)), reported("net_income_to_common", 23 * k, fy(year)),
                  reported("revenue", 90 * k, fy(year)), reported("diluted_shares", 3, fy(year), unit="shares"),
                  reported("loans", 700 * k, fy_end(year)), reported("deposits", 1000 * k, fy_end(year)),
                  reported("total_equity", 200 * k, fy_end(year)), reported("total_assets", 2000 * k, fy_end(year))]
    analytics = run_analytics(banks, facts, company_id=COMPANY, quality_report=None).values
    result = run_forecast(banks, company_id=COMPANY, facts=facts, analytics=analytics, forecast_years=2)
    base = result.by_scenario["base"]
    year = result.base_year + 1

    assert result.graph.fallbacks.keys() == {"net_interest_income"}
    for metric in ("net_interest_income", "revenue", "pre_tax_income", "net_income", "diluted_eps"):
        assert base[metric][year].fallback_for == ("net_interest_income",), metric
    # independent of NII, so unmarked: a mark on everything would be a mark on nothing
    for metric in ("noninterest_income", "loans", "provision_for_credit_losses", "deposits"):
        assert base[metric][year].fallback_for == (), metric
    # the base-year actual is a fact, not a projection, whatever model follows it
    assert base["net_interest_income"][result.base_year].fallback_for == ()
