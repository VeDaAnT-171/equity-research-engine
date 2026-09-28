"""Annual historical analytics over the current fact set, gated by the data-quality report."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import Decimal

from ..expressions import ExpressionError, evaluate, referenced_names
from ..schemas.analytics import AnalyticBasis, AnalyticValue, make_value_id
from ..schemas.financial import FinancialFact, FiscalPeriodCode, PeriodType, Provenance
from ..schemas.framework import AnalyticSpec, IndustryFramework


class NotComputed(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class QualityGate:
    """Error-severity issues block their facts; warning-severity issues travel with every value built on them."""

    def __init__(self, quality_report: dict | None):
        self.blocked: dict[str, str] = {}
        self.blocked_periods: dict[tuple[str, str], str] = {}
        self.flags: dict[str, set[str]] = defaultdict(set)
        for issue in (quality_report or {}).get("issues", []):
            if issue["severity"] == "error":
                for fid in issue.get("fact_ids", []):
                    self.blocked.setdefault(fid, issue["check"])
                if issue.get("metric_id") and issue.get("period") and not issue.get("fact_ids"):
                    self.blocked_periods.setdefault((issue["metric_id"], issue["period"]), issue["check"])
            elif issue["severity"] == "warning":
                for fid in issue.get("fact_ids", []):
                    self.flags[fid].add(issue["check"])

    def check(self, fact: FinancialFact) -> set[str]:
        check = self.blocked.get(fact.fact_id) or self.blocked_periods.get((fact.metric_id, fact.period.label))
        if check:
            raise NotComputed(f"input_failed_quality:{check}")
        return set(self.flags.get(fact.fact_id, ()))


@dataclass
class Term:
    value: Decimal
    fact_ids: list[str] = field(default_factory=list)
    value_ids: list[str] = field(default_factory=list)
    flags: set[str] = field(default_factory=set)
    derived: bool = False
    currencies: set[str] = field(default_factory=set)

    @staticmethod
    def combine(terms: Iterable[Term], value: Decimal) -> Term:
        out = Term(value)
        for t in terms:
            out.fact_ids += [f for f in t.fact_ids if f not in out.fact_ids]
            out.value_ids += [v for v in t.value_ids if v not in out.value_ids]
            out.flags |= t.flags
            out.derived |= t.derived
            out.currencies |= t.currencies
        return out


@dataclass
class AnalysisResult:
    values: list[AnalyticValue]
    by_analytic: dict[str, dict[int, AnalyticValue]]
    not_computed: Counter
    fiscal_years: list[int]


def _order(framework: IndustryFramework) -> list[AnalyticSpec]:
    ids = framework.analytic_ids
    deps = {a.id: set(a.references) & ids for a in framework.analytics}
    ordered: list = []
    done: set[str] = set()
    while len(ordered) < len(deps):
        for a in framework.analytics:  # acyclic is guaranteed by framework validation
            if a.id not in done and deps[a.id] <= done:
                ordered.append(a)
                done.add(a.id)
    return ordered


class _Analyzer:
    def __init__(self, framework: IndustryFramework, facts: Iterable[FinancialFact], company_id: str, gate: QualityGate):
        self.framework, self.company_id, self.gate = framework, company_id, gate
        self.metrics = {m.id: m for m in framework.metrics}
        self.flow: dict[tuple[str, int], FinancialFact] = {}
        self.balance: dict[tuple[str, int], FinancialFact] = {}
        for f in facts:
            if f.period.fiscal_period is not FiscalPeriodCode.FY:
                continue
            store = self.flow if f.period.period_type is PeriodType.DURATION else self.balance
            store[(f.metric_id, f.period.fiscal_year)] = f
        self.metrics_with_data = {m for (m, _) in (*self.flow, *self.balance)}
        self.values: dict[str, dict[int, AnalyticValue]] = defaultdict(dict)
        self.not_computed: Counter = Counter()

    # ---- inputs ---------------------------------------------------------------------------
    def _fact_term(self, fact: FinancialFact) -> Term:
        flags = self.gate.check(fact)
        return Term(fact.value, [fact.fact_id], [], flags, fact.provenance is Provenance.DERIVED,
                    {fact.currency} if fact.currency else set())

    def point(self, name: str, year: int, basis: str = "period_end") -> Term:
        if name in self.framework.analytic_ids:
            v = self.values[name].get(year)
            if v is None:
                raise NotComputed(f"input_not_computed:{name}")
            return Term(v.value, [], [v.value_id], set(v.quality_flags), v.uses_derived_facts,
                        {v.currency} if v.currency else set())
        spec = self.metrics[name]
        if name not in self.metrics_with_data:
            raise NotComputed(f"input_missing:{name}")
        if spec.period_type is PeriodType.DURATION:
            fact = self.flow.get((name, year))
            if fact is None:
                raise NotComputed(f"input_missing:{name}")
            return self._fact_term(fact)
        if basis in ("period_end", "average"):
            closing = self.balance.get((name, year))
            if closing is None:
                raise NotComputed(f"input_missing:{name}")
        if basis == "period_end":
            return self._fact_term(closing)
        opening = self.balance.get((name, year - 1))
        if opening is None:
            raise NotComputed(f"opening_balance_missing:{name}")
        if basis == "opening":
            return self._fact_term(opening)
        a, b = self._fact_term(opening), self._fact_term(closing)
        return Term.combine([a, b], (a.value + b.value) / 2)

    def candidate_years(self, spec: AnalyticSpec) -> set[int]:
        years: set[int] = set()
        for name in spec.references:
            if name in self.framework.analytic_ids:
                years |= set(self.values[name])
            else:
                years |= {y for (m, y) in (*self.flow, *self.balance) if m == name}
        return years

    # ---- kinds ----------------------------------------------------------------------------
    def _growth(self, name: str, year: int) -> tuple[Decimal, Term]:
        cur = self.point(name, year)
        try:
            prev = self.point(name, year - 1)
        except NotComputed:
            raise NotComputed(f"prior_year_missing:{name}") from None
        if prev.value <= 0:
            raise NotComputed(f"growth_base_not_positive:{name}")
        return cur.value / prev.value - 1, Term.combine([cur, prev], Decimal(0))

    def compute(self, spec: AnalyticSpec, year: int) -> tuple[Decimal, Term, str, AnalyticBasis]:
        if spec.kind == "level":
            t = self.point(spec.metric, year)
            return t.value, t, spec.metric, "period_end"
        if spec.kind == "growth":
            g, t = self._growth(spec.metric, year)
            return g, t, f"{spec.metric}[t] / {spec.metric}[t-1] - 1", "year_on_year"
        if spec.kind == "elasticity":
            g_of, t_of = self._growth(spec.of, year)
            g_rel, t_rel = self._growth(spec.relative_to, year)
            if g_rel == 0:
                raise NotComputed(f"reference_growth_zero:{spec.relative_to}")
            return g_of / g_rel, Term.combine([t_of, t_rel], Decimal(0)), f"growth({spec.of}) / growth({spec.relative_to})", "year_on_year"
        if spec.kind == "expression":
            names = sorted(referenced_names(spec.formula))
            terms = {n: self.point(n, year) for n in names}
            try:
                value = evaluate(spec.formula, {n: t.value for n, t in terms.items()})
            except ExpressionError:
                raise NotComputed("division_by_zero") from None
            return value, Term.combine(terms.values(), value), spec.formula, "period_end"
        # ratio
        num_terms = {n: self.point(n, year) for n in sorted(referenced_names(spec.numerator))}
        den_terms = {}
        for n in sorted(referenced_names(spec.denominator)):
            is_balance = n in self.metrics and self.metrics[n].period_type is PeriodType.INSTANT
            den_terms[n] = self.point(n, year, spec.denominator_basis if is_balance else "period_end")
        denominator = evaluate(spec.denominator, {n: t.value for n, t in den_terms.items()})
        if denominator <= 0:
            raise NotComputed("denominator_not_positive")
        numerator = evaluate(spec.numerator, {n: t.value for n, t in num_terms.items()})
        basis = spec.denominator_basis
        den_label = spec.denominator if basis == "period_end" else f"{basis}({spec.denominator})"
        return (numerator / denominator, Term.combine([*num_terms.values(), *den_terms.values()], Decimal(0)),
                f"({spec.numerator}) / {den_label}", basis)

    def run(self) -> None:
        for spec in _order(self.framework):
            years = sorted(self.candidate_years(spec))
            for year in years:
                try:
                    value, term, formula, basis = self.compute(spec, year)
                except NotComputed as exc:
                    reason = exc.reason
                    if year == years[0] and reason.startswith(("prior_year_missing", "opening_balance_missing")):
                        # The first year of history has no prior year by construction: labelled, not hidden.
                        reason = "start_of_history:" + reason.split(":", 1)[1]
                    self.not_computed[f"{spec.id}:{reason}"] += 1
                    continue
                currency = None
                if spec.unit_kind in ("currency", "currency_per_share"):
                    if len(term.currencies) != 1:
                        self.not_computed[f"{spec.id}:{'mixed_currency' if term.currencies else 'no_currency_input'}"] += 1
                        continue
                    currency = next(iter(term.currencies))
                elif len(term.currencies) > 1:
                    self.not_computed[f"{spec.id}:mixed_currency"] += 1
                    continue
                inputs = tuple(term.fact_ids) + tuple(term.value_ids)
                self.values[spec.id][year] = AnalyticValue(
                    value_id=make_value_id(self.company_id, spec.id, year, inputs),
                    company_id=self.company_id, analytic_id=spec.id, category=spec.category, kind=spec.kind,
                    unit_kind=spec.unit_kind, fiscal_year=year, value=value, currency=currency, formula=formula,
                    basis=basis, input_fact_ids=tuple(term.fact_ids), input_value_ids=tuple(term.value_ids),
                    quality_flags=tuple(sorted(term.flags)), uses_derived_facts=term.derived,
                )


def run_analytics(framework: IndustryFramework, facts: Iterable[FinancialFact], *, company_id: str,
                  quality_report: dict | None) -> AnalysisResult:
    facts = list(facts)
    analyzer = _Analyzer(framework, facts, company_id, QualityGate(quality_report))
    analyzer.run()
    values = [v for series in analyzer.values.values() for v in series.values()]
    years = sorted({f.period.fiscal_year for f in facts if f.period.fiscal_period is FiscalPeriodCode.FY})
    return AnalysisResult(sorted(values, key=lambda v: (v.analytic_id, v.fiscal_year)),
                          {k: dict(v) for k, v in analyzer.values.items() if v}, analyzer.not_computed, years)
