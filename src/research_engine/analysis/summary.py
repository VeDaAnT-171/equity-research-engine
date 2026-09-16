"""Descriptive statistics for annual analytic series. Descriptive only: nothing here implies causation."""

from __future__ import annotations

import statistics
from typing import Optional

from ..schemas.analytics import AnalyticValue
from ..schemas.framework import IndustryFramework

MIN_TREND_POINTS = 5


def _consecutive_tail(years: list[int], length: int) -> Optional[list[int]]:
    tail = years[-length:]
    if len(tail) == length and tail[-1] - tail[0] == length - 1:
        return tail
    return None


def _cagr(first: float, last: float, span: int) -> Optional[float]:
    if span <= 0 or first <= 0 or last <= 0:
        return None
    return (last / first) ** (1 / span) - 1


def ols_slope(xs: list[int], ys: list[float]) -> float:
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sum((x - mx) ** 2 for x in xs)


def max_drawdown(points: list[tuple[int, float]]) -> Optional[dict]:
    """Largest peak-to-trough decline in a positive annual series."""
    if len(points) < 2 or any(v <= 0 for _, v in points):
        return None
    peak_year, peak = points[0]
    worst = None
    for year, value in points[1:]:
        if value > peak:
            peak_year, peak = year, value
            continue
        decline = value / peak - 1
        if worst is None or decline < worst["decline"]:
            worst = {"decline": decline, "peak_year": peak_year, "trough_year": year}
    return worst


def summarize(series: dict[int, AnalyticValue], unit_kind: str, kind: str) -> dict:
    years = sorted(series)
    vals = [float(series[y].value) for y in years]
    out: dict = {
        "observations": len(vals),
        "first_year": years[0],
        "latest_year": years[-1],
        "latest": vals[-1],
        "median": statistics.median(vals),
        "mean": statistics.fmean(vals),
        "stdev": statistics.stdev(vals) if len(vals) >= 3 else None,
        "min": {"value": min(vals), "year": years[vals.index(min(vals))]},
        "max": {"value": max(vals), "year": years[vals.index(max(vals))]},
        "flagged_years": [y for y in years if series[y].quality_flags],
        "years_using_derived_facts": [y for y in years if series[y].uses_derived_facts],
    }
    for n in (3, 5):
        tail = _consecutive_tail(years, n)
        out[f"avg_{n}y"] = statistics.fmean(float(series[y].value) for y in tail) if tail else None
    out["trend_per_year"] = ols_slope(years, vals) if len(vals) >= MIN_TREND_POINTS else None
    if unit_kind in ("currency", "count") and kind in ("level", "expression"):
        out["cagr_full"] = _cagr(vals[0], vals[-1], years[-1] - years[0])
        for n in (3, 5):
            start = years[-1] - n
            out[f"cagr_{n}y"] = _cagr(float(series[start].value), vals[-1], n) if start in series else None
    if kind == "growth":
        out["down_years"] = sum(1 for v in vals if v < 0)
    return out


def summarize_all(framework: IndustryFramework, by_analytic: dict[str, dict[int, AnalyticValue]],
                  metric_series: dict[str, list[tuple[int, float]]]) -> dict:
    summaries = {}
    for spec in framework.analytics:
        series = by_analytic.get(spec.id)
        if not series:
            continue
        s = summarize(series, spec.unit_kind, spec.kind)
        if spec.kind == "growth" and spec.metric in metric_series:
            s["underlying_max_drawdown"] = max_drawdown(metric_series[spec.metric])
        summaries[spec.id] = {"name": spec.name, "category": spec.category, "kind": spec.kind,
                              "unit_kind": spec.unit_kind, **s}
    return summaries
