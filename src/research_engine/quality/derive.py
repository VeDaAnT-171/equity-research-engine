"""Derived facts with lineage: interim periods from year-to-date values, and framework derivations."""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from decimal import Decimal
from typing import Optional

from ..calendar import HALF_DAYS, QUARTER_DAYS
from ..expressions import ExpressionError, evaluate, is_additive, referenced_names
from ..schemas.financial import (
    ExtractionMethod, FinancialFact, FiscalPeriodCode as C, Period, PeriodType, Provenance, make_fact_id,
)
from ..schemas.framework import IndustryFramework, MetricSpec
from .index import FactIndex
from .model import QualityIssue, QualityReport, Severity
from .tolerance import rounding_tolerance

_NAME = {C.FY: "annual", C.H1: "first_half", C.M9: "nine_month_ytd", C.Q1: "q1", C.Q2: "q2", C.Q3: "q3"}

# (target, minuend, subtrahends). Year-to-date differencing first: it uses the fewest inputs.
INTERIM_RULES: tuple[tuple[C, C, tuple[C, ...]], ...] = (
    (C.Q2, C.H1, (C.Q1,)),
    (C.Q3, C.M9, (C.H1,)),
    (C.Q4, C.FY, (C.M9,)),
    (C.H2, C.FY, (C.H1,)),
    (C.Q3, C.M9, (C.Q1, C.Q2)),
    (C.Q4, C.FY, (C.Q1, C.Q2, C.Q3)),
)
_TARGET_DAYS = {C.Q2: QUARTER_DAYS, C.Q3: QUARTER_DAYS, C.Q4: QUARTER_DAYS, C.H2: HALF_DAYS}


def _derived_fact(company_id: str, metric_id: str, value: Decimal, unit: str, currency: Optional[str],
                  period: Period, inputs: list[FinancialFact], formula: str, notes: str) -> FinancialFact:
    input_ids = tuple(f.fact_id for f in inputs)
    return FinancialFact(
        fact_id=make_fact_id(company_id, metric_id, period, Provenance.DERIVED, f"{formula}|{'|'.join(sorted(input_ids))}"),
        company_id=company_id, metric_id=metric_id, value=value, unit=unit, currency=currency, period=period,
        provenance=Provenance.DERIVED, extraction_method=ExtractionMethod.COMPUTED, inputs=input_ids,
        formula=formula, notes=notes,
    )


def derive_interim(index: FactIndex, framework: IndustryFramework, company_id: str, report: QualityReport) -> list[FinancialFact]:
    derived: list[FinancialFact] = []
    for spec in framework.metrics:
        facts = [f for f in index.facts(spec.id) if f.period.period_type is PeriodType.DURATION]
        if not facts:
            continue
        if not spec.is_additive:
            if any(f.period.fiscal_period is C.FY for f in facts):
                report.derivations[f"interim_not_derivable_non_additive:{spec.id}"] += 1
            continue
        by_year: dict[int, dict[C, FinancialFact]] = defaultdict(dict)
        for f in facts:
            by_year[f.period.fiscal_year][f.period.fiscal_period] = f
        for year, periods in by_year.items():
            changed = True
            while changed:
                changed = False
                for target, minuend_code, sub_codes in INTERIM_RULES:
                    if target in periods or minuend_code not in periods or any(c not in periods for c in sub_codes):
                        continue
                    minuend = periods[minuend_code]
                    subs = sorted((periods[c] for c in sub_codes), key=lambda f: f.period.end)
                    if subs[0].period.start != minuend.period.start or any(
                        nxt.period.start != prev.period.end + timedelta(days=1) for prev, nxt in zip(subs, subs[1:])
                    ) or subs[-1].period.end >= minuend.period.end:
                        report.derivations[f"interim_periods_not_contiguous:{spec.id}"] += 1
                        continue
                    units = {f.unit for f in [minuend, *subs]}
                    if len(units) != 1:
                        report.derivations[f"interim_mixed_units:{spec.id}"] += 1
                        continue
                    start, end = subs[-1].period.end + timedelta(days=1), minuend.period.end
                    lo, hi = _TARGET_DAYS[target]
                    if not lo <= (end - start).days + 1 <= hi:
                        report.derivations[f"interim_period_length_unexpected:{spec.id}"] += 1
                        continue
                    period = Period(period_type=PeriodType.DURATION, start=start, end=end, fiscal_year=year, fiscal_period=target)
                    formula = f"{_NAME[minuend_code]} - " + " - ".join(_NAME[c] for c in sub_codes)
                    fact = _derived_fact(company_id, spec.id, minuend.value - sum(f.value for f in subs), minuend.unit,
                                         minuend.currency, period, [minuend, *subs], formula,
                                         f"{target.value} derived from {minuend_code.value} less {', '.join(c.value for c in sub_codes)}")
                    periods[target] = fact
                    index.add(fact)
                    derived.append(fact)
                    report.derivations[f"interim:{target.value}"] += 1
                    changed = True
    return derived


