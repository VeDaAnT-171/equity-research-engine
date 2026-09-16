"""Framework-defined charts rendered to SVG and PNG, with derived and flagged points marked."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.ticker import FuncFormatter, MaxNLocator, PercentFormatter  # noqa: E402

from ..schemas.analytics import AnalyticValue  # noqa: E402
from ..schemas.financial import FinancialFact, FiscalPeriodCode, Provenance  # noqa: E402
from ..schemas.framework import ChartSpec, IndustryFramework  # noqa: E402

PALETTE = ("#1f3a5f", "#3f8f8a", "#8a8f98", "#c7862f", "#6b4c9a", "#b24a3b")
plt.rcParams.update({
    "svg.hashsalt": "research-engine",  # deterministic SVG ids
    "font.family": "DejaVu Sans",
    "font.size": 9,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.edgecolor": "#8a8f98",
    "axes.titlesize": 11,
    "axes.titleweight": "bold",
    "axes.titlelocation": "left",
})


@dataclass
class Point:
    year: int
    value: float
    derived: bool
    flagged: bool
    lineage_id: str
    currency: Optional[str] = None


@dataclass
class ChartRecord:
    chart_id: str
    title: str
    files: list[str]
    series: dict[str, list[str]]  # series id -> lineage ids (fact ids or analytic value ids)
    years: list[int]
    derived_points: int
    flagged_points: int
    skipped: Optional[str] = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def series_points(series_id: str, framework: IndustryFramework, facts: list[FinancialFact],
                  analytics: dict[str, dict[int, AnalyticValue]], flagged_fact_ids: set[str]) -> list[Point]:
    if series_id in framework.analytic_ids:
        return [Point(y, float(v.value), v.uses_derived_facts, bool(v.quality_flags), v.value_id, v.currency)
                for y, v in sorted(analytics.get(series_id, {}).items())]
    return [Point(f.period.fiscal_year, float(f.value), f.provenance is Provenance.DERIVED,
                  f.fact_id in flagged_fact_ids, f.fact_id, f.currency)
            for f in sorted(facts, key=lambda f: f.period.fiscal_year)
            if f.metric_id == series_id and f.period.fiscal_period is FiscalPeriodCode.FY]


def _label(series_id: str, framework: IndustryFramework) -> str:
    if series_id in framework.analytic_ids:
        return framework.analytic(series_id).name
    return framework.metric(series_id).name


def _currency_formatter(max_abs: float, currency: str):
    for divisor, suffix in ((1e9, "bn"), (1e6, "m"), (1e3, "k")):
        if max_abs >= divisor:
            places = 1 if max_abs / divisor < 10 else 0
            return FuncFormatter(lambda v, _: f"{v / divisor:,.{places}f}"), f"{currency} {suffix}"
    return FuncFormatter(lambda v, _: f"{v:,.0f}"), currency


def render_chart(spec: ChartSpec, framework: IndustryFramework, facts: list[FinancialFact],
                 analytics: dict[str, dict[int, AnalyticValue]], flagged_fact_ids: set[str], out_dir: Path,
                 *, company_label: str, engine_version: str) -> ChartRecord:
    data = {sid: series_points(sid, framework, facts, analytics, flagged_fact_ids) for sid in spec.series}
    data = {sid: pts for sid, pts in data.items() if pts}
    record = ChartRecord(spec.id, spec.title, [], {sid: [p.lineage_id for p in pts] for sid, pts in data.items()}, [], 0, 0)
    if not any(len(pts) >= 2 for pts in data.values()):
        record.skipped = "fewer_than_two_points"
        return record
    missing = [sid for sid in spec.series if sid not in data]
    if missing:
        record.notes.append(f"series without data: {', '.join(missing)}")
    currencies = {p.currency for pts in data.values() for p in pts if p.currency}
    if spec.format == "currency" and len(currencies) != 1:
        record.skipped = "currency_not_uniform"
        return record

    years = sorted({p.year for pts in data.values() for p in pts})
    record.years = years
    x_of = {y: i for i, y in enumerate(years)}
    fig, ax = plt.subplots(figsize=(7.2, 3.8), dpi=150)
    n = len(data)
    width = 0.8 / n
    dagger_offsets = []
    for i, (sid, pts) in enumerate(data.items()):
        color = PALETTE[i % len(PALETTE)]
        label = _label(sid, framework)
        xs = [x_of[p.year] for p in pts]
        ys = [p.value for p in pts]
        if spec.kind == "bar":
            offsets = [x - 0.4 + width * (i + 0.5) for x in xs]
            bars = ax.bar(offsets, ys, width=width * 0.92, color=color, label=label, zorder=2)
            for bar, p in zip(bars, pts):
                if p.derived:
                    bar.set_hatch("////")
                    bar.set_facecolor("white")
                    bar.set_edgecolor(color)
            dagger_offsets += [(o, p) for o, p in zip(offsets, pts)]
        else:
            ax.plot(xs, ys, color=color, linewidth=1.8, label=label, zorder=2)
            for x, p in zip(xs, pts):
                ax.plot([x], [p.value], marker="o", markersize=5, color=color,
                        markerfacecolor="white" if p.derived else color, zorder=3)
            dagger_offsets += [(x, p) for x, p in zip(xs, pts)]
        record.derived_points += sum(p.derived for p in pts)
        record.flagged_points += sum(p.flagged for p in pts)
    for x, p in dagger_offsets:
        if p.flagged:
            ax.annotate("†", (x, p.value), textcoords="offset points", xytext=(0, 6), ha="center", color="#b24a3b")

    ax.set_xticks(range(len(years)), [f"FY{y}" for y in years])
    ax.grid(axis="y", color="#e3e6ea", linewidth=0.8, zorder=0)
    ax.axhline(0, color="#8a8f98", linewidth=0.8, zorder=1)
    ax.yaxis.set_major_locator(MaxNLocator(nbins=6))
    if spec.format == "percent":
        ax.yaxis.set_major_formatter(PercentFormatter(1.0, decimals=1))
    elif spec.format == "multiple":
        ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:.1f}x"))
    elif spec.format == "currency":
        max_abs = max(abs(p.value) for pts in data.values() for p in pts)
        formatter, unit_label = _currency_formatter(max_abs, next(iter(currencies)))
        ax.yaxis.set_major_formatter(formatter)
        ax.set_ylabel(unit_label, color="#5b6470")
    ax.set_title(f"{spec.title}\n", loc="left")
    ax.text(0, 1.02, company_label, transform=ax.transAxes, color="#5b6470", fontsize=8.5)
    if len(spec.series) > 1:  # a multi-series chart names its series even when only one has data
        ax.legend(frameon=False, loc="upper left", bbox_to_anchor=(0, -0.12), ncol=min(n, 3), fontsize=8.5)
    footnote = f"Source: company SEC XBRL filings; computed by research-engine {engine_version}."
    if record.derived_points:
        footnote += " Hollow markers / hatched bars: derived by the engine."
    if record.flagged_points:
        footnote += " †: an input was flagged by data-quality checks."
    fig.text(0.01, 0.01, footnote, fontsize=7, color="#5b6470")
    fig.tight_layout(rect=(0, 0.05 if len(spec.series) <= 1 else 0.08, 1, 1))

    out_dir.mkdir(parents=True, exist_ok=True)
    for ext in ("svg", "png"):
        path = out_dir / f"{spec.id}.{ext}"
        fig.savefig(path, format=ext, metadata={"Date": None} if ext == "svg" else {"Software": None})
        record.files.append(f"charts/{path.name}")
    plt.close(fig)
    return record


def render_charts(framework: IndustryFramework, facts: list[FinancialFact], analytics: dict[str, dict[int, AnalyticValue]],
                  flagged_fact_ids: set[str], out_dir: Path, *, company_label: str, engine_version: str) -> list[ChartRecord]:
    return [render_chart(spec, framework, facts, analytics, flagged_fact_ids, out_dir,
                         company_label=company_label, engine_version=engine_version) for spec in framework.charts]
