"""Forecast charts: one line chart per projected metric, all scenarios together.

Two conventions carry the meaning, and neither relies on colour:

* **Solid to the base year, dashed after it.** The break is where reported history stops and the
  model starts. A reader who cannot see the palette still sees where the facts end.
* **Scenarios are distinguished by line style and a label at the end of each line**, not by hue.
  Colour reinforces; it never encodes on its own.

A metric is charted only where at least one scenario projects it. A metric the engine refused to
project has no line rather than a flat one continuing its last actual, because a flat line reads
as a forecast of no change, which is a claim the engine did not make.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.ticker import FuncFormatter, MaxNLocator, PercentFormatter  # noqa: E402

from ..analysis.charts import PALETTE, _currency_formatter  # noqa: E402
from ..presentation import in_sentence  # noqa: E402
from ..schemas.forecast import BASE_SCENARIO, ForecastValue  # noqa: E402
from ..schemas.framework import IndustryFramework  # noqa: E402

# Line styles do the work colour must not do alone.
DASHES = ((), (5, 2), (1.5, 1.5), (7, 2, 1.5, 2), (3, 1, 1, 1))
MIN_POINTS = 2


@dataclass
class ForecastChartRecord:
    chart_id: str
    metric_id: str
    title: str
    unit_kind: str
    currency: str | None
    files: list[str]
    series: dict[str, list[str]]        # scenario -> forecast value ids, for lineage
    base_year: int | None
    years: list[int]
    scenarios: list[str]
    # The accessible fallback the chart guidance requires: the same numbers as a table.
    table: dict[str, dict[str, float]] = field(default_factory=dict)
    methods: dict[str, str] = field(default_factory=dict)
    skipped: str | None = None
    # Metrics projected by fallback that any plotted value depends on. The image is downloaded and
    # pasted on its own, so the caveat has to be in the picture, not only in the page around it.
    fallback_for: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _title(framework: IndustryFramework, metric_id: str) -> str:
    try:
        return framework.metric(metric_id).name
    except KeyError:  # pragma: no cover - metrics always resolve for charted values
        return metric_id


def render_forecast_chart(metric_id: str, by_scenario: dict[str, list[ForecastValue]],
                          framework: IndustryFramework, out_dir: Path, *, base_year: int,
                          company_label: str, engine_version: str) -> ForecastChartRecord:
    ordered = {s: sorted(vs, key=lambda v: v.fiscal_year)
               for s, vs in by_scenario.items() if vs}
    # Base first, then declared order, so the base case is the reference line.
    names = ([BASE_SCENARIO] if BASE_SCENARIO in ordered else []) + \
            [s for s in ordered if s != BASE_SCENARIO]

    first = next(iter(ordered.values()), [])
    unit_kind = first[0].unit_kind if first else "ratio"
    currency = next((v.currency for vs in ordered.values() for v in vs if v.currency), None)
    record = ForecastChartRecord(
        chart_id=f"forecast_{metric_id}", metric_id=metric_id,
        title=_title(framework, metric_id), unit_kind=unit_kind, currency=currency, files=[],
        series={s: [v.value_id for v in ordered[s]] for s in names},
        base_year=base_year, years=[], scenarios=names,
    )

    projected = {s: [v for v in vs if v.fiscal_year > base_year] for s, vs in ordered.items()}
    if not any(len(vs) >= 1 for vs in projected.values()):
        record.skipped = "not_projected"
        return record
    if max((len(vs) for vs in ordered.values()), default=0) < MIN_POINTS:
        record.skipped = "fewer_than_two_points"
        return record
    if unit_kind in ("currency", "currency_per_share") and not currency:
        record.skipped = "currency_not_uniform"
        return record

    years = sorted({v.fiscal_year for vs in ordered.values() for v in vs})
    record.years = years
    record.table = {s: {f"FY{v.fiscal_year}": float(v.value) for v in ordered[s]} for s in names}
    record.methods = {s: next((v.method for v in projected.get(s, [])), "not_projected") for s in names}

    fig, ax = plt.subplots(figsize=(7.2, 3.8), dpi=150)
    for i, scenario in enumerate(names):
        values = ordered[scenario]
        colour = PALETTE[i % len(PALETTE)]
        dash = DASHES[i % len(DASHES)]
        xs = [v.fiscal_year for v in values]
        ys = [float(v.value) for v in values]

        actual = [(x, y) for x, y, v in zip(xs, ys, values, strict=True) if v.fiscal_year <= base_year]
        ahead = [(x, y) for x, y, v in zip(xs, ys, values, strict=True) if v.fiscal_year >= base_year]
        if actual:
            ax.plot([p[0] for p in actual], [p[1] for p in actual], color=colour, linewidth=2.0,
                    solid_capstyle="round", zorder=3)
        if len(ahead) >= 2:
            line, = ax.plot([p[0] for p in ahead], [p[1] for p in ahead], color=colour, linewidth=1.8, zorder=3)
            if dash:
                line.set_dashes(dash)
            else:
                line.set_dashes((4, 2))  # every projection is dashed, base case included
        for x, y, v in zip(xs, ys, values, strict=True):
            ax.plot([x], [y], marker="o", markersize=4.5, color=colour, zorder=4,
                    markerfacecolor=colour if v.is_actual else "white")
        if ahead:
            ax.annotate(f" {scenario.replace('_', ' ').capitalize()}", (ahead[-1][0], ahead[-1][1]), color=colour, fontsize=8.5,
                        va="center", ha="left", zorder=5)

    ax.axvline(base_year, color="#8a8f98", linewidth=0.9, linestyle=(0, (2, 3)), zorder=1)
    # Sits low and to the right of the rule, where the header text and the first data point are not.
    ax.annotate(" last reported", (base_year, 0.02), xycoords=("data", "axes fraction"),
                fontsize=7.5, color="#5b6470", ha="left", va="bottom")

    ax.set_xticks(years, [f"FY{y}" for y in years])
    ax.margins(x=0.12)
    ax.grid(axis="y", color="#e3e6ea", linewidth=0.8, zorder=0)
    ax.yaxis.set_major_locator(MaxNLocator(nbins=6))
    if unit_kind == "ratio":
        ax.yaxis.set_major_formatter(PercentFormatter(1.0, decimals=1))
    elif unit_kind in ("currency", "currency_per_share"):
        biggest = max(abs(float(v.value)) for vs in ordered.values() for v in vs)
        formatter, unit_label = _currency_formatter(biggest, currency)
        ax.yaxis.set_major_formatter(formatter)
        ax.set_ylabel(unit_label, color="#5b6470")
    elif max(abs(float(v.value)) for vs in ordered.values() for v in vs) >= 1e6:
        ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v / 1e6:,.0f}"))
        ax.set_ylabel("millions", color="#5b6470")
    else:
        ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:,.0f}"))

    ax.set_title(f"{record.title}\n", loc="left")
    ax.text(0, 1.02, company_label, transform=ax.transAxes, color="#5b6470", fontsize=8.5)
    record.fallback_for = sorted({f for vs in ordered.values() for v in vs for f in v.fallback_for})
    caption = ("Estimates, not company guidance. Solid to the last reported year, dashed after it;\n"
               "filled markers are reported, hollow are estimated. Source: company filings with the SEC.")
    if record.fallback_for:
        caption += (f"\n‡ Uses a trend estimate for {', '.join(in_sentence(_title(framework, m)) for m in record.fallback_for)}: "
                    "an input the full model needs isn't disclosed.")
    fig.text(0.01, 0.01, caption, fontsize=7, color="#5b6470")
    fig.tight_layout(rect=(0, 0.12 if record.fallback_for else 0.09, 1, 1))

    out_dir.mkdir(parents=True, exist_ok=True)
    for ext in ("svg", "png"):
        path = out_dir / f"{record.chart_id}.{ext}"
        fig.savefig(path, format=ext, metadata={"Date": None} if ext == "svg" else {"Software": None})
        record.files.append(f"forecast_charts/{path.name}")
    plt.close(fig)
    return record


def render_forecast_charts(result, framework: IndustryFramework, out_dir: Path, *,
                           company_label: str, engine_version: str) -> list[ForecastChartRecord]:
    """One chart per metric that at least one scenario projects, in driver-graph order."""
    by_metric: dict[str, dict[str, list[ForecastValue]]] = {}
    for scenario, series in result.by_scenario.items():
        for metric_id, points in series.items():
            by_metric.setdefault(metric_id, {})[scenario] = list(points.values())
    ordered = [m for m in result.graph.order if m in by_metric]
    records = [
        render_forecast_chart(metric_id, by_metric[metric_id], framework, out_dir,
                              base_year=result.base_year, company_label=company_label,
                              engine_version=engine_version)
        for metric_id in ordered
    ]
    remove_stale_images(out_dir, [r.chart_id for r in records if r.skipped])
    return records


def remove_stale_images(out_dir: Path, chart_ids: list[str]) -> None:
    """Delete images a previous run rendered for charts this run skipped.

    A skipped chart writes nothing, so without this its old image outlives the reason it existed:
    a projection the engine now refuses would still sit in the output folder looking current.
    """
    for chart_id in chart_ids:
        for ext in ("svg", "png"):
            (out_dir / f"{chart_id}.{ext}").unlink(missing_ok=True)
