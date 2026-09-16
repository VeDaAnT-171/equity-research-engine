"""Accounting identities and time-series continuity checks. Every threshold states its rule."""

from __future__ import annotations

import math
import statistics
from decimal import Decimal

from ..expressions import evaluate
from ..schemas.financial import FinancialFact, FiscalPeriodCode, Period, PeriodType, Provenance
from ..schemas.framework import OPENING_SUFFIX, IdentityCheck, IndustryFramework
from .index import FactIndex
from .model import QualityIssue, QualityReport, Severity
from .tolerance import rounding_tolerance

# Scale errors multiply a value by exactly 10^3k. Flag a year-on-year ratio within 0.2 of such a power in log10
# terms: that band equals a genuine change between -37% and +58% on top of the scale factor.
SCALE_POWERS = (3, 6, 9)
SCALE_BAND_LOG10 = 0.2
# Modified z-score outlier rule (Iglewicz & Hoaglin, 1993): |0.6745 (x - median) / MAD| > 3.5.
MODIFIED_Z_CUTOFF = 3.5
MIN_GROWTH_OBSERVATIONS = 5


def _anchor_type(check: IdentityCheck, framework: IndustryFramework) -> PeriodType:
    for name in check.names:
        if name.endswith(OPENING_SUFFIX):
            return PeriodType.DURATION
        if framework.metric(name).period_type is PeriodType.DURATION:
            return PeriodType.DURATION
    return PeriodType.INSTANT


def run_identity_checks(index: FactIndex, framework: IndustryFramework, report: QualityReport) -> None:
    for check in framework.checks:
        summary = report.summary(check.id, check.description)
        anchor_type = _anchor_type(check, framework)
        anchors: dict[tuple, Period] = {}
        for name in check.names:
            for f in index.facts(IdentityCheck.base_metric(name)):
                if f.period.period_type is anchor_type:
                    anchors[(f.period.start, f.period.end)] = f.period
        for anchor in sorted(anchors.values(), key=lambda p: (p.end, p.start or p.end)):
            env: dict[str, Decimal] = {}
            used: list[FinancialFact] = []
            missing = []
            for name in sorted(check.names):
                base = IdentityCheck.base_metric(name)
                fact = index.opening(base, anchor) if name != base else index.at(framework.metric(base), anchor)
                if fact is None:
                    if name in check.optional_terms:
                        env[name] = Decimal(0)
                        continue
                    missing.append(name)
                    continue
                env[name] = fact.value
                used.append(fact)
            if missing:
                summary.not_evaluable += 1
                continue
            left, right = evaluate(check.left, env), evaluate(check.right, env)
            tolerance = rounding_tolerance(f.value for f in used)
            if abs(left - right) <= tolerance:
                summary.passed += 1
                continue
            summary.failed += 1
            omitted = sorted(n for n in check.optional_terms if not any(f.metric_id == n for f in used))
            report.add(QualityIssue(
                check=check.id, severity=Severity(check.severity), period=anchor.label,
                fact_ids=tuple(f.fact_id for f in used),
                message=f"{check.left} = {left} but {check.right} = {right} (difference {left - right}, tolerance {tolerance})"
                        + (f". {check.explanation_if_failed}" if check.explanation_if_failed else ""),
                details={"left": str(left), "right": str(right), "difference": str(left - right),
                         "tolerance": str(tolerance), "optional_terms_assumed_zero": omitted,
                         "derived_inputs": [f.fact_id for f in used if f.provenance is Provenance.DERIVED]},
            ))


def _annual_series(index: FactIndex, metric_id: str) -> list[FinancialFact]:
    return sorted((f for f in index.facts(metric_id) if f.period.fiscal_period is FiscalPeriodCode.FY),
                  key=lambda f: f.period.fiscal_year)


def run_continuity_checks(index: FactIndex, framework: IndustryFramework, report: QualityReport) -> None:
    sign = report.summary("sign", "Metrics that cannot be negative are non-negative")
    scale = report.summary("scale_break", "No year-on-year change consistent with a thousand/million unit error")
    outliers = report.summary("unusual_change", "Year-on-year log change within the modified z-score cutoff of the metric's own history")
    gaps = report.summary("missing_years", "Annual series have no missing fiscal years")

    for spec in framework.metrics:
        facts = index.facts(spec.id)
        if spec.expected_sign == "non_negative":
            for f in facts:
                if f.value < 0:
                    sign.failed += 1
                    report.add(QualityIssue("sign", Severity.ERROR, f"{spec.id} is negative ({f.value}) but cannot be",
                                            spec.id, f.period.label, (f.fact_id,),
                                            {"provenance": f.provenance.value, "formula": f.formula}))
                else:
                    sign.passed += 1

        series = _annual_series(index, spec.id)
        if len(series) < 2:
            continue
        years = [f.period.fiscal_year for f in series]
        absent = sorted(set(range(years[0], years[-1] + 1)) - set(years))
        if absent:
            gaps.failed += 1
            report.add(QualityIssue("missing_years", Severity.WARNING,
                                    f"{spec.id} has no FY value for {', '.join(map(str, absent))} inside its reported range",
                                    spec.id, details={"missing": absent}))
        else:
            gaps.passed += 1

        growths: list[tuple[float, FinancialFact, FinancialFact]] = []
        for prev, cur in zip(series, series[1:]):
            if cur.period.fiscal_year != prev.period.fiscal_year + 1:
                continue
            if prev.value == 0 or cur.value == 0 or (prev.value > 0) != (cur.value > 0):
                scale.not_evaluable += 1
                continue
            log_ratio = math.log10(abs(float(cur.value / prev.value)))
            if any(abs(abs(log_ratio) - p) <= SCALE_BAND_LOG10 for p in SCALE_POWERS):
                scale.failed += 1
                report.add(QualityIssue("scale_break", Severity.WARNING,
                                        f"{spec.id} changed by a factor of {abs(cur.value / prev.value):.4g} from FY{prev.period.fiscal_year}; "
                                        "consistent with a unit or scale error", spec.id, cur.period.label,
                                        (prev.fact_id, cur.fact_id), {"log10_ratio": round(log_ratio, 3)}))
            else:
                scale.passed += 1
            growths.append((math.log(abs(float(cur.value / prev.value))), prev, cur))

        if len(growths) < MIN_GROWTH_OBSERVATIONS:
            outliers.not_evaluable += 1
            continue
        values = [g for g, _, _ in growths]
        median = statistics.median(values)
        mad = statistics.median(abs(v - median) for v in values)
        if mad == 0:
            outliers.not_evaluable += 1
            continue
        for g, prev, cur in growths:
            z = 0.6745 * (g - median) / mad
            if abs(z) > MODIFIED_Z_CUTOFF:
                outliers.failed += 1
                report.add(QualityIssue("unusual_change", Severity.INFO,
                                        f"{spec.id} FY{cur.period.fiscal_year} change of {math.exp(g) - 1:+.1%} is unusual for this "
                                        f"metric's history (modified z {z:.1f}); verify before modelling", spec.id, cur.period.label,
                                        (prev.fact_id, cur.fact_id), {"modified_z": round(z, 2), "observations": len(values)}))
            else:
                outliers.passed += 1


def build_coverage(index: FactIndex, framework: IndustryFramework, report: QualityReport) -> None:
    for spec in framework.metrics:
        cells = {f"FY{f.period.fiscal_year}": ("R" if f.provenance is Provenance.REPORTED else "D")
                 for f in _annual_series(index, spec.id)}
        report.coverage[spec.id] = dict(sorted(cells.items()))
