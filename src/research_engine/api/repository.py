"""Read-only access to what the pipeline already wrote.

The API computes nothing. Every number it serves was produced by `ingest`, `quality`, `analyze`
or `forecast` and written to a company's `output/` directory, so the dashboard cannot disagree
with the files an analyst would read directly. When a stage has not been run, that is reported
as a missing stage rather than as an empty result: a dashboard showing zero issues because the
quality stage never ran would be worse than one showing nothing at all.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from ..config import load_project_config
from ..errors import ConfigError, ResearchEngineError
from ..schemas.company import ProjectConfig

STAGE_FILES = {
    "ingest": "manifest.json",
    "quality": "quality_manifest.json",
    "analyze": "analysis_manifest.json",
    "forecast": "forecast_manifest.json",
}
STAGE_COMMANDS = {
    "ingest": "make ingest CONFIG=<config>",
    "quality": "make quality CONFIG=<config>",
    "analyze": "make analyze CONFIG=<config>",
    "forecast": "make forecast CONFIG=<config>",
}


class StageNotRun(ResearchEngineError):
    """A requested artifact needs a pipeline stage that has not been run for this company."""

    def __init__(self, company_id: str, stage: str):
        super().__init__(
            f"the {stage!r} stage has not been run for {company_id!r}; "
            f"run `{STAGE_COMMANDS[stage]}` and reload"
        )
        self.company_id = company_id
        self.stage = stage


class UnknownCompany(ResearchEngineError):
    def __init__(self, company_id: str, known: list[str]):
        listed = ", ".join(known) if known else "none configured"
        super().__init__(f"no company {company_id!r} in this workspace (available: {listed})")


@dataclass(frozen=True)
class CompanyWorkspace:
    """One `companies/<id>/` directory: its config and whatever the pipeline has written."""

    company_id: str
    config_path: Path
    root: Path
    config: ProjectConfig = field(repr=False)

    @property
    def output(self) -> Path:
        return self.root / "output"

    @property
    def charts(self) -> Path:
        return self.output / "charts"

    def has(self, stage: str) -> bool:
        return (self.output / STAGE_FILES[stage]).is_file()

    def stages(self) -> dict[str, bool]:
        return {stage: self.has(stage) for stage in STAGE_FILES}

    def require(self, stage: str) -> None:
        if not self.has(stage):
            raise StageNotRun(self.company_id, stage)

    # ---- raw readers ---------------------------------------------------------------------

    def json(self, name: str, stage: str) -> dict:
        self.require(stage)
        path = self.output / name
        if not path.is_file():
            raise StageNotRun(self.company_id, stage)
        return json.loads(path.read_text(encoding="utf-8"))

    def parquet(self, name: str, stage: str) -> list[dict]:
        self.require(stage)
        path = self.output / name
        if not path.is_file():
            raise StageNotRun(self.company_id, stage)
        try:
            import pyarrow.parquet as pq
        except ImportError:  # pragma: no cover - pyarrow is a core dependency
            raise ConfigError("pyarrow is required to read pipeline datasets") from None
        return pq.read_table(path).to_pylist()

    def text(self, name: str, stage: str) -> str:
        self.require(stage)
        path = self.output / name
        if not path.is_file():
            raise StageNotRun(self.company_id, stage)
        return path.read_text(encoding="utf-8")

    def chart_file(self, chart_id: str, fmt: str) -> Path:
        """Resolve a chart by id. The id never reaches the filesystem unvalidated."""
        self.require("analyze")
        if fmt not in ("svg", "png"):
            raise UnknownChart(chart_id)
        # A chart the index records as skipped has no current image, whatever is on disk. A file
        # left by an earlier run would otherwise be served as though it described today's data.
        rendered = {c["chart_id"] for c in self.chart_index() if not c.get("skipped")}
        if chart_id not in rendered:
            raise UnknownChart(chart_id)
        path = (self.charts / f"{chart_id}.{fmt}").resolve()
        if not path.is_file() or self.charts.resolve() not in path.parents:
            raise UnknownChart(chart_id)
        return path

    def chart_index(self) -> list[dict]:
        self.require("analyze")
        path = self.charts / "index.json"
        if not path.is_file():
            return []
        return [c for c in json.loads(path.read_text(encoding="utf-8")) if not c.get("skipped")]

    # ---- forecast charts ------------------------------------------------------------------

    @property
    def forecast_charts(self) -> Path:
        return self.output / "forecast_charts"

    def forecast_chart_index(self) -> list[dict]:
        self.require("forecast")
        path = self.forecast_charts / "index.json"
        if not path.is_file():
            return []
        return [c for c in json.loads(path.read_text(encoding="utf-8")) if not c.get("skipped")]

    def forecast_chart_file(self, chart_id: str, fmt: str) -> Path:
        self.require("forecast")
        if fmt not in ("svg", "png"):
            raise UnknownChart(chart_id)
        # Same rule as chart_file. This was live: an operating-cash-flow projection rendered by an
        # earlier run stayed on disk after the metric stopped being projected, the index correctly
        # marked it skipped, and this endpoint served the stale picture anyway.
        if chart_id not in {c["chart_id"] for c in self.forecast_chart_index() if not c.get("skipped")}:
            raise UnknownChart(chart_id)
        path = (self.forecast_charts / f"{chart_id}.{fmt}").resolve()
        if not path.is_file() or self.forecast_charts.resolve() not in path.parents:
            raise UnknownChart(chart_id)
        return path


class UnknownChart(ResearchEngineError):
    def __init__(self, chart_id: str):
        super().__init__(f"no rendered chart {chart_id!r} for this company")


class Repository:
    """Discovers company workspaces under a companies root and serves their outputs."""

    def __init__(self, companies_root: Path):
        self.root = Path(companies_root).resolve()
        if not self.root.is_dir():
            raise ConfigError(f"{self.root} is not a directory; pass --companies to point at one")

    def _discover(self) -> dict[str, CompanyWorkspace]:
        found: dict[str, CompanyWorkspace] = {}
        for config_path in sorted(self.root.glob("*/config.yaml")):
            try:
                config = load_project_config(config_path)
            except ResearchEngineError:
                continue  # an invalid config is surfaced by `validate`, not by the dashboard
            found[config.company_id] = CompanyWorkspace(
                company_id=config.company_id, config_path=config_path,
                root=config_path.parent, config=config,
            )
        return found

    @property
    def companies(self) -> dict[str, CompanyWorkspace]:
        return self._discover()

    def get(self, company_id: str) -> CompanyWorkspace:
        found = self.companies
        if company_id not in found:
            raise UnknownCompany(company_id, sorted(found))
        return found[company_id]

    def __iter__(self) -> Iterator[CompanyWorkspace]:
        return iter(self.companies.values())


# ---- shaping helpers used by the routes ---------------------------------------------------

def overview_of(ws: CompanyWorkspace) -> dict[str, Any]:
    company = ws.config.company
    data: dict[str, Any] = {
        "company_id": ws.company_id,
        "name": company.name,
        "ticker": company.ticker,
        "exchange": company.exchange,
        "country": company.country,
        "sector": company.sector,
        "reporting_currency": company.reporting_currency,
        "stages": ws.stages(),
        "framework": None,
        "entity": None,
        "fiscal_calendar": None,
        "versions": None,
        "counts": {},
        "warnings": [],
    }
    if ws.has("ingest"):
        manifest = ws.json("manifest.json", "ingest")
        fw = manifest.get("framework") or {}
        data["framework"] = {"name": fw.get("name"), "method": fw.get("method"),
                             "evidence": fw.get("evidence")}
        data["fiscal_calendar"] = manifest.get("fiscal_calendar")
        data["versions"] = {k: manifest.get(k) for k in
                            ("engine_version", "schema_version", "parser_version")}
        data["counts"]["facts"] = manifest.get("facts")
        data["counts"]["facts_current"] = manifest.get("facts_current")
        data["counts"]["documents"] = len(manifest.get("documents") or [])
        data["warnings"] = manifest.get("warnings") or []
        data["generated_at"] = manifest.get("generated_at")
    if ws.has("quality"):
        qm = ws.json("quality_manifest.json", "quality")
        data["counts"]["facts_derived"] = qm.get("facts_derived")
        data["counts"]["issues"] = qm.get("issue_counts")
    if ws.has("analyze"):
        am = ws.json("analysis_manifest.json", "analyze")
        data["counts"]["analytic_values"] = am.get("analytic_values")
        data["counts"]["charts"] = am.get("charts_rendered")
    if ws.has("forecast"):
        fm = ws.json("forecast_manifest.json", "forecast")
        data["counts"]["forecast_values"] = fm.get("forecast_values")
        data["forecast_horizon"] = {
            "base_year": fm.get("base_year"), "years": fm.get("forecast_years") or [],
            "scenarios": fm.get("scenarios") or [],
            "assumptions_file": fm.get("assumptions_file"),
        }
    return data


def documents_of(ws: CompanyWorkspace) -> list[dict[str, Any]]:
    manifest = ws.json("manifest.json", "ingest")
    return manifest.get("documents") or []


def glossary_of(ws: CompanyWorkspace) -> dict[str, Any]:
    """Names and vocabulary written by `analyze`. Absent for older runs: readers then see ids."""
    path = ws.output / "glossary.json"
    if not path.is_file():
        return {"labels": {}, "metric_order": [], "statements": {}, "units": {}, "analytic_order": [],
                "categories": {}, "document_only": [], "statement_labels": {}, "category_labels": {},
                "check_labels": {}, "sector": None, "framework": None}
    return json.loads(path.read_text(encoding="utf-8"))


def _names(ws: CompanyWorkspace) -> tuple[dict[str, str], frozenset[str]]:
    g = glossary_of(ws)
    return g.get("labels") or {}, frozenset(g.get("document_only") or ())


def studied_documents(ws: CompanyWorkspace) -> dict[str, dict[str, Any]]:
    """document_id -> what the last ingest found in that document (empty before any document)."""
    path = ws.output / "documents.json"
    if not path.is_file():
        return {}
    try:
        return {d["document_id"]: d for d in json.loads(path.read_text(encoding="utf-8")).get("documents", [])}
    except (ValueError, KeyError, TypeError):
        return {}


_STATUS_LABEL = {"verified": "In use", "unverified": "Not used", "rejected": "Rejected", "pending": "Waiting to be analysed"}


def library_of(ws: CompanyWorkspace) -> dict[str, Any]:
    """The company's documents: where each came from, whether it passed its checks, what it added."""
    from ..documents.library import KIND_LABEL, DocumentLibrary
    labels = glossary_of(ws).get("labels") or {}
    reports = list(studied_documents(ws).values())
    by_entry = {r.get("entry_id"): r for r in reports if r.get("entry_id")}
    items = []

    def contribution(report: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"metric_id": m, "label": labels.get(m, m), "periods": sorted(p)}
                for m, p in sorted((report.get("contributed") or {}).items(), key=lambda kv: labels.get(kv[0], kv[0]))]

    for entry in DocumentLibrary(ws.root).entries():
        report = by_entry.get(entry.id)
        status = report["status"] if report else "pending"
        if status == "verified" and report and not report.get("contributed"):
            label = "Checked — nothing new"
        else:
            label = _STATUS_LABEL.get(status, status)
        items.append({
            "id": entry.id, "document_id": report.get("document_id") if report else None,
            "title": entry.title, "kind": entry.kind, "kind_label": KIND_LABEL.get(entry.kind, entry.kind),
            "added_by": entry.added_by, "added_at": entry.added_at, "media_type": entry.media_type or (report or {}).get("media_type"),
            "url": entry.url, "has_file": bool(entry.file), "bytes": entry.bytes,
            "form": (report or {}).get("form"), "filed": (report or {}).get("filed"),
            "status": status, "status_label": label,
            "reason": (report or {}).get("reason") or "It will be read on the next analysis run.",
            "checked": (report or {}).get("checked", 0), "agreed": (report or {}).get("agreed", 0),
            "contributed": contribution(report) if report else [],
        })
    for report in reports:
        if report.get("entry_id"):
            continue
        items.append({
            "id": report["document_id"], "document_id": report["document_id"], "title": report["title"],
            "kind": report["kind"], "kind_label": report.get("kind_label") or report["kind"], "added_by": "config",
            "added_at": None, "media_type": report.get("media_type"), "url": report.get("url"), "has_file": False,
            "bytes": None, "form": report.get("form"), "filed": report.get("filed"), "status": report["status"],
            "status_label": _STATUS_LABEL.get(report["status"], report["status"]), "reason": report["reason"],
            "checked": report.get("checked", 0), "agreed": report.get("agreed", 0), "contributed": contribution(report),
        })
    cik = ws.config.company.identifiers.cik
    return {
        "structured_source": {
            "title": "SEC structured financial data (XBRL)",
            "url": f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type=10-K" if cik else None,
        },
        "documents": items,
    }


