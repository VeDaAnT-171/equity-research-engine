from decimal import Decimal

import pytest

from research_engine.errors import ConfigError
from research_engine.forecast import AssumptionSet, load_assumptions_file
from research_engine.forecast.assumptions import (
    AssumptionUnavailable,
    SeedInput,
    annual_analytic_series,
    seed_growth,
    seed_level,
)
from research_engine.schemas.analytics import AnalyticValue, make_value_id
from research_engine.schemas.assumption import Assumption, AssumptionType

from .factories import fy, reported

DOC = "doc_0123456789abcdef"


def _a(aid, value, atype, **kw):
    kw.setdefault("unit", "ratio")
    if atype in (AssumptionType.MANAGEMENT_GUIDANCE, AssumptionType.CONSENSUS):
        kw.setdefault("source_document_id", DOC)
    if atype is AssumptionType.ANALYST_ASSUMPTION:
        kw.setdefault("rationale", "because")
    if atype is AssumptionType.SCENARIO:
        kw.setdefault("rationale", "because")
    if atype in (AssumptionType.HISTORICAL, AssumptionType.DERIVED):
        kw.setdefault("source_fact_ids", ("fact_0123456789abcdef",))
    return Assumption(assumption_id=aid, company_id="test-co", description=aid,
                      value=None if value is None else Decimal(str(value)), type=atype, **kw)


# ---- seeding -------------------------------------------------------------------------------

def test_growth_seed_is_the_median_of_the_trailing_window():
    facts = [reported("revenue", v, fy(y)) for y, v in
             [(2020, 100), (2021, 200), (2022, 220), (2023, 242), (2024, 266.2)]]
    seed = SeedInput({f.period.fiscal_year: f.value for f in facts},
                     {f.period.fiscal_year: (f.fact_id,) for f in facts})
    a = seed_growth("test-co", "revenue", seed)
    # trailing three growth observations are all 10%; the 100% step in 2021 falls outside the window
    assert a.value == Decimal("0.1")
    assert a.type is AssumptionType.HISTORICAL
    assert len(a.source_fact_ids) == 4  # three years plus the base year of the earliest pair


def test_growth_seed_refuses_a_non_positive_base():
    seed = SeedInput({2023: Decimal(0), 2024: Decimal(50)}, {2023: ("fact_a" * 4,), 2024: ("fact_b" * 4,)})
    assert seed_growth("test-co", "revenue", seed) is None


def test_growth_seed_needs_two_consecutive_years():
    facts = [reported("revenue", 100, fy(2024))]
    seed = SeedInput({2024: facts[0].value}, {2024: (facts[0].fact_id,)})
    assert seed_growth("test-co", "revenue", seed) is None


def test_level_seed_uses_the_median_level():
    facts = [reported("net_interest_margin", v, fy(y), unit="pure") for y, v in
             [(2022, "0.02"), (2023, "0.03"), (2024, "0.04")]]
    seed = SeedInput({f.period.fiscal_year: f.value for f in facts},
                     {f.period.fiscal_year: (f.fact_id,) for f in facts})
    a = seed_level("test-co", "net_interest_margin", seed, key="level.net_interest_margin",
                   unit="ratio", label="level of")
    assert a.value == Decimal("0.03")


def test_analytic_lineage_resolves_through_intermediate_analytics():
    """A rate seeded from an analytic must carry the facts underneath it, not just its parent value."""
    leaf = AnalyticValue(
        value_id=make_value_id("test-co", "net_margin", 2024, ("fact_" + "a" * 16,)),
        company_id="test-co", analytic_id="net_margin", category="profitability", kind="ratio",
        unit_kind="ratio", fiscal_year=2024, value=Decimal("0.1"), formula="x/y", basis="period_end",
        input_fact_ids=("fact_" + "a" * 16,),
    )
    parent = AnalyticValue(
        value_id=make_value_id("test-co", "roic", 2024, (leaf.value_id,)),
        company_id="test-co", analytic_id="roic", category="returns", kind="ratio", unit_kind="ratio",
        fiscal_year=2024, value=Decimal("0.2"), formula="f", basis="period_end",
        input_value_ids=(leaf.value_id,),
    )
    series = annual_analytic_series([leaf, parent])
    assert series["roic"].fact_ids[2024] == ("fact_" + "a" * 16,)


# ---- resolution ladder ---------------------------------------------------------------------

def test_resolution_order_scenario_analyst_guidance_consensus_history():
    everything = [
        _a("growth.revenue", 0.01, AssumptionType.HISTORICAL),
        _a("growth.revenue", 0.02, AssumptionType.CONSENSUS),
        _a("growth.revenue", 0.03, AssumptionType.MANAGEMENT_GUIDANCE),
        _a("growth.revenue", 0.04, AssumptionType.ANALYST_ASSUMPTION),
        _a("growth.revenue", 0.05, AssumptionType.SCENARIO, scenario="bull"),
    ]
    registry = AssumptionSet(everything)
    assert registry.resolve("growth.revenue", 2026, "base").value == Decimal("0.04")
    assert registry.resolve("growth.revenue", 2026, "bull").value == Decimal("0.05")

    without_analyst = AssumptionSet(everything[:3])
    assert without_analyst.resolve("growth.revenue", 2026, "base").value == Decimal("0.03")
    assert AssumptionSet(everything[:2]).resolve("growth.revenue", 2026, "base").value == Decimal("0.02")
    assert AssumptionSet(everything[:1]).resolve("growth.revenue", 2026, "base").value == Decimal("0.01")


