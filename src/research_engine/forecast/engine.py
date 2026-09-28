"""Scenario projection over the driver graph.

Each scenario is evaluated independently. Within a scenario, forecast years are evaluated in
order, and within a year metrics are evaluated in the driver graph's topological order, so a
metric never reads a value that has not been produced yet.

Nothing is guessed. A metric whose rule needs an input that is missing, or an assumption that
was declared but never given a value, is *not projected*, and the reason is counted and
reported. Silence would be the only real failure mode here: a forecast that quietly invents a
number it could not compute is worse than one that is visibly incomplete.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import Decimal

from ..expressions import ExpressionError, evaluate
from ..schemas.analytics import AnalyticValue
from ..schemas.assumption import Assumption
from ..schemas.financial import FinancialFact, FiscalPeriodCode
from ..schemas.forecast import BASE_SCENARIO, ForecastValue, make_forecast_id
from ..schemas.framework import IndustryFramework
from .assumptions import (
    SEED_WINDOW_YEARS,
    AssumptionSet,
    AssumptionUnavailable,
    annual_analytic_series,
    annual_metric_series,
    seed_growth,
    seed_level,
)
from .drivers import DriverGraph, build_driver_graph


class NotProjected(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass
class ForecastResult:
    base_year: int
    forecast_years: tuple[int, ...]
    graph: DriverGraph
    assumptions: AssumptionSet
    seeded: tuple[Assumption, ...]
    values: list[ForecastValue] = field(default_factory=list)
    by_scenario: dict[str, dict[str, dict[int, ForecastValue]]] = field(default_factory=dict)
    not_projected: Counter = field(default_factory=Counter)
    unseeded: dict[str, str] = field(default_factory=dict)

    @property
    def scenarios(self) -> tuple[str, ...]:
        return tuple(self.by_scenario)


def seed_assumptions(framework: IndustryFramework, graph: DriverGraph, *, company_id: str,
                     facts: Iterable[FinancialFact], analytics: Iterable[AnalyticValue],
                     base_year: int) -> tuple[list[Assumption], dict[str, str]]:
    """One `historical` assumption per exogenous input the graph needs, from verified history."""
    metric_series = annual_metric_series(facts)
    analytic_series = annual_analytic_series(analytics)
    metrics = {m.id: m for m in framework.metrics}
    seeded: list[Assumption] = []
    unseeded: dict[str, str] = {}

    for key in graph.assumption_keys:
        prefix, _, name = key.partition(".")
        assumption: Assumption | None = None
        if prefix == "growth":
            seed = metric_series.get(name)
            if seed is None:
                unseeded[key] = f"no annual history for metric {name!r}"
                continue
            assumption = seed_growth(company_id, name, seed, base_year=base_year)
            if assumption is None:
                unseeded[key] = (
                    f"no usable year-on-year observation of {name!r} in "
                    f"FY{base_year - SEED_WINDOW_YEARS + 1}-FY{base_year}: the series is stale, "
                    "discontinuous, or crosses zero there, so no growth rate can be measured")
        elif prefix == "level":
            seed = metric_series.get(name)
            if seed is None:
                unseeded[key] = f"no annual history for metric {name!r}"
                continue
            spec = metrics[name]
            assumption = seed_level(company_id, name, seed, key=key,
                                    unit="ratio" if spec.unit_kind == "ratio" else (spec.unit_kind or "ratio"),
                                    label="level of", base_year=base_year)
            if assumption is None:
                unseeded[key] = (f"no annual observations of {name!r} in "
                                 f"FY{base_year - SEED_WINDOW_YEARS + 1}-FY{base_year}")
        elif prefix == "rate":
            seed = analytic_series.get(name)
            if seed is None:
                unseeded[key] = (f"analytic {name!r} was not computed in the historical analysis, "
                                 "so no rate can be seeded from it")
                continue
            assumption = seed_level(company_id, name, seed, key=key, unit="ratio", label="",
                                    base_year=base_year)
            if assumption is None:
                unseeded[key] = (f"analytic {name!r} has no traceable values in "
                                 f"FY{base_year - SEED_WINDOW_YEARS + 1}-FY{base_year}")
        else:  # pragma: no cover - build_driver_graph emits no other prefixes
            unseeded[key] = f"unknown assumption namespace {prefix!r}"
        if assumption is not None:
            seeded.append(assumption)
    return seeded, unseeded


class _Projector:
    def __init__(self, framework: IndustryFramework, graph: DriverGraph, assumptions: AssumptionSet,
                 *, company_id: str, base_facts: dict[str, FinancialFact], base_year: int,
                 years: tuple[int, ...]):
        self.framework, self.graph, self.assumptions = framework, graph, assumptions
        self.company_id, self.base_facts, self.base_year, self.years = company_id, base_facts, base_year, years
        self.metrics = {m.id: m for m in framework.metrics}
        self.not_projected: Counter = Counter()

    def _resolve(self, key: str, year: int, scenario: str) -> Assumption:
        try:
            return self.assumptions.resolve(key, year, scenario)
        except AssumptionUnavailable as exc:
            raise NotProjected(exc.reason) from None

    def _currency(self, metric_id: str, inputs: Iterable[ForecastValue]) -> str | None:
        spec = self.metrics[metric_id]
        if spec.unit_kind not in ("currency", "currency_per_share"):
            return None
        fact = self.base_facts.get(metric_id)
        if fact is not None and fact.currency:
            return fact.currency
        seen = {v.currency for v in inputs if v.currency}
        return next(iter(seen)) if len(seen) == 1 else None

    def project(self, scenario: str) -> dict[str, dict[int, ForecastValue]]:
        series: dict[str, dict[int, ForecastValue]] = defaultdict(dict)

        for metric_id in self.graph.rules:
            fact = self.base_facts.get(metric_id)
            if fact is None:
                continue
            spec = self.metrics[metric_id]
            series[metric_id][self.base_year] = ForecastValue(
                value_id=make_forecast_id(self.company_id, scenario, metric_id, self.base_year, (fact.fact_id,)),
                company_id=self.company_id, scenario=scenario, metric_id=metric_id,
                fiscal_year=self.base_year, value=fact.value, unit_kind=spec.unit_kind,
                currency=fact.currency, method="actual", formula="reported",
                input_fact_ids=(fact.fact_id,),
            )

        for year in self.years:
            for metric_id in self.graph.order:
                rule = self.graph.rule(metric_id)
                spec = self.metrics[metric_id]
                try:
                    value, used_assumptions, inputs = self._value(rule, year, scenario, series)
                except NotProjected as exc:
                    self.not_projected[f"{metric_id}:{exc.reason}"] += 1
                    continue
                fact_ids: list[str] = []
                value_ids: list[str] = []
                for v in inputs:
                    if v.is_actual:
                        fact_ids += [f for f in v.input_fact_ids if f not in fact_ids]
                    if v.value_id not in value_ids:
                        value_ids.append(v.value_id)
                ids = tuple(a.assumption_id for a in used_assumptions)
                fallback = {f for v in inputs for f in v.fallback_for}
                if rule.fallback_for is not None:
                    fallback.add(metric_id)
                series[metric_id][year] = ForecastValue(
                    value_id=make_forecast_id(self.company_id, scenario, metric_id, year, ids + tuple(value_ids)),
                    company_id=self.company_id, scenario=scenario, metric_id=metric_id, fiscal_year=year,
                    value=value, unit_kind=spec.unit_kind, currency=self._currency(metric_id, inputs),
                    method=rule.method, formula=rule.formula, assumption_ids=ids,
                    assumption_types=tuple(a.type.value for a in used_assumptions),
                    input_value_ids=tuple(value_ids), input_fact_ids=tuple(fact_ids),
                    fallback_for=tuple(sorted(fallback)),
                )
        return {k: dict(v) for k, v in series.items() if v}

    def _value(self, rule, year: int, scenario: str,
               series: dict[str, dict[int, ForecastValue]]) -> tuple[Decimal, list[Assumption], list[ForecastValue]]:
        if rule.method == "level":
            a = self._resolve(rule.assumption_keys[0], year, scenario)
            return a.value, [a], []

        if rule.method == "growth":
            prior = series.get(rule.metric_id, {}).get(year - 1)
            if prior is None:
                reason = "base_year_actual_missing" if year - 1 == self.base_year else "prior_year_not_projected"
                raise NotProjected(f"{reason}:{rule.metric_id}")
            # Compounding a rate onto a non-positive starting value is the same error the
            # historical analytics refuse, made once and then repeated for every year of the
            # horizon. A bank whose base-year operating cash flow was -147.8bn had it multiplied
            # by (1 + a rate) into -38.7bn, -10.1bn, -2.7bn, -697m, -183m — a decay toward zero
            # that the forecast table presented exactly like its deposit projection. There is no
            # rate that makes this meaningful, so the refusal belongs at the starting point.
            if prior.value <= 0:
                raise NotProjected(f"growth_base_not_positive:{rule.metric_id}")
            a = self._resolve(rule.assumption_keys[0], year, scenario)
            return prior.value * (Decimal(1) + a.value), [a], [prior]

        env: dict[str, Decimal] = {}
        used: list[Assumption] = []
        inputs: list[ForecastValue] = []
        for dep in rule.metric_inputs:
            v = series.get(dep, {}).get(year)
            if v is None:
                raise NotProjected(f"input_not_projected:{dep}")
            env[dep] = v.value
            inputs.append(v)
        for key in rule.assumption_keys:
            a = self._resolve(key, year, scenario)
            env[key.partition(".")[2]] = a.value
            used.append(a)
        try:
            return evaluate(rule.formula, env), used, inputs
        except ExpressionError as exc:
            raise NotProjected("division_by_zero" if "zero" in str(exc) else "formula_not_evaluable") from None


def run_forecast(framework: IndustryFramework, *, company_id: str, facts: Iterable[FinancialFact],
                 analytics: Iterable[AnalyticValue], analyst_assumptions: Iterable[Assumption] = (),
                 scenarios: Iterable = (), forecast_years: int = 5) -> ForecastResult:
    facts = list(facts)
    analytics = list(analytics)

    annual = [f for f in facts if f.period.fiscal_period is FiscalPeriodCode.FY]
    if not annual:
        from ..errors import ForecastError
        raise ForecastError("no full-year facts available; run `ingest`, `quality` and `analyze` first")
    base_year = max(f.period.fiscal_year for f in annual)
    base_facts = {f.metric_id: f for f in annual if f.period.fiscal_year == base_year}
    years = tuple(range(base_year + 1, base_year + 1 + forecast_years))
    # The plan depends on what this company actually reports: a driver whose formula needs a
    # metric with no base-year value takes its declared fallback, if the framework gave it one.
    graph = build_driver_graph(framework, available=frozenset(base_facts))

    seeded, unseeded = seed_assumptions(framework, graph, company_id=company_id, facts=facts,
                                        analytics=analytics, base_year=base_year)
    registry = AssumptionSet([*seeded, *analyst_assumptions], scenarios)

    result = ForecastResult(base_year=base_year, forecast_years=years, graph=graph, assumptions=registry,
                            seeded=tuple(seeded), unseeded=unseeded)
    for scenario in registry.scenarios:
        projector = _Projector(framework, graph, registry, company_id=company_id, base_facts=base_facts,
                               base_year=base_year, years=years)
        result.by_scenario[scenario] = projector.project(scenario)
        if scenario == BASE_SCENARIO:
            result.not_projected = projector.not_projected
    result.values = sorted(
        (v for s in result.by_scenario.values() for m in s.values() for v in m.values()),
        key=lambda v: (v.scenario, v.metric_id, v.fiscal_year),
    )
    return result