def library_file(ws: CompanyWorkspace, entry_id: str) -> tuple[Path, str] | None:
    from ..documents.library import DocumentLibrary
    library = DocumentLibrary(ws.root)
    entry = library.get(entry_id)
    path = library.path_of(entry) if entry else None
    return (path, entry.media_type or "") if path else None


def financials_of(ws: CompanyWorkspace) -> dict[str, Any]:
    """Full-year figures by statement, as reported (or derived, and marked so) — no new numbers."""
    from ..presentation import STATEMENT_LABEL
    g = glossary_of(ws)
    labels = g.get("labels") or {}
    order = {m: i for i, m in enumerate(g.get("metric_order") or [])}
    statements = g.get("statements") or {}
    units = g.get("units") or {}
    rows: dict[str, dict[str, Any]] = {}
    years: set[int] = set()
    currency = None
    studied = studied_documents(ws)
    for fact in ws.parquet("historical_financials.parquet", "analyze"):
        if fact["fiscal_period"] != "FY":
            continue
        metric = fact["metric_id"]
        row = rows.setdefault(metric, {"metric_id": metric, "label": labels.get(metric, metric),
                                       "unit_kind": units.get(metric), "values": {}})
        years.add(fact["fiscal_year"])
        currency = currency or fact["currency"]
        row["values"][str(fact["fiscal_year"])] = {
            "value": fact["value"], "fact_id": fact["fact_id"],
            "derived": fact["provenance"] == "derived",
            "form": fact["filing_form"], "filed": str(fact["filed_date"]) if fact["filed_date"] else None,
            "document": (studied.get(fact["document_id"]) or {}).get("title"),
        }
    grouped: dict[str, list[dict[str, Any]]] = {}
    for metric, row in sorted(rows.items(), key=lambda kv: (order.get(kv[0], 10_000), kv[0])):
        grouped.setdefault(statements.get(metric, "operating"), []).append(row)
    sequence = ["income_statement", "balance_sheet", "cash_flow", "operating", "regulatory"]
    return {
        "fiscal_years": sorted(years), "currency": currency,
        "statements": [{"id": s, "label": STATEMENT_LABEL.get(s, s), "rows": grouped[s]}
                       for s in sequence if s in grouped],
    }


