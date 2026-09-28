"""Tables-only forecast in Markdown. Every projection is labelled with the assumption behind it."""

from __future__ import annotations

from ..analysis.report import fmt
from ..schemas.assumption import AssumptionType
from ..schemas.framework import IndustryFramework
from .engine import ForecastResult

METHOD_LABEL = {
    "actual": "reported",
    "driver_formula": "driver",
    "derivation": "derivation",
    "growth": "growth assumption",
    "level": "level assumption",
}

TYPE_LABEL = {
    AssumptionType.SCENARIO.value: "scenario",
    AssumptionType.ANALYST_ASSUMPTION.value: "analyst",
    AssumptionType.MANAGEMENT_GUIDANCE.value: "guidance",
    AssumptionType.CONSENSUS.value: "consensus",
    AssumptionType.HISTORICAL.value: "history",
    AssumptionType.DERIVED.value: "derived",
}


def _unit_of(framework: IndustryFramework, metric_id: str) -> str:
    try:
        return framework.metric(metric_id).unit_kind
    except KeyError:  # pragma: no cover - metrics always resolve in practice
        return "ratio"


def render_markdown(*, company_id: str, framework: IndustryFramework, result: ForecastResult,
                    versions: dict) -> str:
    years = list(result.forecast_years)
    span = f"FY{years[0]}–FY{years[-1]}" if years else "–"
    lines = [
        f"# Forecast: {company_id}", "",
        f"Framework **{framework.name}** · base year FY{result.base_year} · forecast {span} · "
        f"engine {versions['engine_version']}", "",
        "> **Classification: MODEL OUTPUT.** Nothing below is a fact. Each projected figure is produced by the "
        "framework's driver graph from assumptions listed in this document, starting from the last reported fiscal "
        "year. The `source` column on every assumption says whether it came from the analyst, from cited management "
        "guidance, from consensus, or from the engine's own reading of history. Metrics the engine could not project "
        "are listed rather than filled in.", "",
    ]

    if result.graph.fallbacks:
        # Placed before the numbers, not after them: a reader who stops at the table should
        # already know which of its rows rest on a trend rather than the declared model.
        lines += ["## Fallbacks in force", "",
                  "> These metrics are **not** produced by the model the framework declares for them. The company "
                  "does not report an input that model needs, so each is projected on its own history instead, and "
                  "every figure computed from one is marked **‡** in the tables below.", "",
                  "| Metric | Why the declared driver was not used |", "|---|---|"]
        lines += [f"| `{m}` | {why} |" for m, why in sorted(result.graph.fallbacks.items())]
        lines.append("")

    lines += ["## Projection plan", "",
              "How each metric obtains its value, in evaluation order. This is framework data, not engine logic.", "",
              "| Metric | Method | Formula | Why |", "|---|---|---|---|"]
    for metric_id in result.graph.order:
        rule = result.graph.rule(metric_id)
        lines.append(f"| `{metric_id}` | {METHOD_LABEL[rule.method]} | `{rule.formula}` | {rule.reason} |")
    lines.append("")

    scenarios = list(result.by_scenario)
    for scenario in scenarios:
        spec = result.assumptions.scenarios[scenario]
        series = result.by_scenario[scenario]
        lines += [f"## Scenario: {spec.name} (`{scenario}`)", ""]
        if spec.description:
            lines += [spec.description, ""]
        header = ["Metric", f"FY{result.base_year} (actual)"] + [f"FY{y}" for y in years] + ["Method"]
        lines += ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
        for metric_id in sorted(series):
            row = series[metric_id]
            unit = _unit_of(framework, metric_id)
            currency = next((v.currency for v in row.values() if v.currency), None)
            base = row.get(result.base_year)
            cells = [f"`{metric_id}`", fmt(float(base.value), unit, currency) if base else "–"]
            for y in years:
                v = row.get(y)
                mark = " ‡" if v is not None and v.fallback_for else ""
                cells.append(fmt(float(v.value), unit, currency) + mark if v else "–")
            projected = next((row[y] for y in years if y in row), None)
            method = METHOD_LABEL[projected.method] if projected else "not projected"
            if projected is not None and projected.fallback_for:
                method += f" ‡ (rests on {', '.join(projected.fallback_for)} trend)"
            cells.append(method)
            lines.append("| " + " | ".join(cells) + " |")
        lines.append("")
        if any(v.fallback_for for row in series.values() for v in row.values()):
            lines += ["‡ Depends on a metric projected by fallback rather than by its declared driver; "
                      "see *Fallbacks in force*.", ""]

    lines += ["## Assumptions in force", "",
              "Resolution order is fixed: scenario, then analyst, then management guidance, then consensus, then "
              "the engine's seeded history. Within one rung, an assumption pinned to a fiscal year beats one that "
              "applies to every year.", "",
              "| Assumption | Value | Unit | Source | Period | Scenario | Basis / rationale | Cited document |",
              "|---|---|---|---|---|---|---|---|"]
    for a in sorted(result.assumptions.all, key=lambda a: (a.assumption_id, a.type.value, a.period or "")):
        value = "not set" if a.value is None else (
            f"{float(a.value) * 100:.2f}%" if a.unit == "ratio" else f"{float(a.value):,.4g}")
        basis = a.rationale or a.description
        lines.append(
            f"| `{a.assumption_id}` | {value} | {a.unit} | {TYPE_LABEL.get(a.type.value, a.type.value)} | "
            f"{a.period or 'all years'} | {a.scenario or '–'} | {basis} | "
            f"{'`' + a.source_document_id + '`' if a.source_document_id else '–'} |"
        )
    lines.append("")

    if result.unseeded:
        lines += ["## Assumptions the engine could not seed", "",
                  "These inputs have no value until the analyst supplies one; metrics depending on them are "
                  "not projected.", "", "| Assumption | Reason |", "|---|---|"]
        lines += [f"| `{k}` | {v} |" for k, v in sorted(result.unseeded.items())]
        lines.append("")

    if result.not_projected:
        lines += ["## Not projected (base scenario)", "",
                  "| Metric and reason | Forecast years affected |", "|---|---|"]
        lines += [f"| `{k}` | {v} |" for k, v in sorted(result.not_projected.items())]
        lines.append("")

    if result.graph.unresolved:
        lines += ["## Unresolved forecast targets", "", "| Target | Reason |", "|---|---|"]
        lines += [f"| `{k}` | {v} |" for k, v in sorted(result.graph.unresolved.items())]
        lines.append("")

    return "\n".join(lines) + "\n"
