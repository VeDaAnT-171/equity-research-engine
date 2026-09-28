"""Forecast charts must encode the actual/projected boundary without relying on colour."""

from decimal import Decimal

import pytest

from research_engine.forecast.charts import (
    DASHES,
    render_forecast_chart,
    render_forecast_charts,
)
from research_engine.frameworks import FrameworkRegistry
from research_engine.schemas.forecast import ForecastValue, make_forecast_id

COMPANY = "test-co"


@pytest.fixture
def framework(frameworks_dir):
    return FrameworkRegistry(frameworks_dir).get("generic")


def _value(scenario, metric, year, value, method="growth", unit_kind="currency"):
    inputs = (f"{scenario}|{metric}|{year}",)
    return ForecastValue(
        value_id=make_forecast_id(COMPANY, scenario, metric, year, inputs),
        company_id=COMPANY, scenario=scenario, metric_id=metric, fiscal_year=year,
        value=Decimal(str(value)), unit_kind=unit_kind, currency="USD" if "currency" in unit_kind else None,
        method=method, formula="f",
        assumption_ids=() if method == "actual" else ("growth.revenue",),
        assumption_types=() if method == "actual" else ("historical",),
        input_fact_ids=("fact_" + "a" * 16,) if method == "actual" else (),
        input_value_ids=() if method == "actual" else (make_forecast_id(COMPANY, scenario, metric, year - 1, inputs),),
    )


def _series(scenario, values, base=2025):
    return [_value(scenario, "revenue", base, 1000, method="actual")] + [
        _value(scenario, "revenue", base + i + 1, v) for i, v in enumerate(values)]


def test_chart_renders_svg_and_png_with_a_data_table(framework, tmp_path):
    record = render_forecast_chart(
        "revenue", {"base": _series("base", [1100, 1200]), "bull": _series("bull", [1300, 1500])},
        framework, tmp_path, base_year=2025, company_label="Test Co", engine_version="x")
    assert record.skipped is None
    assert {p.name for p in tmp_path.iterdir()} == {"forecast_revenue.svg", "forecast_revenue.png"}
    # the accessible fallback carries the same numbers as the picture
    assert record.table["bull"]["FY2027"] == 1500.0
    assert record.years == [2025, 2026, 2027]
    assert record.scenarios == ["base", "bull"]
    assert record.methods["bull"] == "growth"


def test_every_scenario_gets_a_distinct_line_style(framework, tmp_path):
    scenarios = {name: _series(name, [1100, 1200]) for name in ("base", "bull", "bear", "stress")}
    record = render_forecast_chart("revenue", scenarios, framework, tmp_path, base_year=2025,
                                   company_label="Test Co", engine_version="x")
    svg = (tmp_path / "forecast_revenue.svg").read_text()
    assert record.skipped is None
    assert len(set(DASHES[:len(scenarios)])) == len(scenarios), "line styles must not repeat"
    # each scenario is named on the chart itself, so hue is never the only distinction
    for name in scenarios:
        assert name in svg


def test_the_base_case_is_listed_first(framework, tmp_path):
    record = render_forecast_chart(
        "revenue", {"bull": _series("bull", [1300]), "base": _series("base", [1100])},
        framework, tmp_path, base_year=2025, company_label="Test Co", engine_version="x")
    assert record.scenarios[0] == "base"


def test_a_metric_with_no_projection_is_skipped_not_drawn_flat(framework, tmp_path):
    """A flat line continuing the last actual would read as a forecast of no change."""
    only_actual = {"base": [_value("base", "revenue", 2025, 1000, method="actual")]}
    record = render_forecast_chart("revenue", only_actual, framework, tmp_path, base_year=2025,
                                   company_label="Test Co", engine_version="x")
    assert record.skipped == "not_projected"
    assert not list(tmp_path.iterdir())


def test_currency_must_be_uniform(framework, tmp_path):
    values = _series("base", [1100, 1200])
    stripped = [v.model_copy(update={"currency": None}) for v in values]
    record = render_forecast_chart("revenue", {"base": stripped}, framework, tmp_path,
                                   base_year=2025, company_label="Test Co", engine_version="x")
    assert record.skipped == "currency_not_uniform"


def test_charts_follow_driver_graph_order(framework, tmp_path):
    from research_engine.analysis import run_analytics
    from research_engine.forecast import run_forecast

    from .factories import fy, fy_end, reported
    facts = []
    for year, revenue in zip(range(2021, 2026), (1000, 1100, 1200, 1300, 1400), strict=True):
        facts.append(reported("revenue", revenue, fy(year)))
        facts.append(reported("total_equity", revenue, fy_end(year)))
    analytics = run_analytics(framework, facts, company_id=COMPANY, quality_report=None).values
    result = run_forecast(framework, company_id=COMPANY, facts=facts, analytics=analytics,
                          forecast_years=2)
    records = render_forecast_charts(result, framework, tmp_path, company_label="Test Co",
                                     engine_version="x")
    charted = [r.metric_id for r in records]
    assert charted == [m for m in result.graph.order if m in charted]
    assert any(not r.skipped for r in records)


def test_rendering_removes_images_left_by_a_previous_run(framework, tmp_path):
    """A chart skipped this run writes nothing, so its old image would otherwise look current."""
    from types import SimpleNamespace

    (tmp_path / "forecast_revenue.svg").write_text("<svg>from an earlier run</svg>")
    (tmp_path / "forecast_revenue.png").write_bytes(b"old")
    only_actual = {"base": {"revenue": {2025: _value("base", "revenue", 2025, 1000, method="actual")}}}
    result = SimpleNamespace(by_scenario=only_actual, base_year=2025,
                             graph=SimpleNamespace(order=("revenue",)))
    records = render_forecast_charts(result, framework, tmp_path, company_label="Test Co", engine_version="x")
    assert records[0].skipped == "not_projected"
    assert not list(tmp_path.iterdir())


def test_a_chart_built_on_a_fallback_says_so_in_the_image(framework, tmp_path):
    """The image travels without the page around it, so the caveat has to be inside it."""
    marked = [v if v.method == "actual" else v.model_copy(update={"fallback_for": ("net_interest_income",)})
              for v in _series("base", [1100, 1200])]
    record = render_forecast_chart("revenue", {"base": marked}, framework, tmp_path, base_year=2025,
                                   company_label="Test Co", engine_version="x")
    assert record.fallback_for == ["net_interest_income"]
    assert "Rests on net_interest_income projected by its own trend" in (tmp_path / "forecast_revenue.svg").read_text()


def test_a_chart_on_the_declared_model_carries_no_fallback_caption(framework, tmp_path):
    record = render_forecast_chart("revenue", {"base": _series("base", [1100, 1200])}, framework, tmp_path,
                                   base_year=2025, company_label="Test Co", engine_version="x")
    assert record.fallback_for == [] and "Rests on" not in (tmp_path / "forecast_revenue.svg").read_text()