def analytics_of(ws: CompanyWorkspace) -> dict[str, Any]:
    rows = ws.parquet("historical_analytics.parquet", "analyze")
    summary = ws.json("historical_summary.json", "analyze")
    series: dict[str, dict[str, Any]] = {}
    for row in rows:
        entry = series.setdefault(row["analytic_id"], {
            "analytic_id": row["analytic_id"], "category": row["category"], "kind": row["kind"],
            "unit_kind": row["unit_kind"], "currency": row["currency"], "points": [],
        })
        entry["points"].append({
            "fiscal_year": row["fiscal_year"], "value": row["value"], "basis": row["basis"],
            "formula": row["formula"], "quality_flags": list(row["quality_flags"] or ()),
            "uses_derived_facts": row["uses_derived_facts"], "value_id": row["value_id"],
        })
    for entry in series.values():
        entry["points"].sort(key=lambda p: p["fiscal_year"])
        entry["summary"] = summary.get("summaries", {}).get(entry["analytic_id"])
    from ..presentation import gaps
    names, document_only = _names(ws)
    order = {a: i for i, a in enumerate(glossary_of(ws).get("analytic_order") or [])}
    for entry in series.values():
        entry["label"] = names.get(entry["analytic_id"], entry["analytic_id"])
    return {
        "fiscal_years": summary.get("fiscal_years", []),
        "classification": summary.get("classification"),
        "series": sorted(series.values(), key=lambda e: (e["category"], order.get(e["analytic_id"], 10_000),
                                                         e["analytic_id"])),
        "not_computed": summary.get("not_computed", {}),
        "gaps": gaps(summary.get("not_computed", {}), names, document_only),
    }