def _topological(framework: IndustryFramework) -> list[MetricSpec]:
    specs = {m.id: m for m in framework.metrics if m.derivation}
    deps = {mid: referenced_names(spec.derivation) & set(specs) for mid, spec in specs.items()}
    ordered, done = [], set()
    while len(ordered) < len(specs):
        ready = sorted(mid for mid in specs if mid not in done and deps[mid] <= done)
        for mid in ready:  # acyclic is guaranteed by framework validation
            ordered.append(specs[mid])
            done.add(mid)
    return ordered


def _output_unit(spec: MetricSpec, inputs: list[FinancialFact]) -> tuple[Optional[tuple[str, Optional[str]]], Optional[str]]:
    currencies = {f.currency for f in inputs if f.currency}
    if len(currencies) > 1:
        return None, "mixed_currency"
    currency = next(iter(currencies), None)
    if spec.unit_kind == "ratio":
        return ("pure", None), None
    if spec.unit_kind == "count":
        return ("count", None), None
    if currency is None:
        return None, "no_currency_input"
    return ((currency, currency) if spec.unit_kind == "currency" else (f"{currency}/share", currency)), None


def derive_framework_metrics(index: FactIndex, framework: IndustryFramework, company_id: str,
                             report: QualityReport) -> list[FinancialFact]:
    specs = {m.id: m for m in framework.metrics}
    derived: list[FinancialFact] = []
    for target in _topological(framework):
        names = sorted(referenced_names(target.derivation))
        anchors: dict[tuple, Period] = {}
        for name in names:
            for f in index.facts(name):
                if f.period.period_type is target.period_type:
                    anchors[(f.period.start, f.period.end)] = f.period
        summary = report.summary(f"derivation:{target.id}", f"{target.id} = {target.derivation} (checked where also reported)")
        additive = is_additive(target.derivation)
        for anchor in anchors.values():
            existing = index.at(target, anchor)
            inputs = [index.at(specs[n], anchor) for n in names]
            if any(i is None for i in inputs):
                if existing is None:
                    report.derivations[f"derivation_inputs_missing:{target.id}"] += 1
                else:
                    summary.not_evaluable += 1
                continue
            env = {n: i.value for n, i in zip(names, inputs)}
            if existing is not None:
                # Reported value wins. Where the definition is additive and inputs are reported, test consistency.
                if not additive or any(i.provenance is Provenance.DERIVED for i in inputs):
                    summary.not_evaluable += 1
                    continue
                computed = evaluate(target.derivation, env)
                tolerance = rounding_tolerance([existing.value, *env.values()])
                if abs(computed - existing.value) <= tolerance:
                    summary.passed += 1
                else:
                    summary.failed += 1
                    report.add(QualityIssue(
                        check=f"derivation:{target.id}", severity=Severity.WARNING, metric_id=target.id,
                        period=anchor.label, fact_ids=(existing.fact_id, *(i.fact_id for i in inputs)),
                        message=f"reported {target.id} differs from {target.derivation} by {computed - existing.value} "
                                f"(tolerance {tolerance}); the filer's definition may differ from the framework's",
                        details={"reported": str(existing.value), "computed": str(computed), "tolerance": str(tolerance)},
                    ))
                continue
            unit, problem = _output_unit(target, inputs)
            if problem:
                report.derivations[f"derivation_{problem}:{target.id}"] += 1
                continue
            try:
                value = evaluate(target.derivation, env)
            except ExpressionError:
                report.derivations[f"derivation_division_by_zero:{target.id}"] += 1
                continue
            notes = f"computed from framework definition {target.derivation}"
            if target.unit_kind == "ratio" and anchor.period_type is PeriodType.DURATION and anchor.fiscal_period is not C.FY:
                notes += "; interim-period ratio, not annualised"
            fact = _derived_fact(company_id, target.id, value, unit[0], unit[1], anchor, inputs, target.derivation, notes)
            index.add(fact)
            derived.append(fact)
            report.derivations[f"framework:{target.id}"] += 1
    return derived
