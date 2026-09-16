"""Phase 5 stage: driver-graph forecast over scenarios, from Phase 4 analytics and an assumption registry."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from ..errors import ConfigError, ExtractionError
from ..extraction import current_facts
from ..forecast import load_assumptions_file, run_forecast
from ..forecast.engine import ForecastResult
from ..forecast.report import render_markdown
from ..frameworks import FrameworkRegistry, framework_fingerprint
from ..lineage import LineageGraph, LineageNode, NodeKind
from ..registry import DocumentRegistry
from ..schemas.analytics import AnalyticValue
from ..schemas.company import ProjectConfig
from ..schemas.financial import Provenance
from ..schemas.forecast import ForecastValue
from ..versioning import version_stamp
from .analysis import _require, _sha, _write_parquet
from .outputs import _atomic_write, _csv_text
from .quality import _load_facts

ASSUMPTIONS_FILENAME = "assumptions.yaml"


@dataclass
class ForecastStageResult:
    output_dir: Path
    forecast: ForecastResult
    assumptions_path: Path
    assumptions_file_present: bool


def _forecast_schema():
    import pyarrow as pa
    strings = pa.list_(pa.string())
    return pa.schema([
        ("company_id", pa.string()), ("scenario", pa.string()), ("metric_id", pa.string()),
        ("fiscal_year", pa.int32()), ("value", pa.float64()), ("value_exact", pa.string()),
        ("unit_kind", pa.string()), ("currency", pa.string()), ("method", pa.string()), ("formula", pa.string()),
        ("assumption_ids", strings), ("assumption_types", strings), ("value_id", pa.string()),
        ("input_value_ids", strings), ("input_fact_ids", strings),
    ])


def _row(v: ForecastValue) -> dict:
    return {
        "company_id": v.company_id, "scenario": v.scenario, "metric_id": v.metric_id,
        "fiscal_year": v.fiscal_year, "value": float(v.value), "value_exact": str(v.value),
        "unit_kind": v.unit_kind, "currency": v.currency, "method": v.method, "formula": v.formula,
        "assumption_ids": list(v.assumption_ids), "assumption_types": list(v.assumption_types),
        "value_id": v.value_id, "input_value_ids": list(v.input_value_ids),
        "input_fact_ids": list(v.input_fact_ids),
    }


def _load_analytics(path: Path) -> list[AnalyticValue]:
    """Read back exactly what `analyze` produced, rather than recomputing it here."""
    try:
        import pyarrow.parquet as pq
    except ImportError:
        raise ConfigError("pyarrow is required to read Phase 4 analytics: pip install pyarrow") from None
    values = []
    for row in pq.read_table(path).to_pylist():
        values.append(AnalyticValue(
            value_id=row["value_id"], company_id=row["company_id"], analytic_id=row["analytic_id"],
            category=row["category"], kind=row["kind"], unit_kind=row["unit_kind"],
            fiscal_year=row["fiscal_year"], value=Decimal(row["value_exact"]), currency=row["currency"],
            formula=row["formula"], basis=row["basis"], input_fact_ids=tuple(row["input_fact_ids"] or ()),
            input_value_ids=tuple(row["input_value_ids"] or ()), quality_flags=tuple(row["quality_flags"] or ()),
            uses_derived_facts=row["uses_derived_facts"],
        ))
    return values


def run_forecast_stage(config: ProjectConfig, *, workspace: Path,
                       frameworks: FrameworkRegistry) -> ForecastStageResult:
    workspace = Path(workspace)
    out = workspace / "output"
    manifest_path = _require(out / "manifest.json", "run `ingest` first")
    quality_manifest_path = _require(out / "quality_manifest.json", "run `quality` first")
    analysis_manifest_path = _require(out / "analysis_manifest.json", "run `analyze` first")
    manifest = json.loads(manifest_path.read_text())
    analysis_manifest = json.loads(analysis_manifest_path.read_text())

    if manifest.get("config_sha256") != hashlib.sha256(config.model_dump_json().encode()).hexdigest():
        raise ConfigError("the company config changed since the last ingestion; re-run `ingest`, `quality`, `analyze`")
    framework = frameworks.get(manifest["framework"]["name"])
    fingerprint = framework_fingerprint(framework)
    if manifest.get("framework_sha256") != fingerprint:
        raise ConfigError(f"industry framework {framework.name!r} changed since the last ingestion; re-run `ingest`")
    if analysis_manifest.get("quality_manifest_sha256") != _sha(quality_manifest_path):
        raise ConfigError("historical analysis is older than the latest data-quality run; re-run `analyze`")

    reported = _load_facts(out / "facts.jsonl")
    derived = _load_facts(_require(out / "facts_derived.jsonl", "run `quality` first"))
    facts = current_facts([f for f in reported if f.provenance is Provenance.REPORTED]) + derived
    analytics = _load_analytics(_require(out / "historical_analytics.parquet", "run `analyze` first"))

    assumptions_path = workspace / ASSUMPTIONS_FILENAME
    analyst, scenarios = load_assumptions_file(assumptions_path, company_id=config.company_id)

    result = run_forecast(framework, company_id=config.company_id, facts=facts, analytics=analytics,
                          analyst_assumptions=analyst, scenarios=scenarios,
                          forecast_years=config.research.forecast_years)

    # Lineage: forecast value -> prior forecast value -> base-year fact -> document -> source,
    # with each exogenous input attached to the assumption that supplied it.
    registry = DocumentRegistry(workspace / "data" / "registry.sqlite", workspace / "data" / "raw")
    documents = registry.list_documents(company_id=config.company_id, include_superseded=True)
    lineage = LineageGraph.from_records(documents, [*reported, *derived])
    for a in result.assumptions.all:
        node = f"assumption:{a.assumption_id}:{a.type.value}"
        lineage.add_node(LineageNode(node, NodeKind.ASSUMPTION, a.assumption_id,
                                     {"type": a.type.value, "unit": a.unit}))
        for fid in a.source_fact_ids:
            lineage.link(node, fid)
    for v in result.values:
        lineage.add_node(LineageNode(v.value_id, NodeKind.MODEL_OUTPUT, f"{v.scenario}:{v.metric_id}",
                                     {"fiscal_year": str(v.fiscal_year), "value": str(v.value),
                                      "method": v.method, "formula": v.formula}))
    for v in result.values:
        for parent in (*v.input_value_ids, *v.input_fact_ids):
            lineage.link(v.value_id, parent)
        for aid, atype in zip(v.assumption_ids, v.assumption_types):
            lineage.link(v.value_id, f"assumption:{aid}:{atype}")
    broken = lineage.broken_nodes()
    if broken:
        raise ExtractionError(f"forecast lineage check failed for {len(broken)} nodes, e.g. {broken[0].node_id}")

    versions = version_stamp()
    years = list(result.forecast_years)
    _write_parquet(out / "forecast.parquet", [_row(v) for v in result.values], _forecast_schema())

    places = Decimal(1).scaleb(-10)
    rows = []
    for scenario, series in result.by_scenario.items():
        for metric_id in sorted(series):
            row = series[metric_id]
            base = row.get(result.base_year)
            rule = result.graph.rule(metric_id)
            rows.append([scenario, metric_id, rule.method,
                         format(base.value.quantize(places).normalize(), "f") if base else ""]
                        + [format(row[y].value.quantize(places).normalize(), "f") if y in row else "" for y in years])
    _atomic_write(out / "forecast.csv", _csv_text(
        ["scenario", "metric_id", "method", f"FY{result.base_year}"] + [f"FY{y}" for y in years], rows))

    _atomic_write(out / "assumptions.json", json.dumps({
        "company_id": config.company_id, "framework": framework.name,
        "base_year": result.base_year, "forecast_years": years,
        "resolution_order": ["scenario", "analyst_assumption", "management_guidance", "consensus", "historical"],
        "scenarios": [s.model_dump() for s in result.assumptions.scenarios.values()],
        "assumptions": [a.model_dump() for a in sorted(result.assumptions.all,
                                                       key=lambda a: (a.assumption_id, a.type.value))],
        "unseeded": dict(sorted(result.unseeded.items())),
        "projection_plan": [
            {"metric_id": m, "method": result.graph.rule(m).method, "formula": result.graph.rule(m).formula,
             "driver_id": result.graph.rule(m).driver_id, "reason": result.graph.rule(m).reason,
             "metric_inputs": list(result.graph.rule(m).metric_inputs),
             "assumption_keys": list(result.graph.rule(m).assumption_keys)}
            for m in result.graph.order
        ],
        "not_projected": dict(sorted(result.not_projected.items())),
        "unresolved_targets": dict(sorted(result.graph.unresolved.items())),
        "demoted_derivations": dict(sorted(result.graph.demoted.items())),
    }, indent=2, default=str))

    _atomic_write(out / "forecast.md", render_markdown(company_id=config.company_id, framework=framework,
                                                       result=result, versions=versions))
    _atomic_write(out / "lineage.json", json.dumps(lineage.to_dict(), indent=1, default=str))
    _atomic_write(out / "forecast_manifest.json", json.dumps({
        **versions, "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "framework": framework.name, "framework_sha256": fingerprint,
        "analysis_manifest_sha256": _sha(analysis_manifest_path),
        "assumptions_file": ASSUMPTIONS_FILENAME if assumptions_path.is_file() else None,
        "assumptions_sha256": _sha(assumptions_path) if assumptions_path.is_file() else None,
        "base_year": result.base_year, "forecast_years": years,
        "scenarios": list(result.by_scenario),
        "assumptions_seeded": len(result.seeded), "assumptions_analyst": len(analyst),
        "assumptions_unseeded": len(result.unseeded),
        "forecast_values": len(result.values),
        "not_projected": sum(result.not_projected.values()),
    }, indent=2))
    return ForecastStageResult(out, result, assumptions_path, assumptions_path.is_file())