def forecast_of(ws: CompanyWorkspace) -> dict[str, Any]:
    rows = ws.parquet("forecast.parquet", "forecast")
    doc = ws.json("assumptions.json", "forecast")
    manifest = ws.json("forecast_manifest.json", "forecast")
    by_scenario: dict[str, dict[str, Any]] = {}
    for row in rows:
        scenario = by_scenario.setdefault(row["scenario"], {})
        metric = scenario.setdefault(row["metric_id"], {
            "metric_id": row["metric_id"], "unit_kind": row["unit_kind"],
            "currency": row["currency"], "points": [],
        })
        metric["points"].append({
            "fiscal_year": row["fiscal_year"], "value": row["value"], "method": row["method"],
            "formula": row["formula"], "assumption_ids": list(row["assumption_ids"] or ()),
            "assumption_types": list(row["assumption_types"] or ()), "value_id": row["value_id"],
            # .get: a forecast written before fallbacks existed has no such column, and "no
            # fallback recorded" is the truthful reading of it.
            "fallback_for": list(row.get("fallback_for") or ()),
        })
    for scenario in by_scenario.values():
        for metric in scenario.values():
            metric["points"].sort(key=lambda p: p["fiscal_year"])
    plan = {row["metric_id"]: row for row in doc.get("projection_plan", [])}
    return {
        "base_year": manifest.get("base_year"),
        "forecast_years": manifest.get("forecast_years") or [],
        "scenarios": [
            {**s, "metrics": sorted(by_scenario.get(s["id"], {}).values(),
                                    key=lambda m: plan_order(plan, m["metric_id"]))}
            for s in doc.get("scenarios", [])
        ],
        "projection_plan": doc.get("projection_plan", []),
        "not_projected": doc.get("not_projected", {}),
        "unseeded": doc.get("unseeded", {}),
        "demoted_derivations": doc.get("demoted_derivations", {}),
        "fallbacks": doc.get("fallbacks", {}),
        "unresolved_targets": doc.get("unresolved_targets", {}),
        "assumptions_file": manifest.get("assumptions_file"),
        **_forecast_words(ws, doc, by_scenario),
    }


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def _forecast_words(ws: CompanyWorkspace, doc: dict, by_scenario: dict) -> dict[str, Any]:
    """Labels and plain-language explanations for the forecast view."""
    from ..presentation import gaps, in_sentence, unseeded_text
    names, document_only = _names(ws)
    for scenario in by_scenario.values():
        for metric in scenario.values():
            metric["label"] = names.get(metric["metric_id"], metric["metric_id"])
    fallback_notes = []
    for metric, missing in (doc.get("fallback_inputs") or {}).items():
        inputs = [in_sentence(names.get(m, m)) for m in missing]
        where = ("aren't" if len(inputs) > 1 else "isn't") + (
            " disclosed in the SEC's structured financial data" if set(missing) & document_only else " reported")
        fallback_notes.append({"metric_id": metric, "label": names.get(metric, metric),
                               "text": f"{names.get(metric, metric)} is estimated from its own recent trend "
                                       f"because {_join(inputs)} {where}."})
    return {
        "refusals": gaps(doc.get("not_projected", {}), names, document_only),
        "unseeded_list": [{"key": k, "label": names.get(k, k), "text": unseeded_text(k, names, document_only)}
                          for k in sorted(doc.get("unseeded", {}))],
        "fallback_notes": fallback_notes,
    }


