from decimal import Decimal

import pytest

from research_engine.analysis.summary import max_drawdown, ols_slope, summarize
from research_engine.schemas.analytics import AnalyticValue, make_value_id

FACT = "fact_0123456789abcdef"


def series(values: dict[int, float], kind="ratio_kind"):
    return {y: AnalyticValue(value_id=make_value_id("c", "a", y, (FACT,)), company_id="c", analytic_id="a", category="growth",
                             kind="level", unit_kind="currency", fiscal_year=y, value=Decimal(str(v)), currency="USD",
                             formula="a", basis="period_end", input_fact_ids=(FACT,))
            for y, v in values.items()}


def test_levels_summary_with_cagr_and_trend():
    s = summarize(series({2019: 100, 2020: 110, 2021: 121, 2022: 133.1, 2023: 146.41, 2024: 161.051}), "currency", "level")
    assert s["observations"] == 6 and s["latest"] == pytest.approx(161.051)
    assert s["cagr_5y"] == pytest.approx(0.10) and s["cagr_full"] == pytest.approx(0.10)
    assert s["avg_3y"] == pytest.approx((133.1 + 146.41 + 161.051) / 3)
    assert s["trend_per_year"] is not None and s["min"]["year"] == 2019


def test_short_or_gappy_series():
    s = summarize(series({2020: 1, 2022: 2}), "currency", "level")
    assert s["avg_3y"] is None and s["stdev"] is None and s["trend_per_year"] is None and s["cagr_3y"] is None


def test_drawdown_and_slope():
    assert max_drawdown([(2019, 100), (2020, 80), (2021, 120), (2022, 60), (2023, 90)]) == {
        "decline": pytest.approx(-0.5), "peak_year": 2021, "trough_year": 2022}
    assert max_drawdown([(2019, 100), (2020, -5)]) is None
    assert ols_slope([1, 2, 3], [2.0, 4.0, 6.0]) == pytest.approx(2.0)
