"""Read-only access to what the pipeline already wrote.

The API computes nothing. Every number it serves was produced by `ingest`, `quality`, `analyze`
or `forecast` and written to a company's `output/` directory, so the dashboard cannot disagree
with the files an analyst would read directly. When a stage has not been run, that is reported
as a missing stage rather than as an empty result: a dashboard showing zero issues because the
quality stage never ran would be worse than one showing nothing at all.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterator, Optional

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
        rendered = {c["chart_id"] for c in self.chart_index()}
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
    return {
        "fiscal_years": summary.get("fiscal_years", []),
        "classification": summary.get("classification"),
        "series": sorted(series.values(), key=lambda e: (e["category"], e["analytic_id"])),
        "not_computed": summary.get("not_computed", {}),
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
        "unresolved_targets": doc.get("unresolved_targets", {}),
        "assumptions_file": manifest.get("assumptions_file"),
    }


def plan_order(plan: dict[str, dict], metric_id: str) -> tuple[int, str]:
    """Evaluation order, so the UI lists a metric after the metrics that produce it."""
    ids = list(plan)
    return (ids.index(metric_id) if metric_id in ids else len(ids), metric_id)


def assumptions_of(ws: CompanyWorkspace) -> dict[str, Any]:
    doc = ws.json("assumptions.json", "forecast")
    return {
        "resolution_order": doc.get("resolution_order", []),
        "assumptions": doc.get("assumptions", []),
        "scenarios": doc.get("scenarios", []),
        "unseeded": doc.get("unseeded", {}),
        "base_year": doc.get("base_year"),
        "forecast_years": doc.get("forecast_years", []),
    }


def quality_of(ws: CompanyWorkspace) -> dict[str, Any]:
    return ws.json("data_quality_report.json", "quality")


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


def _depth(node_id: str, parents: dict[str, list[str]], seen: Optional[set] = None) -> int:
    seen = seen if seen is not None else set()
    if node_id in seen:
        return 0
    seen.add(node_id)
    ups = parents.get(node_id) or []
    return 1 + max((_depth(p, parents, seen) for p in ups), default=-1)


class UnknownNode(ResearchEngineError):
    def __init__(self, node_id: str):
        super().__init__(f"no lineage node {node_id!r} for this company")
