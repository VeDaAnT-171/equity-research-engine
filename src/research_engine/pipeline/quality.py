"""Phase 3 stage: load ingestion outputs, derive facts, run checks, write the quality report."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..errors import ConfigError, ExtractionError
from ..frameworks import FrameworkRegistry, framework_fingerprint
from ..lineage import LineageGraph
from ..quality import QualityReport, run_quality
from ..quality.html import render_html
from ..registry import DocumentRegistry
from ..schemas.company import ProjectConfig
from ..schemas.financial import FinancialFact, FiscalPeriodCode
from ..versioning import version_stamp
from .outputs import _INTERIM, _atomic_write, _csv_text, _wide, display_value


@dataclass
class QualityStageResult:
    output_dir: Path
    current: list[FinancialFact]
    derived: list[FinancialFact]
    report: QualityReport


def _load_facts(path: Path) -> list[FinancialFact]:
    if not path.is_file():
        raise ConfigError(f"{path} not found: run `ingest` first")
    facts = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            try:
                facts.append(FinancialFact.model_validate_json(line))
            except ValueError as exc:
                raise ExtractionError(f"{path}:{lineno}: invalid fact record ({exc})") from None
    return facts


def run_quality_stage(config: ProjectConfig, *, workspace: Path, frameworks: FrameworkRegistry) -> QualityStageResult:
    out = Path(workspace) / "output"
    manifest_path = out / "manifest.json"
    if not manifest_path.is_file():
        raise ConfigError(f"{manifest_path} not found: run `ingest` first")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    config_hash = hashlib.sha256(config.model_dump_json().encode()).hexdigest()
    if manifest.get("config_sha256") != config_hash:
        raise ConfigError("the company config changed since the last ingestion; re-run `ingest` before `quality`")
    if not manifest.get("framework"):
        raise ConfigError("ingestion manifest has no framework selection; re-run `ingest`")

    framework = frameworks.get(manifest["framework"]["name"])
    fingerprint = framework_fingerprint(framework)
    if manifest.get("framework_sha256") != fingerprint:
        raise ConfigError(f"industry framework {framework.name!r} changed since the last ingestion; re-run `ingest`")
    facts = _load_facts(out / "facts.jsonl")
    extraction_path = out / "extraction_report.json"
    extraction = json.loads(extraction_path.read_text(encoding="utf-8")) if extraction_path.is_file() else None

    current, derived, report = run_quality(facts, framework=framework, company_id=config.company_id,
                                           historical_years=config.research.historical_years,
                                           extraction_report=extraction)

    registry = DocumentRegistry(Path(workspace) / "data" / "registry.sqlite", Path(workspace) / "data" / "raw")
    documents = registry.list_documents(company_id=config.company_id, include_superseded=True)
    lineage = LineageGraph.from_records(documents, [*facts, *derived])
    broken = lineage.broken_nodes()
    if broken:
        raise ExtractionError(f"lineage check failed for {len(broken)} nodes, e.g. {broken[0].node_id}")

    combined = sorted([*current, *derived], key=lambda f: (f.metric_id, f.period.end, f.period.start or f.period.end))
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _atomic_write(out / "facts_derived.jsonl", "".join(f.model_dump_json() + "\n" for f in derived))
    _atomic_write(out / "financials_long.csv", _csv_text(
        ["metric_id", "period", "period_type", "start", "end", "value", "unit", "provenance", "xbrl_concept_or_formula",
         "accession", "filed", "fact_id", "input_fact_ids"],
        [[f.metric_id, f.period.label, f.period.period_type.value, f.period.start or "", f.period.end, display_value(f), f.unit,
          f.provenance.value, f.source.xbrl_concept if f.source else f.formula,
          f.source.filing_accession if f.source else "", f.source.filed_date if f.source else "", f.fact_id,
          " ".join(f.inputs)] for f in combined],
    ))
    _atomic_write(out / "financials_annual.csv", _wide([f for f in combined if f.period.fiscal_period is FiscalPeriodCode.FY], {FiscalPeriodCode.FY}))
    _atomic_write(out / "financials_interim.csv", _wide(combined, _INTERIM))
    _atomic_write(out / "lineage.json", json.dumps(lineage.to_dict(), indent=1, default=str))
    _atomic_write(out / "data_quality_report.json", json.dumps(report.to_dict(), indent=2, default=str))
    _atomic_write(out / "data_quality_report.html", render_html(report, generated_at=generated_at, versions=version_stamp()))
    _atomic_write(out / "quality_manifest.json", json.dumps({
        **version_stamp(),
        "generated_at": generated_at,
        "ingestion_manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "framework": framework.name,
        "framework_sha256": fingerprint,
        "facts_current_reported": len(current),
        "facts_derived": len(derived),
        "issue_counts": report.counts(),
    }, indent=2))
    return QualityStageResult(out, current, derived, report)
