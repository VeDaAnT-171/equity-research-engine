import pytest

from research_engine.errors import ForecastError
from research_engine.forecast import build_driver_graph
from research_engine.frameworks import FrameworkRegistry
from research_engine.schemas.framework import DriverSpec, IndustryFramework, MetricSpec, ValuationPolicy


@pytest.fixture
def registry(frameworks_dir):
    return FrameworkRegistry(frameworks_dir)


def test_driver_formula_beats_derivation_and_trend(registry):
    graph = build_driver_graph(registry.get("generic"))
    assert graph.rule("operating_income").method == "driver_formula"
    assert graph.rule("operating_income").formula == "revenue * operating_margin"
    # revenue has no formula driver and no derivation in `generic`: it trends.
    assert graph.rule("revenue").method == "growth"
    # net_income has a derivation and no driver formula: the derivation is re-applied forward.
    assert graph.rule("net_income").method == "derivation"


def test_ratio_metrics_take_a_level_not_a_growth_rate(registry):
    graph = build_driver_graph(registry.get("banks"))
    assert graph.rule("cet1_ratio").method == "level"
    assert "level.cet1_ratio" in graph.rule("cet1_ratio").assumption_keys
    assert graph.rule("loans").method == "growth"
    assert "growth.loans" in graph.rule("loans").assumption_keys


def test_bank_margin_derivation_is_demoted_to_break_the_cycle(registry):
    """`net_interest_margin` derives from NII in history and drives NII in the forecast."""
    graph = build_driver_graph(registry.get("banks"))
    assert "net_interest_margin" in graph.demoted
    assert graph.rule("net_interest_margin").method == "level"
    assert graph.rule("net_interest_income").method == "driver_formula"
    assert "circular" in graph.demoted["net_interest_margin"]
    # the demotion is visible on the rule itself, not hidden in a side table
    assert "circular" in graph.rule("net_interest_margin").reason


def test_evaluation_order_puts_inputs_before_outputs(registry):
    graph = build_driver_graph(registry.get("banks"))
    position = {m: i for i, m in enumerate(graph.order)}
    for metric_id, rule in graph.rules.items():
        for dep in rule.metric_inputs:
            assert position[dep] < position[metric_id], f"{dep} must be evaluated before {metric_id}"


def test_bank_chain_runs_from_balance_sheet_to_eps(registry):
    graph = build_driver_graph(registry.get("banks"))
    assert graph.rule("net_interest_income").metric_inputs == ("average_interest_earning_assets", "net_interest_margin")
    assert graph.rule("revenue").method == "derivation"
    assert graph.rule("pre_tax_income").formula == "revenue - noninterest_expense - provision_for_credit_losses"
    assert graph.rule("income_tax_expense").assumption_keys == ("rate.effective_tax_rate",)
    assert graph.rule("diluted_eps").metric_inputs == ("diluted_shares", "net_income_to_common")


def test_analytics_in_driver_formulas_become_rate_assumptions(registry):
    graph = build_driver_graph(registry.get("industrials"))
    assert "rate.backlog_conversion" in graph.rule("revenue").assumption_keys
    assert graph.rule("revenue").metric_inputs == ("backlog",)


def test_targets_expand_transitively(registry):
    graph = build_driver_graph(registry.get("generic"))
    # `gross_profit` is a declared target; `cost_of_revenue` is only reachable through its derivation
    assert "gross_profit" in graph.targets
    assert "cost_of_revenue" not in graph.targets
    assert "cost_of_revenue" in graph.rules


def test_unknown_targets_are_reported_not_raised(registry):
    graph = build_driver_graph(registry.get("generic"), extra_targets=("not_a_metric",))
    assert graph.unresolved == {"not_a_metric": "not a metric in this framework"}


def _framework(metrics, drivers, targets=()):
    return IndustryFramework(
        name="toy", display_name="Toy", metrics=tuple(metrics), drivers=tuple(drivers),
        forecast_targets=tuple(targets), valuation=ValuationPolicy(preferred=("pe",)),
    )


def _metric(mid, unit_kind="currency", derivation=None):
    return MetricSpec(id=mid, name=mid, unit_kind=unit_kind, statement="income_statement",
                      period_type="duration", derivation=derivation)


def test_unbreakable_cycle_raises(registry):
    """Two driver formulas pointing at each other cannot be demoted: nothing is a derivation."""
    framework = _framework(
        [_metric("a"), _metric("b")],
        [DriverSpec(id="d1", name="d1", affects=("a",), formula="b"),
         DriverSpec(id="d2", name="d2", affects=("b",), formula="a")],
        targets=("a",),
    )
    with pytest.raises(ForecastError, match="circular forecast dependency"):
        build_driver_graph(framework)


def test_self_referential_derivation_is_demoted():
    framework = _framework(
        [_metric("margin", unit_kind="ratio", derivation="income"), _metric("income")],
        [DriverSpec(id="engine", name="engine", affects=("income",), formula="margin")],
        targets=("income",),
    )
    graph = build_driver_graph(framework)
    assert graph.rule("margin").method == "level"
    assert "margin" in graph.demoted
