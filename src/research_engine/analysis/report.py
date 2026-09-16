"""Tables-only historical analysis in Markdown. Every figure is labelled as a model output; no interpretation."""

from __future__ import annotations

from typing import Optional

from ..schemas.framework import IndustryFramework

CATEGORY_ORDER = ("growth", "profitability", "returns", "efficiency", "capital_intensity", "leverage", "liquidity",
                  "credit", "capital", "per_share", "operating")


def fmt(value: Optional[float], unit_kind: str, currency: Optional[str] = None) -> str:
    if value is None:
        return "–"
    if unit_kind == "ratio":
        return f"{value * 100:.1f}%"
    if unit_kind == "multiple":
        return f"{value:.2f}x"
    if unit_kind in ("currency", "currency_per_share"):
        prefix = f"{currency} " if currency else ""
        for divisor, suffix in ((1e9, "bn"), (1e6, "m")):
            if abs(value) >= divisor:
                return f"{prefix}{value / divisor:,.1f}{suffix}"
        return f"{prefix}{value:,.2f}"
    return f"{value:,.2f}"


def render_markdown(*, company_id: str, framework: IndustryFramework, fiscal_years: list[int], summaries: dict,
                    not_computed: dict, charts: list[dict], currencies: dict[str, Optional[str]], versions: dict) -> str:
    lines = [
        f"# Historical analysis: {company_id}", "",
        f"Framework **{framework.name}** · fiscal years {fiscal_years[0] if fiscal_years else '–'}–"
        f"{fiscal_years[-1] if fiscal_years else '–'} · engine {versions['engine_version']}", "",
        "> **Classification: MODEL OUTPUT.** Every figure below is computed by the engine from reported facts and "
        "engine-derived facts (see `historical_financials.parquet` for lineage). Statistics are descriptive; nothing "
        "here is an inference about causes or a forecast.", "",
    ]
    by_category: dict[str, list[str]] = {}
    for aid, s in summaries.items():
        by_category.setdefault(s["category"], []).append(aid)
    for category in CATEGORY_ORDER:
        ids = by_category.get(category)
        if not ids:
            continue
        lines += [f"## {category.replace('_', ' ').title()}", "",
                  "| Analytic | Basis | Latest | 3y avg | 5y avg | Median | Min | Max | Trend / yr | CAGR 5y | Notes |",
                  "|---|---|---|---|---|---|---|---|---|---|---|"]
        for aid in ids:
            s = summaries[aid]
            spec = framework.analytic(aid)
            uk, cur = s["unit_kind"], currencies.get(aid)
            basis = {"level": "as reported", "growth": "year on year", "elasticity": "year on year",
                     "expression": "period end"}.get(spec.kind, spec.denominator_basis.replace("_", " "))
            notes = []
            if s["flagged_years"]:
                notes.append("† flagged " + ", ".join(f"FY{y}" for y in s["flagged_years"]))
            if s["years_using_derived_facts"]:
                notes.append("uses derived facts")
            dd = s.get("underlying_max_drawdown")
            if dd:
                notes.append(f"max decline {dd['decline'] * 100:.1f}% (FY{dd['peak_year']}→FY{dd['trough_year']})")
            if s["kind"] == "growth" and s["down_years"]:
                notes.append(f"{s['down_years']} down year(s)")
            trend = s["trend_per_year"]
            lines.append(
                f"| {s['name']} | {basis} | {fmt(s['latest'], uk, cur)} (FY{s['latest_year']}) | {fmt(s['avg_3y'], uk, cur)} | "
                f"{fmt(s['avg_5y'], uk, cur)} | {fmt(s['median'], uk, cur)} | {fmt(s['min']['value'], uk, cur)} (FY{s['min']['year']}) | "
                f"{fmt(s['max']['value'], uk, cur)} (FY{s['max']['year']}) | {fmt(trend, uk, cur) if trend is not None else '–'} | "
                f"{fmt(s.get('cagr_5y'), 'ratio') if 'cagr_5y' in s else '–'} | {'; '.join(notes)} |"
            )
        lines.append("")
    missing = [aid for aid in framework.analytic_ids if aid not in summaries]
    lines += ["## Not computed", "",
              "Values are never estimated to fill gaps. Reasons, counted by analytic-year:", "",
              "| Analytic | Reason | Years |", "|---|---|---|"]
    lines += [f"| {key.split(':', 1)[0]} | {key.split(':', 1)[1]} | {count} |" for key, count in sorted(not_computed.items())]
    if missing:
        lines += ["", f"No values at all for: {', '.join(sorted(missing))}."]
    lines += ["", "## Charts", ""]
    for c in charts:
        if c["skipped"]:
            lines.append(f"- {c['title']}: not rendered ({c['skipped']})")
        else:
            lines.append(f"- [{c['title']}]({c['files'][0]})" + (f" ({'; '.join(c['notes'])})" if c["notes"] else ""))
    lines += ["", "## Method", "",
              "- Ratios with balance-sheet denominators use the stated basis: average of opening and closing, or opening balance.",
              "- Ratios with non-positive denominators, and growth from a non-positive base, are not computed.",
              "- Facts failing error-severity data-quality checks are excluded; warning-severity flags are carried to every value built on them (†).",
              "- Trend is the OLS slope over all available years (minimum 5). CAGR uses positive endpoints only.",
              "- Segment-level analysis is not available: SEC companyfacts carries no segment dimensions.", ""]
    return "\n".join(lines)