def plan_order(plan: dict[str, dict], metric_id: str) -> tuple[int, str]:
    """Evaluation order, so the UI lists a metric after the metrics that produce it."""
    ids = list(plan)
    return (ids.index(metric_id) if metric_id in ids else len(ids), metric_id)


def assumptions_of(ws: CompanyWorkspace) -> dict[str, Any]:
    doc = ws.json("assumptions.json", "forecast")
    names, _ = _names(ws)
    source_text = {"historical": "Company history", "analyst_assumption": "Analyst",
                   "management_guidance": "Management guidance", "consensus": "Consensus",
                   "scenario": "Scenario", "derived": "Derived"}
    assumptions = []
    for a in doc.get("assumptions", []):
        span = re.search(r"over (FY\d{4})(?:-(FY\d{4}))?", a.get("description") or "")
        if a.get("type") == "historical" and span:
            first, last = span.group(1), span.group(2)
            basis = f"Median, {first}–{last}" if last and last != first else f"{first} only"
        else:
            basis = a.get("rationale") or a.get("description") or ""
        assumptions.append({**a, "label": names.get(a["assumption_id"], a["assumption_id"]),
                            "basis_text": basis, "source_text": source_text.get(a.get("type"), a.get("type"))})
    return {
        "resolution_order": doc.get("resolution_order", []),
        "assumptions": assumptions,
        "scenarios": doc.get("scenarios", []),
        "unseeded": doc.get("unseeded", {}),
        "base_year": doc.get("base_year"),
        "forecast_years": doc.get("forecast_years", []),
    }