def test_a_year_pinned_assumption_beats_an_all_years_one_of_the_same_type():
    registry = AssumptionSet([
        _a("growth.revenue", 0.04, AssumptionType.ANALYST_ASSUMPTION),
        _a("growth.revenue", 0.09, AssumptionType.ANALYST_ASSUMPTION, period="FY2027"),
    ])
    assert registry.resolve("growth.revenue", 2026, "base").value == Decimal("0.04")
    assert registry.resolve("growth.revenue", 2027, "base").value == Decimal("0.09")


def test_a_scenario_override_does_not_leak_into_other_scenarios():
    registry = AssumptionSet([
        _a("growth.revenue", 0.01, AssumptionType.HISTORICAL),
        _a("growth.revenue", 0.20, AssumptionType.SCENARIO, scenario="bull"),
    ])
    assert registry.resolve("growth.revenue", 2026, "bear").value == Decimal("0.01")


def test_missing_and_unset_assumptions_are_distinguished():
    registry = AssumptionSet([_a("growth.revenue", None, AssumptionType.ANALYST_ASSUMPTION)])
    with pytest.raises(AssumptionUnavailable, match="assumption_unset:growth.revenue"):
        registry.resolve("growth.revenue", 2026, "base")
    with pytest.raises(AssumptionUnavailable, match="assumption_missing:growth.other"):
        registry.resolve("growth.other", 2026, "base")


# ---- the analyst file ----------------------------------------------------------------------

def _write(tmp_path, text):
    path = tmp_path / "assumptions.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_missing_file_is_not_an_error(tmp_path):
    assert load_assumptions_file(tmp_path / "assumptions.yaml", company_id="test-co") == ([], [])


def test_loads_scenarios_and_assumptions(tmp_path):
    path = _write(tmp_path, """
scenarios:
  - {id: bull, name: Bull case, description: Faster growth}
assumptions:
  - id: growth.revenue
    description: Management targets high single digit growth
    value: 0.08
    unit: ratio
    type: management_guidance
    source_document_id: doc_0123456789abcdef
  - id: growth.revenue
    description: Bull case growth
    value: 0.15
    unit: ratio
    type: scenario
    scenario: bull
    rationale: Assumes the new product ramps on schedule
""")
    assumptions, scenarios = load_assumptions_file(path, company_id="test-co")
    assert [s.id for s in scenarios] == ["bull"]
    assert {a.type for a in assumptions} == {AssumptionType.MANAGEMENT_GUIDANCE, AssumptionType.SCENARIO}


def test_guidance_without_a_document_is_rejected(tmp_path):
    path = _write(tmp_path, """
assumptions:
  - {id: growth.revenue, description: guided, value: 0.08, unit: ratio, type: management_guidance}
""")
    with pytest.raises(ConfigError, match="must cite source_document_id"):
        load_assumptions_file(path, company_id="test-co")


def test_the_analyst_cannot_claim_a_historical_assumption(tmp_path):
    path = _write(tmp_path, """
assumptions:
  - {id: growth.revenue, description: history, value: 0.08, unit: ratio, type: historical,
     source_fact_ids: [fact_0123456789abcdef]}
""")
    with pytest.raises(ConfigError, match="computed by the engine"):
        load_assumptions_file(path, company_id="test-co")


def test_scenario_assumption_must_bind_to_a_declared_scenario(tmp_path):
    path = _write(tmp_path, """
assumptions:
  - {id: growth.revenue, description: x, value: 0.15, unit: ratio, type: scenario,
     scenario: bull, rationale: why}
""")
    with pytest.raises(ConfigError, match="undeclared scenario"):
        load_assumptions_file(path, company_id="test-co")


def test_duplicate_assumptions_are_rejected(tmp_path):
    path = _write(tmp_path, """
assumptions:
  - {id: growth.revenue, description: a, value: 0.1, unit: ratio, type: analyst_assumption, rationale: r}
  - {id: growth.revenue, description: b, value: 0.2, unit: ratio, type: analyst_assumption, rationale: r}
""")
    with pytest.raises(ConfigError, match="duplicate assumption"):
        load_assumptions_file(path, company_id="test-co")


def test_unknown_top_level_keys_are_rejected(tmp_path):
    path = _write(tmp_path, "assumptionz: []\n")
    with pytest.raises(ConfigError, match="unknown keys"):
        load_assumptions_file(path, company_id="test-co")


def test_assumptions_for_another_company_are_rejected(tmp_path):
    path = _write(tmp_path, """
assumptions:
  - {id: growth.revenue, company_id: other-co, description: a, value: 0.1, unit: ratio,
     type: analyst_assumption, rationale: r}
""")
    with pytest.raises(ConfigError, match="another company"):
        load_assumptions_file(path, company_id="test-co")
