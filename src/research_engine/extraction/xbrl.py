"""XBRL observations -> canonical financial facts, driven entirely by the industry framework."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field

from ..calendar import FiscalCalendar
from ..schemas.financial import (
    ExtractionMethod,
    FinancialFact,
    Provenance,
    SourceLocation,
    make_fact_id,
)
from ..schemas.framework import IndustryFramework, MetricSpec
from ..sources.sec import XbrlObservation

DEFAULT_FORMS = frozenset({"10-K", "10-K/A", "10-Q", "10-Q/A", "20-F", "20-F/A", "40-F", "40-F/A", "10-KT", "10-QT"})

_CURRENCY = re.compile(r"^[A-Z]{3}$")
_PER_SHARE = re.compile(r"^([A-Z]{3})/shares$")


def unit_compatible(metric: MetricSpec, unit: str) -> bool:
    kind = metric.unit_kind
    if kind == "currency":
        return bool(_CURRENCY.match(unit))
    if kind == "currency_per_share":
        return bool(_PER_SHARE.match(unit))
    if kind == "ratio":
        return unit == "pure"
    return unit != "pure" and not _CURRENCY.match(unit) and "/" not in unit  # counts: shares, customers, ...


def canonical_unit(unit: str) -> tuple[str, str | None]:
    if _CURRENCY.match(unit):
        return unit, unit
    m = _PER_SHARE.match(unit)
    if m:
        return f"{m.group(1)}/share", m.group(1)
    return unit, None


@dataclass
class MetricCoverage:
    metric_id: str
    concepts_used: Counter = field(default_factory=Counter)      # concept -> periods sourced from it
    shadowed_concepts: Counter = field(default_factory=Counter)  # lower-precedence concepts that also had values
    periods: set = field(default_factory=set)
    restatements: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "metric_id": self.metric_id,
            "concepts_used": dict(self.concepts_used),
            "shadowed_concepts": dict(self.shadowed_concepts),
            "periods": sorted(self.periods),
            "restatements": self.restatements,
            "concept_switch": len(self.concepts_used) > 1,
        }


@dataclass
class ExtractionReport:
    framework: str
    document_id: str
    observations_total: int = 0
    observations_mapped: int = 0
    malformed_observations: int = 0
    skipped: Counter = field(default_factory=Counter)
    coverage: dict = field(default_factory=dict)
    metrics_without_data: list = field(default_factory=list)
    metrics_requiring_documents: list = field(default_factory=list)
    derivation_only_metrics: list = field(default_factory=list)
    currency_mismatches: Counter = field(default_factory=Counter)
    facts_emitted: int = 0

    def to_dict(self) -> dict:
        return {
            "framework": self.framework,
            "document_id": self.document_id,
            "observations_total": self.observations_total,
            "observations_mapped": self.observations_mapped,
            "malformed_observations": self.malformed_observations,
            "facts_emitted": self.facts_emitted,
            "skipped": dict(sorted(self.skipped.items())),
            "coverage": {k: v.to_dict() for k, v in sorted(self.coverage.items())},
            "metrics_without_data": sorted(self.metrics_without_data),
            "metrics_requiring_documents": sorted(self.metrics_requiring_documents),
            "derivation_only_metrics": sorted(self.derivation_only_metrics),
            "currency_mismatches": dict(self.currency_mismatches),
        }


def extract_xbrl_facts(
    observations: Iterable[XbrlObservation],
    *,
    framework: IndustryFramework,
    calendar: FiscalCalendar,
    company_id: str,
    document_id: str,
    allowed_forms: frozenset[str] = DEFAULT_FORMS,
    expected_currency: str | None = None,
) -> tuple[list[FinancialFact], ExtractionReport]:
    report = ExtractionReport(framework=framework.name, document_id=document_id)
    concept_index: dict[str, list[tuple[MetricSpec, int]]] = defaultdict(list)
    for metric in framework.metrics:
        for rank, concept in enumerate(metric.xbrl_concepts):
            concept_index[concept].append((metric, rank))

    # (metric_id, period_type, start, end) -> rank -> [(obs, period)]
    candidates: dict[tuple, dict[int, list]] = defaultdict(lambda: defaultdict(list))
    metrics_by_id = {m.id: m for m in framework.metrics}
    for obs in observations:
        report.observations_total += 1
        targets = concept_index.get(obs.qname)
        if not targets:
            report.skipped["unmapped_concept"] += 1
            continue
        if obs.form not in allowed_forms:
            report.skipped[f"form_not_accepted:{obs.form}"] += 1
            continue
        classification = calendar.classify(obs.start, obs.end)
        if classification.period is None:
            report.skipped[classification.skip_reason] += 1
            continue
        period = classification.period
        mapped = False
        for metric, rank in targets:
            if metric.period_type is not period.period_type:
                report.skipped[f"period_type_mismatch:{metric.id}"] += 1
                continue
            if not unit_compatible(metric, obs.unit):
                report.skipped[f"unit_incompatible:{metric.id}:{obs.unit}"] += 1
                continue
            key = (metric.id, period.period_type.value, period.start, period.end)
            candidates[key][rank].append((obs, period))
            mapped = True
        report.observations_mapped += int(mapped)

    facts: list[FinancialFact] = []
    for (metric_id, *_), by_rank in sorted(candidates.items(), key=lambda kv: (kv[0][0], kv[0][3], kv[0][2] or kv[0][3])):
        metric = metrics_by_id[metric_id]
        coverage = report.coverage.setdefault(metric_id, MetricCoverage(metric_id))
        best_rank = min(by_rank)
        for rank in by_rank:
            if rank != best_rank:
                coverage.shadowed_concepts[metric.xbrl_concepts[rank]] += 1
        chosen = sorted(by_rank[best_rank], key=lambda item: (item[0].filed, item[0].accession))
        concept = metric.xbrl_concepts[best_rank]
        period = chosen[0][1]
        coverage.concepts_used[concept] += 1
        coverage.periods.add(period.label)

        # Comparatives repeat the same value in later filings: keep the first disclosure of each distinct
        # value. A different value for the same period in a later filing is a restatement: keep both.
        first_by_value: dict[tuple, XbrlObservation] = {}
        for obs, _ in chosen:
            first_by_value.setdefault((obs.unit, obs.value), obs)
        distinct = list(first_by_value.values())
        units = Counter(o.unit for o in distinct)
        if len(units) > 1:
            report.skipped[f"multiple_units_same_period:{metric_id}"] += 1
        if any(count > 1 for count in units.values()):
            coverage.restatements.append({
                "period": period.label,
                "values": [{"value": str(o.value), "unit": o.unit, "accession": o.accession, "form": o.form,
                            "filed": o.filed.isoformat()} for o in distinct],
            })
        for obs in distinct:
            unit, currency = canonical_unit(obs.unit)
            if expected_currency and currency and currency != expected_currency:
                report.currency_mismatches[f"{metric_id}:{currency}"] += 1
            later = [o for o, _ in chosen if o.value == obs.value and o.accession != obs.accession]
            notes = f"concept rank {best_rank + 1} of {len(metric.xbrl_concepts)}"
            if later:
                notes += f"; repeated in {len(later)} later filing(s)"
            facts.append(FinancialFact(
                fact_id=make_fact_id(company_id, metric_id, period, Provenance.REPORTED,
                                     f"{obs.qname}|{obs.unit}|{obs.accession}|{obs.value}"),
                company_id=company_id,
                metric_id=metric_id,
                value=obs.value,
                unit=unit,
                currency=currency,
                period=period,
                provenance=Provenance.REPORTED,
                extraction_method=ExtractionMethod.XBRL,
                source=SourceLocation(
                    document_id=document_id,
                    xbrl_concept=obs.qname,
                    xbrl_context=obs.frame,
                    filing_accession=obs.accession,
                    filing_form=obs.form,
                    filed_date=obs.filed,
                ),
                notes=notes,
            ))

    for metric in framework.metrics:
        if metric.id in report.coverage:
            continue
        if metric.xbrl_concepts:
            report.metrics_without_data.append(metric.id)
        elif metric.derivation:
            report.derivation_only_metrics.append(metric.id)
        else:
            report.metrics_requiring_documents.append(metric.id)
    report.facts_emitted = len(facts)
    return facts, report


def current_facts(facts: Iterable[FinancialFact]) -> list[FinancialFact]:
    """One fact per (metric, period): the value from the most recently filed disclosure.
    Earlier values stay in the full fact set; this is a view, not a deletion."""
    best: dict[tuple, FinancialFact] = {}
    for fact in facts:
        key = (fact.metric_id, fact.period.period_type, fact.period.start, fact.period.end)
        incumbent = best.get(key)
        rank = (fact.source.filed_date, fact.source.filing_accession) if fact.source else (None, None)
        if incumbent is None or rank > (incumbent.source.filed_date, incumbent.source.filing_accession):
            best[key] = fact
    return sorted(best.values(), key=lambda f: (f.metric_id, f.period.end, f.period.period_type.value))