def quality_of(ws: CompanyWorkspace) -> dict[str, Any]:
    return ws.json("data_quality_report.json", "quality")


def checks_of(ws: CompanyWorkspace) -> dict[str, Any]:
    """The data-quality report in words; `quality_of` stays verbatim for anyone auditing it."""
    from ..presentation import checks_view
    names, _ = _names(ws)
    units = glossary_of(ws).get("units") or {}
    return checks_view(quality_of(ws), names, ws.config.company.reporting_currency, units)


def chart_data(ws: CompanyWorkspace, chart_id: str) -> dict[str, Any]:
    """A chart's numbers as a table.

    The chart guidance this dashboard follows requires every chart to have a non-visual
    equivalent, and a picture of a line is useless to a screen reader. The chart record stores the
    lineage id behind each plotted point, so the table is a join rather than a second computation.

    Each value is placed under *its own* fiscal year, read from the row it came from. Pairing the
    chart's year axis with a series' id list positionally is wrong whenever a series has fewer
    points than the chart spans, which is common: the shorter series then slides left and every
    value lands under the wrong year. That misalignment is invisible in the picture, so it would
    be served only to the readers who depend on the table instead of the picture.
    """
    record = next((c for c in ws.chart_index() if c["chart_id"] == chart_id), None)
    if record is None:
        raise UnknownChart(chart_id)
    values: dict[str, tuple[float, str | None]] = {}
    year_of: dict[str, int] = {}
    for fact in ws.parquet("historical_financials.parquet", "analyze"):
        values[fact["fact_id"]] = (fact["value"], fact["currency"])
        year_of[fact["fact_id"]] = fact["fiscal_year"]
    unit_kinds: dict[str, str] = {}
    for analytic in ws.parquet("historical_analytics.parquet", "analyze"):
        values[analytic["value_id"]] = (analytic["value"], analytic["currency"])
        year_of[analytic["value_id"]] = analytic["fiscal_year"]
        unit_kinds[analytic["analytic_id"]] = analytic["unit_kind"]

    years = record.get("years") or []
    rows = []
    for series_id, lineage_ids in (record.get("series") or {}).items():
        row: dict[str, Any] = {"series": series_id}
        row.update({f"FY{y}": None for y in years})
        for lineage_id in lineage_ids:
            year = year_of.get(lineage_id)
            found = values.get(lineage_id)
            if year is not None and found is not None and year in years:
                row[f"FY{year}"] = found[0]
        rows.append(row)
    currency = next((c for v, c in values.values() if c), None)
    # Analytics declare a unit kind; plain metric series do not, so infer from the currency.
    declared = next((unit_kinds[s] for s in (record.get("series") or {}) if s in unit_kinds), None)
    return {
        "chart_id": chart_id, "title": record["title"],
        "unit_kind": declared or ("currency" if currency else "number"),
        "currency": currency,
        "columns": ["series", *[f"FY{y}" for y in years]],
        "rows": rows,
        "notes": list(record.get("notes") or []),
        "truncated": dict(record.get("truncated") or {}),
    }


