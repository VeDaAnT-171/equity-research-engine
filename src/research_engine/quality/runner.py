from __future__ import annotations

from typing import Iterable, Optional

from ..extraction import current_facts
from ..schemas.financial import FinancialFact, Provenance
from ..schemas.framework import IndustryFramework
from .checks import build_coverage, run_continuity_checks, run_identity_checks
from .derive import derive_framework_metrics, derive_interim
from .index import FactIndex
from .model import QualityIssue, QualityReport, Severity


def run_quality(
    facts: Iterable[FinancialFact],
    *,
    framework: IndustryFramework,
    company_id: str,
    historical_years: int,
    extraction_report: Optional[dict] = None,
) -> tuple[list[FinancialFact], list[FinancialFact], QualityReport]:
    """Returns (current reported facts, derived facts, report). Never modifies reported values."""
    reported = [f for f in facts if f.provenance is Provenance.REPORTED]
    current = current_facts(reported)
    report = QualityReport(company_id=company_id, framework=framework.name, historical_years=historical_years)
    index = FactIndex(current)

    derived = derive_interim(index, framework, company_id, report)
    derived += derive_framework_metrics(index, framework, company_id, report)
    run_identity_checks(index, framework, report)
    run_continuity_checks(index, framework, report)
    build_coverage(index, framework, report)
    if extraction_report:
        _carry_extraction_findings(extraction_report, report)
    short = [m for m, cells in report.coverage.items() if cells and len(cells) < historical_years]
    if short:
        report.add(QualityIssue("history_length", Severity.INFO,
                                f"{len(short)} metrics have fewer than the configured {historical_years} fiscal years: "
                                + ", ".join(short)))
    return current, derived, report


def _carry_extraction_findings(extraction: dict, report: QualityReport) -> None:
    for metric_id, cov in extraction.get("coverage", {}).items():
        for r in cov.get("restatements", []):
            values = ", ".join(f"{v['value']} ({v['form']} filed {v['filed']})" for v in r["values"])
            report.add(QualityIssue("restatement", Severity.INFO, f"{metric_id} {r['period']} was restated: {values}; "
                                    "current views use the latest filing", metric_id, r["period"]))
        if cov.get("concept_switch"):
            report.add(QualityIssue("concept_switch", Severity.WARNING,
                                    f"{metric_id} is sourced from different XBRL concepts across periods "
                                    f"({', '.join(cov['concepts_used'])}); definitions may not be comparable", metric_id,
                                    details={"concepts": cov["concepts_used"]}))
    for key, count in extraction.get("currency_mismatches", {}).items():
        metric_id, currency = key.split(":", 1)
        report.add(QualityIssue("currency", Severity.WARNING,
                                f"{count} {metric_id} values reported in {currency}, not the configured reporting currency",
                                metric_id))
    for metric_id in extraction.get("metrics_without_data", []):
        report.derivations[f"no_xbrl_data:{metric_id}"] += 1
def _carry_extraction_findings(extraction: dict, report: QualityReport) -> None:
    for metric_id, cov in extraction.get("coverage", {}).items():
        for r in cov.get("restatements", []):
            values = ", ".join(f"{v['value']} ({v['form']} filed {v['filed']})" for v in r["values"])
            report.add(QualityIssue("restatement", Severity.INFO, f"{metric_id} {r['period']} was restated: {values}; "
                                    "current views use the latest filing", metric_id, r["period"]))
        if cov.get("concept_switch"):
            report.add(QualityIssue("concept_switch", Severity.WARNING,
                                    f"{metric_id} is sourced from different XBRL concepts across periods "
                                    f"({', '.join(cov['concepts_used'])}); definitions may not be comparable", metric_id,
                                    details={"concepts": cov["concepts_used"]}))
    for key, count in extraction.get("currency_mismatches", {}).items():
        metric_id, currency = key.split(":", 1)
        report.add(QualityIssue("currency", Severity.WARNING,
                                f"{count} {metric_id} values reported in {currency}, not the configured reporting currency",
                                metric_id))
    for metric_id in extraction.get("metrics_without_data", []):
        report.derivations[f"no_xbrl_data:{metric_id}"] += 1
