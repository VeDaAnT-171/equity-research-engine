"""Phase 4 stage: historical analytics, summaries, charts and Parquet datasets from verified facts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from ..analysis import AnalysisResult, run_analytics, summarize_all
from ..analysis.charts import ChartRecord, render_charts
from ..analysis.report import render_markdown
from ..errors import ConfigError, ExtractionError
from ..extraction import current_facts
from ..frameworks import FrameworkRegistry, framework_fingerprint
from ..lineage import LineageGraph, LineageNode, NodeKind
from ..presentation import glossary
from ..registry import DocumentRegistry
from ..schemas.analytics import AnalyticValue
from ..schemas.company import ProjectConfig
from ..schemas.financial import FinancialFact, FiscalPeriodCode, Provenance
from ..versioning import version_stamp
from .outputs import _atomic_write, _csv_text
from .quality import _load_facts


@dataclass
class AnalysisStageResult:
    output_dir: Path
    analysis: AnalysisResult
    summaries: dict
    charts: list[ChartRecord]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _require(path: Path, hint: str) -> Path:
    if not path.is_file():
        raise ConfigError(f"{path} not found: {hint}")
    return path


def _write_parquet(path: Path, rows: list[dict], schema) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".parquet.tmp")
    pq.write_table(pa.Table.from_pylist(rows, schema=schema), tmp)
    tmp.replace(path)


def _parquet_schemas():
    try:
        import pyarrow as pa
    except ImportError:
        raise ConfigError("pyarrow is required for Parquet outputs: pip install pyarrow") from None
    strings = pa.list_(pa.string())
    facts = pa.schema([
        ("company_id", pa.string()), ("metric_id", pa.string()), ("fiscal_year", pa.int32()), ("fiscal_period", pa.string()),
        ("period_type", pa.string()), ("period_start", pa.date32()), ("period_end", pa.date32()),
        ("value", pa.float64()), ("value_exact", pa.string()), ("unit", pa.string()), ("currency", pa.string()),
        ("provenance", pa.string()), ("xbrl_concept", pa.string()), ("formula", pa.string()),
        ("filing_accession", pa.string()), ("filing_form", pa.string()), ("filed_date", pa.date32()),
        ("document_id", pa.string()), ("fact_id", pa.string()), ("input_fact_ids", strings),
    ])
    analytics = pa.schema([
        ("company_id", pa.string()), ("analytic_id", pa.string()), ("category", pa.string()), ("kind", pa.string()),
        ("unit_kind", pa.string()), ("fiscal_year", pa.int32()), ("value", pa.float64()), ("value_exact", pa.string()),
        ("currency", pa.string()), ("basis", pa.string()), ("formula", pa.string()), ("quality_flags", strings),
        ("uses_derived_facts", pa.bool_()), ("value_id", pa.string()), ("input_fact_ids", strings), ("input_value_ids", strings),
    ])
    return facts, analytics


def _fact_row(f: FinancialFact) -> dict:
    s = f.source
    return {
        "company_id": f.company_id, "metric_id": f.metric_id, "fiscal_year": f.period.fiscal_year,
        "fiscal_period": f.period.fiscal_period.value, "period_type": f.period.period_type.value,
        "period_start": f.period.start, "period_end": f.period.end, "value": float(f.value), "value_exact": str(f.value),
        "unit": f.unit, "currency": f.currency, "provenance": f.provenance.value,
        "xbrl_concept": s.xbrl_concept if s else None, "formula": f.formula,
        "filing_accession": s.filing_accession if s else None, "filing_form": s.filing_form if s else None,
        "filed_date": s.filed_date if s else None, "document_id": s.document_id if s else None,
        "fact_id": f.fact_id, "input_fact_ids": list(f.inputs),
    }


def _analytic_row(v: AnalyticValue) -> dict:
    return {
        "company_id": v.company_id, "analytic_id": v.analytic_id, "category": v.category, "kind": v.kind,
        "unit_kind": v.unit_kind, "fiscal_year": v.fiscal_year, "value": float(v.value), "value_exact": str(v.value),
        "currency": v.currency, "basis": v.basis, "formula": v.formula, "quality_flags": list(v.quality_flags),
        "uses_derived_facts": v.uses_derived_facts, "value_id": v.value_id, "input_fact_ids": list(v.input_fact_ids),
        "input_value_ids": list(v.input_value_ids),
    }


def run_analysis_stage(config: ProjectConfig, *, workspace: Path, frameworks: FrameworkRegistry) -> AnalysisStageResult:
    workspace = Path(workspace)
    out = workspace / "output"
    manifest_path = _require(out / "manifest.json", "run `ingest` first")
    quality_manifest_path = _require(out / "quality_manifest.json", "run `quality` first")
    manifest = json.loads(manifest_path.read_text())
    quality_manifest = json.loads(quality_manifest_path.read_text())
    if manifest.get("config_sha256") != hashlib.sha256(config.model_dump_json().encode()).hexdigest():
        raise ConfigError("the company config changed since the last ingestion; re-run `ingest`, `quality`, `analyze`")
    framework = frameworks.get(manifest["framework"]["name"])
    fingerprint = framework_fingerprint(framework)
    if manifest.get("framework_sha256") != fingerprint:
        raise ConfigError(f"industry framework {framework.name!r} changed since the last ingestion; re-run `ingest`")
    if quality_manifest.get("ingestion_manifest_sha256") != _sha(manifest_path):
        raise ConfigError("data-quality outputs are older than the latest ingestion; re-run `quality`")
    fact_schema, analytic_schema = _parquet_schemas()

    reported = _load_facts(out / "facts.jsonl")
    derived = _load_facts(_require(out / "facts_derived.jsonl", "run `quality` first"))
    quality_report = json.loads(_require(out / "data_quality_report.json", "run `quality` first").read_text())
    current = current_facts([f for f in reported if f.provenance is Provenance.REPORTED]) + derived

    result = run_analytics(framework, current, company_id=config.company_id, quality_report=quality_report)
    metric_series: dict[str, list[tuple[int, float]]] = {}
    for f in sorted(current, key=lambda f: f.period.fiscal_year):
        if f.period.fiscal_period is FiscalPeriodCode.FY:
            metric_series.setdefault(f.metric_id, []).append((f.period.fiscal_year, float(f.value)))
    summaries = summarize_all(framework, result.by_analytic, metric_series)

    flagged = {fid for i in quality_report.get("issues", []) if i["severity"] == "warning" for fid in i.get("fact_ids", [])}
    versions = version_stamp()
    label = f"{config.company.name} ({config.company.ticker})"
    charts = render_charts(framework, current, result.by_analytic, flagged, out / "charts",
                           company_label=label, engine_version=versions["engine_version"],
                           coverage_years=result.fiscal_years, not_computed=result.not_computed)

    # Lineage: chart -> analytic value -> fact -> document -> source
    registry = DocumentRegistry(workspace / "data" / "registry.sqlite", workspace / "data" / "raw")
    documents = registry.list_documents(company_id=config.company_id, include_superseded=True)
    lineage = LineageGraph.from_records(documents, [*reported, *derived])
    for v in result.values:
        lineage.add_node(LineageNode(v.value_id, NodeKind.MODEL_OUTPUT, v.analytic_id,
                                     {"fiscal_year": str(v.fiscal_year), "value": str(v.value), "formula": v.formula}))
    for v in result.values:
        for parent in (*v.input_fact_ids, *v.input_value_ids):
            lineage.link(v.value_id, parent)
    for c in charts:
        if c.skipped:
            continue
        node = f"chart:{c.chart_id}"
        lineage.add_node(LineageNode(node, NodeKind.CHART, c.title, {"files": ",".join(c.files)}))
        for ids in c.series.values():
            for parent in ids:
                lineage.link(node, parent)
    broken = lineage.broken_nodes()
    if broken:
        raise ExtractionError(f"lineage check failed for {len(broken)} nodes, e.g. {broken[0].node_id}")

    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _write_parquet(out / "historical_financials.parquet", [_fact_row(f) for f in sorted(
        current, key=lambda f: (f.metric_id, f.period.end, f.period.start or f.period.end))], fact_schema)
    _write_parquet(out / "historical_analytics.parquet", [_analytic_row(v) for v in result.values], analytic_schema)

    years = result.fiscal_years
    places = Decimal(1).scaleb(-10)
    wide_rows = []
    for spec in framework.analytics:
        series = result.by_analytic.get(spec.id, {})
        if series:
            wide_rows.append([spec.id, spec.category, spec.unit_kind] + [
                format(series[y].value.quantize(places).normalize(), "f") if y in series else "" for y in years])
    _atomic_write(out / "historical_analytics.csv", _csv_text(["analytic_id", "category", "unit_kind"] + [f"FY{y}" for y in years], wide_rows))
    currencies = {aid: next((v.currency for v in s.values() if v.currency), None) for aid, s in result.by_analytic.items()}
    chart_dicts = [c.to_dict() for c in charts]
    _atomic_write(out / "historical_summary.json", json.dumps({
        "company_id": config.company_id, "framework": framework.name, "fiscal_years": years,
        "classification": "model_output", "summaries": summaries, "not_computed": dict(sorted(result.not_computed.items())),
        "charts": chart_dicts,
    }, indent=2, default=str))
    _atomic_write(out / "charts" / "index.json", json.dumps(chart_dicts, indent=2))
    # Names and plain-language vocabulary for readers, written from the same framework as the
    # figures so a label can never describe a different definition than the number beside it.
    _atomic_write(out / "glossary.json", json.dumps(glossary(framework), indent=2))
    _atomic_write(out / "historical_analysis.md", render_markdown(
        company_id=config.company_id, framework=framework, fiscal_years=years, summaries=summaries,
        not_computed=dict(result.not_computed), charts=chart_dicts, currencies=currencies, versions=versions))
    _atomic_write(out / "lineage.json", json.dumps(lineage.to_dict(), indent=1, default=str))
    _atomic_write(out / "analysis_manifest.json", json.dumps({
        **versions, "generated_at": generated_at, "framework": framework.name, "framework_sha256": fingerprint,
        "ingestion_manifest_sha256": _sha(manifest_path), "quality_manifest_sha256": _sha(quality_manifest_path),
        "analytic_values": len(result.values), "charts_rendered": sum(1 for c in charts if not c.skipped),
        "not_computed": sum(result.not_computed.values()),
    }, indent=2))
    return AnalysisStageResult(out, result, summaries, charts)