def forecast_chart_data(ws: CompanyWorkspace, chart_id: str) -> dict[str, Any]:
    """A forecast chart's numbers as a table, one row per scenario."""
    record = next((c for c in ws.forecast_chart_index() if c["chart_id"] == chart_id), None)
    if record is None:
        raise UnknownChart(chart_id)
    years = record.get("years") or []
    columns = [f"FY{y}" for y in years]
    table = record.get("table") or {}
    rows = [{"series": scenario, **{col: table.get(scenario, {}).get(col) for col in columns},
             "method": (record.get("methods") or {}).get(scenario, "")}
            for scenario in record.get("scenarios", [])]
    return {
        "chart_id": chart_id, "title": record["title"], "unit_kind": record.get("unit_kind"),
        "currency": record.get("currency"), "columns": ["series", *columns, "method"], "rows": rows,
    }


def coverage_of(ws: CompanyWorkspace) -> dict[str, Any]:
    """The annual coverage matrix: which metric-years are reported, derived or absent."""
    report = ws.json("data_quality_report.json", "quality")
    return {"company_id": report.get("company_id"), "framework": report.get("framework"),
            "coverage": report.get("coverage"), "derivations": report.get("derivations")}


# ---- lineage -------------------------------------------------------------------------------

@lru_cache(maxsize=32)
def _lineage_cached(path: str, mtime: float) -> tuple[dict, dict]:
    graph = json.loads(Path(path).read_text(encoding="utf-8"))
    nodes = {n["id"]: n for n in graph["nodes"]}
    parents: dict[str, list[str]] = {}
    for edge in graph["edges"]:
        parents.setdefault(edge["child"], []).append(edge["parent"])
    return nodes, parents


def _lineage(ws: CompanyWorkspace) -> tuple[dict, dict]:
    stage = "forecast" if ws.has("forecast") else "analyze" if ws.has("analyze") else "ingest"
    ws.require(stage)
    path = ws.output / "lineage.json"
    if not path.is_file():
        raise StageNotRun(ws.company_id, stage)
    return _lineage_cached(str(path), path.stat().st_mtime)


def trace(ws: CompanyWorkspace, node_id: str, max_nodes: int = 400) -> dict[str, Any]:
    """Every path from a value up to its roots: the chain a reviewer follows to a filed document."""
    nodes, parents = _lineage(ws)
    if node_id not in nodes:
        raise UnknownNode(node_id)
    seen: dict[str, dict] = {}
    edges: list[dict] = []
    stack = [node_id]
    truncated = False
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        if len(seen) >= max_nodes:
            truncated = True
            break
        seen[current] = nodes[current]
        for parent in parents.get(current, []):
            if parent in nodes:
                edges.append({"child": current, "parent": parent})
                stack.append(parent)
    roots = sorted({n["id"] for n in seen.values() if not parents.get(n["id"])})
    sources = sorted({nodes[r]["label"] for r in roots if nodes[r]["kind"] == "source"})
    return {
        "node_id": node_id,
        "nodes": list(seen.values()),
        "edges": edges,
        "roots": roots,
        "source_urls": sources,
        "truncated": truncated,
        "depth": _depth(node_id, parents),
    }


def _depth(node_id: str, parents: dict[str, list[str]], seen: set | None = None) -> int:
    seen = seen if seen is not None else set()
    if node_id in seen:
        return 0
    seen.add(node_id)
    ups = parents.get(node_id) or []
    return 1 + max((_depth(p, parents, seen) for p in ups), default=-1)


class UnknownNode(ResearchEngineError):
    def __init__(self, node_id: str):
        super().__init__(f"no lineage node {node_id!r} for this company")
