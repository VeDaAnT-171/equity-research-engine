"""Writes Phase 2 artifacts. JSONL keeps full lineage; CSVs are human-readable views."""

from __future__ import annotations

import csv
import hashlib
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from ..extraction import current_facts
from ..lineage import LineageGraph
from ..schemas.company import ProjectConfig
from ..schemas.document import DocumentRecord
from ..schemas.financial import FiscalPeriodCode, PeriodType
from ..versioning import version_stamp

if TYPE_CHECKING:
    from .ingest import IngestionResult

_INTERIM = {FiscalPeriodCode.Q1, FiscalPeriodCode.Q2, FiscalPeriodCode.Q3, FiscalPeriodCode.Q4, FiscalPeriodCode.H1, FiscalPeriodCode.H2}


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _csv_text(header: list[str], rows: list[list]) -> str:
    import io
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    return buf.getvalue()


def _wide(facts, codes) -> str:
    selected = [f for f in facts if f.period.fiscal_period in codes]
    periods = sorted({(f.period.fiscal_year, f.period.fiscal_period.value) for f in selected})
    labels = [f"FY{y}" if c == "FY" else f"{c}-{y}" for y, c in periods]
    table: dict[str, dict[str, str]] = defaultdict(dict)
    units: dict[str, str] = {}
    for f in selected:
        table[f.metric_id][f.period.label] = str(f.value)
        units[f.metric_id] = f.unit
    rows = [[m, units[m]] + [table[m].get(label, "") for label in labels] for m in sorted(table)]
    return _csv_text(["metric_id", "unit"] + labels, rows)


def write_ingestion_outputs(result: "IngestionResult", config: ProjectConfig, documents: list[DocumentRecord],
                            lineage: LineageGraph) -> None:
    out = result.output_dir
    facts_sorted = sorted(result.facts, key=lambda f: (f.metric_id, f.period.end, f.fact_id))
    current = current_facts(result.facts)
    annual = [f for f in current if f.period.fiscal_period is FiscalPeriodCode.FY]

    _atomic_write(out / "facts.jsonl", "".join(f.model_dump_json() + "\n" for f in facts_sorted))
    _atomic_write(out / "facts_current.csv", _csv_text(
        ["metric_id", "period", "period_type", "start", "end", "value", "unit", "xbrl_concept", "accession", "form",
         "filed", "fact_id", "document_id"],
        [[f.metric_id, f.period.label, f.period.period_type.value, f.period.start or "", f.period.end, f.value, f.unit,
          f.source.xbrl_concept, f.source.filing_accession, f.source.filing_form, f.source.filed_date, f.fact_id,
          f.source.document_id] for f in current],
    ))
    _atomic_write(out / "historical_annual.csv", _wide(annual, {FiscalPeriodCode.FY}))
    _atomic_write(out / "historical_interim.csv", _wide(current, _INTERIM))
    _atomic_write(out / "lineage.json", json.dumps(lineage.to_dict(), indent=1, default=str))

    report = result.extraction.to_dict() if result.extraction else None
    _atomic_write(out / "extraction_report.json", json.dumps(report, indent=2, default=str))
    _atomic_write(out / "extraction_report.md", _report_md(result, report, annual))
    _atomic_write(out / "sources.md", _sources_md(documents))

    config_hash = hashlib.sha256(config.model_dump_json().encode()).hexdigest()
    manifest = {
        **version_stamp(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "company_id": result.company_id,
        "config_sha256": config_hash,
        "framework": result.framework.__dict__ if result.framework else None,
        "fiscal_calendar": result.calendar.__dict__ if result.calendar else None,
        "documents": [{"document_id": d.document_id, "status": d.status.value, "file_hash": d.file_hash,
                       "source": d.source_url or d.local_source_path} for d in documents],
        "facts": len(result.facts),
        "facts_current": len(current),
        "warnings": result.warnings,
    }
    _atomic_write(out / "manifest.json", json.dumps(manifest, indent=2, default=str))


def _report_md(result: "IngestionResult", report: dict | None, annual) -> str:
    lines = [f"# Extraction report: {result.company_id}", ""]
    if result.profile:
        p = result.profile
        lines += [f"- Entity: {p.name} (CIK {p.cik}), tickers {', '.join(p.tickers) or 'n/a'}",
                  f"- SIC: {p.sic or 'n/a'} {p.sic_description or ''}".rstrip()]
    if result.framework:
        lines.append(f"- Framework: **{result.framework.name}** via {result.framework.method} ({result.framework.evidence})")
    if result.calendar:
        lines.append(f"- Fiscal year end month: {result.calendar.fiscal_year_end_month} ({result.calendar.convention})")
    lines.append("")
    if result.warnings:
        lines += ["## Warnings", ""] + [f"- {w}" for w in result.warnings] + [""]
    if not report:
        lines.append("No XBRL facts were extracted.")
        return "\n".join(lines) + "\n"
    lines += [
        "## Summary", "",
        f"- Observations: {report['observations_total']:,} total, {report['observations_mapped']:,} mapped to framework metrics",
        f"- Facts emitted: {report['facts_emitted']:,} (all versions); malformed observations dropped: {report['malformed_observations']}",
        "", "## Annual coverage (current values)", "",
        "| Metric | Fiscal years | Concept(s) | Restated periods | Concept switch |",
        "|---|---|---|---|---|",
    ]
    years_by_metric = defaultdict(list)
    for f in annual:
        years_by_metric[f.metric_id].append(f.period.fiscal_year)
    for metric_id, cov in report["coverage"].items():
        years = sorted(years_by_metric.get(metric_id, []))
        span = ("interim only" if not years else str(years[0]) if len(years) == 1 else f"{years[0]}–{years[-1]} ({len(years)})")
        lines.append(f"| {metric_id} | {span} | {', '.join(cov['concepts_used'])} | {len(cov['restatements'])} | "
                     f"{'yes' if cov['concept_switch'] else ''} |")
    lines += ["", "## Gaps", "",
              f"- Tagged concepts with no data for this filer: {', '.join(report['metrics_without_data']) or 'none'}",
              f"- Need document extraction (no standard tag): {', '.join(report['metrics_requiring_documents']) or 'none'}",
              f"- Derivation-only (computed in historical analysis): {', '.join(report['derivation_only_metrics']) or 'none'}",
              "", "## Skipped observations", "", "| Reason | Count |", "|---|---|"]
    lines += [f"| {reason} | {count:,} |" for reason, count in report["skipped"].items()]
    return "\n".join(lines) + "\n"


def _sources_md(documents: list[DocumentRecord]) -> str:
    lines = ["# Sources", "", "| Document | Type | Status | Retrieved (UTC) | SHA-256 | Source |", "|---|---|---|---|---|---|"]
    for d in documents:
        lines.append(f"| {d.document_id} | {d.document_type.value} | {d.status.value} | "
                     f"{d.retrieval_timestamp.isoformat(timespec='seconds') if d.retrieval_timestamp else ''} | "
                     f"{(d.file_hash or '')[:16]} | {d.source_url or d.local_source_path} |")
    return "\n".join(lines) + "\n"
