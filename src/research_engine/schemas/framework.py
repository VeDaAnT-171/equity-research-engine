"""Industry framework schema: metrics, drivers and valuation policy as data, not code."""

from __future__ import annotations

import re
from collections.abc import Iterable
from enum import Enum
from typing import Literal

from pydantic import Field, field_validator, model_validator

from ..expressions import is_additive, referenced_names
from .common import METRIC_ID_PATTERN, SLUG_PATTERN, XBRL_CONCEPT_PATTERN, StrictModel
from .financial import PeriodType


class ValuationFamily(str, Enum):
    RELATIVE = "relative"
    INTRINSIC = "intrinsic"
    SCENARIO = "scenario"  # a mode applied over methods, not a method itself


class ValuationMethod(str, Enum):
    DCF_FCFF = "dcf_fcff"
    DCF_FCFE = "dcf_fcfe"
    RESIDUAL_INCOME = "residual_income"
    DIVIDEND_DISCOUNT = "dividend_discount"
    SUM_OF_THE_PARTS = "sum_of_the_parts"
    PE = "pe"
    EV_EBITDA = "ev_ebitda"
    EV_REVENUE = "ev_revenue"
    EV_FCF = "ev_fcf"
    PB = "pb"
    PTBV = "ptbv"


METHOD_FAMILY: dict[ValuationMethod, ValuationFamily] = {
    ValuationMethod.DCF_FCFF: ValuationFamily.INTRINSIC,
    ValuationMethod.DCF_FCFE: ValuationFamily.INTRINSIC,
    ValuationMethod.RESIDUAL_INCOME: ValuationFamily.INTRINSIC,
    ValuationMethod.DIVIDEND_DISCOUNT: ValuationFamily.INTRINSIC,
    ValuationMethod.SUM_OF_THE_PARTS: ValuationFamily.INTRINSIC,
    ValuationMethod.PE: ValuationFamily.RELATIVE,
    ValuationMethod.EV_EBITDA: ValuationFamily.RELATIVE,
    ValuationMethod.EV_REVENUE: ValuationFamily.RELATIVE,
    ValuationMethod.EV_FCF: ValuationFamily.RELATIVE,
    ValuationMethod.PB: ValuationFamily.RELATIVE,
    ValuationMethod.PTBV: ValuationFamily.RELATIVE,
}


class MetricSpec(StrictModel):
    id: str = Field(pattern=METRIC_ID_PATTERN)
    name: str = Field(min_length=1)
    description: str = ""
    unit_kind: Literal["currency", "currency_per_share", "ratio", "count"]
    statement: Literal["income_statement", "balance_sheet", "cash_flow", "operating", "regulatory"]
    period_type: PeriodType
    # Ordered candidate concepts. Coverage is verified at extraction time, never assumed.
    xbrl_concepts: tuple[str, ...] = ()
    # Used when no reported value exists. Reported always wins; derived never overwrites it.
    derivation: str | None = None
    # For KPIs with no standard tag (company-defined operating metrics).
    extraction_hint: str | None = None
    # Data-quality metadata. Levels like assets or revenue cannot be negative; flows like cash change can.
    expected_sign: Literal["non_negative", "any"] = "any"
    # Whether sub-period values sum to the full period (enables Q4 = FY - 9M). Default: currency flows.
    additive: bool | None = None

    @property
    def is_additive(self) -> bool:
        if self.additive is not None:
            return self.additive
        return self.unit_kind == "currency" and self.period_type is PeriodType.DURATION

    @field_validator("xbrl_concepts")
    @classmethod
    def _concepts(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        for c in v:
            if not re.fullmatch(XBRL_CONCEPT_PATTERN, c):
                raise ValueError(f"invalid XBRL concept {c!r}; expected 'prefix:Name'")
        return v

    @field_validator("derivation")
    @classmethod
    def _derivation(cls, v: str | None) -> str | None:
        if v is not None:
            referenced_names(v)
        return v


class DriverSpec(StrictModel):
    id: str = Field(pattern=METRIC_ID_PATTERN)
    name: str = Field(min_length=1)
    description: str = ""
    affects: tuple[str, ...] = Field(min_length=1)
    # None = trend/assumption-driven. Otherwise expressed over metric ids only.
    formula: str | None = None

    @field_validator("formula")
    @classmethod
    def _formula(cls, v: str | None) -> str | None:
        if v is not None:
            referenced_names(v)
        return v


OPENING_SUFFIX = "__opening"  # `cash__opening` = instant value at the start of a duration period


class IdentityCheck(StrictModel):
    id: str = Field(pattern=METRIC_ID_PATTERN)
    description: str = Field(min_length=1)
    left: str
    right: str
    optional_terms: tuple[str, ...] = ()  # treated as zero when not reported (e.g. FX effect on cash)
    severity: Literal["error", "warning"] = "error"
    explanation_if_failed: str = ""

    @model_validator(mode="after")
    def _additive(self) -> IdentityCheck:
        for side in (self.left, self.right):
            if not is_additive(side):
                raise ValueError(f"identity check {self.id!r}: only + and - are allowed ({side!r}); "
                                 "rounding tolerances are defined for sums, not products or ratios")
        unknown = set(self.optional_terms) - self.names
        if unknown:
            raise ValueError(f"identity check {self.id!r}: optional terms not used in the check: {sorted(unknown)}")
        return self

    @property
    def names(self) -> frozenset[str]:
        return referenced_names(self.left) | referenced_names(self.right)

    @staticmethod
    def base_metric(name: str) -> str:
        return name[: -len(OPENING_SUFFIX)] if name.endswith(OPENING_SUFFIX) else name


AnalyticCategory = Literal[
    "growth", "profitability", "returns", "efficiency", "capital_intensity", "leverage", "liquidity",
    "credit", "capital", "per_share", "operating",
]


class AnalyticSpec(StrictModel):
    """A historical analytic computed on annual data. Kinds:
    level      -- a metric or analytic series as-is (for summaries and charts)
    growth     -- year-on-year change of `metric`
    ratio      -- numerator / denominator; instant (balance) terms in the denominator use `denominator_basis`
    expression -- `formula` over same-period values
    elasticity -- growth of `of` divided by growth of `relative_to` (e.g. degree of operating leverage)
    """

    id: str = Field(pattern=METRIC_ID_PATTERN)
    name: str = Field(min_length=1)
    description: str = ""
    category: AnalyticCategory
    kind: Literal["level", "growth", "ratio", "expression", "elasticity"]
    unit_kind: Literal["ratio", "currency", "currency_per_share", "multiple", "count"]
    metric: str | None = None
    numerator: str | None = None
    denominator: str | None = None
    denominator_basis: Literal["period_end", "average", "opening"] = "period_end"
    formula: str | None = None
    of: str | None = None
    relative_to: str | None = None

    @model_validator(mode="after")
    def _shape(self) -> AnalyticSpec:
        required = {
            "level": ("metric",), "growth": ("metric",), "ratio": ("numerator", "denominator"),
            "expression": ("formula",), "elasticity": ("of", "relative_to"),
        }[self.kind]
        all_fields = ("metric", "numerator", "denominator", "formula", "of", "relative_to")
        missing = [f for f in required if getattr(self, f) is None]
        extra = [f for f in all_fields if f not in required and getattr(self, f) is not None]
        if missing or extra:
            raise ValueError(f"analytic {self.id!r} ({self.kind}) requires {list(required)}"
                             + (f"; unexpected {extra}" if extra else ""))
        if self.denominator_basis != "period_end" and self.kind != "ratio":
            raise ValueError(f"analytic {self.id!r}: denominator_basis applies only to ratios")
        if self.kind == "growth" and self.unit_kind != "ratio":
            raise ValueError(f"analytic {self.id!r}: growth is a ratio")
        if self.kind == "elasticity" and self.unit_kind != "multiple":
            raise ValueError(f"analytic {self.id!r}: elasticity is a multiple")
        for expr in (self.numerator, self.denominator, self.formula):
            if expr is not None:
                referenced_names(expr)
        return self

    @property
    def references(self) -> frozenset[str]:
        names: set[str] = set()
        for expr in (self.numerator, self.denominator, self.formula):
            if expr:
                names |= referenced_names(expr)
        names |= {n for n in (self.metric, self.of, self.relative_to) if n}
        return frozenset(names)


class ChartSpec(StrictModel):
    id: str = Field(pattern=METRIC_ID_PATTERN)
    title: str = Field(min_length=1)
    kind: Literal["bar", "line"]
    series: tuple[str, ...] = Field(min_length=1, max_length=6)
    format: Literal["currency", "percent", "multiple", "number"]


class ValuationPolicy(StrictModel):
    preferred: tuple[ValuationMethod, ...] = ()
    excluded: dict[ValuationMethod, str] = Field(default_factory=dict)  # method -> reason

    @model_validator(mode="after")
    def _consistent(self) -> ValuationPolicy:
        overlap = set(self.preferred) & set(self.excluded)
        if overlap:
            raise ValueError(f"methods both preferred and excluded: {sorted(m.value for m in overlap)}")
        for method, reason in self.excluded.items():
            if not reason.strip():
                raise ValueError(f"excluded method {method.value} needs a reason")
        return self

    def methods_for(self, families: Iterable[ValuationFamily]) -> tuple[ValuationMethod, ...]:
        wanted = set(families)
        return tuple(m for m in self.preferred if METHOD_FAMILY[m] in wanted)


class ClassificationRule(StrictModel):
    sic_ranges: tuple[tuple[int, int], ...] = ()
    keywords: tuple[str, ...] = ()

    @field_validator("sic_ranges")
    @classmethod
    def _ranges(cls, v: tuple[tuple[int, int], ...]) -> tuple[tuple[int, int], ...]:
        for lo, hi in v:
            if not (100 <= lo <= hi <= 9999):
                raise ValueError(f"invalid SIC range [{lo}, {hi}]")
        return v

    def matches_sic(self, sic: int) -> bool:
        return any(lo <= sic <= hi for lo, hi in self.sic_ranges)


class IndustryFramework(StrictModel):
    name: str = Field(pattern=SLUG_PATTERN)
    display_name: str = Field(min_length=1)
    description: str = ""
    extends: str | None = Field(default=None, pattern=SLUG_PATTERN)
    classification: ClassificationRule = Field(default_factory=ClassificationRule)
    metrics: tuple[MetricSpec, ...] = ()
    remove_metrics: tuple[str, ...] = ()  # drop inherited metrics that don't apply (e.g. COGS for banks)
    drivers: tuple[DriverSpec, ...] = ()
    remove_drivers: tuple[str, ...] = ()
    checks: tuple[IdentityCheck, ...] = ()
    remove_checks: tuple[str, ...] = ()
    analytics: tuple[AnalyticSpec, ...] = ()
    remove_analytics: tuple[str, ...] = ()
    charts: tuple[ChartSpec, ...] = ()
    remove_charts: tuple[str, ...] = ()
    valuation: ValuationPolicy = Field(default_factory=ValuationPolicy)
    # Metrics the forecast must produce even when no driver names them (net income, equity, ...).
    # The driver graph expands these transitively through formulas and derivations.
    forecast_targets: tuple[str, ...] = ()
    remove_forecast_targets: tuple[str, ...] = ()
    report_sections: tuple[str, ...] = ()

    @field_validator("report_sections")
    @classmethod
    def _sections(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        for s in v:
            if not re.fullmatch(METRIC_ID_PATTERN, s):
                raise ValueError(f"invalid report section id {s!r}")
        if len(set(v)) != len(v):
            raise ValueError("duplicate report sections")
        return v

    @property
    def metric_ids(self) -> frozenset[str]:
        return frozenset(m.id for m in self.metrics)

    @property
    def analytic_ids(self) -> frozenset[str]:
        return frozenset(a.id for a in self.analytics)

    def analytic(self, analytic_id: str) -> AnalyticSpec:
        for a in self.analytics:
            if a.id == analytic_id:
                return a
        raise KeyError(f"framework {self.name!r} has no analytic {analytic_id!r}")

    def metric(self, metric_id: str) -> MetricSpec:
        for m in self.metrics:
            if m.id == metric_id:
                return m
        raise KeyError(f"framework {self.name!r} has no metric {metric_id!r}")

    def check_references(self) -> None:
        """Validate a *resolved* (inheritance-merged) framework. Raises ValueError."""
        metric_ids = [m.id for m in self.metrics]
        driver_ids = [d.id for d in self.drivers]
        for label, ids in (("metric", metric_ids), ("driver", driver_ids)):
            dupes = {i for i in ids if ids.count(i) > 1}
            if dupes:
                raise ValueError(f"duplicate {label} ids: {sorted(dupes)}")
        double = [i for i in metric_ids if "__" in i]
        if double:
            raise ValueError(f"metric ids may not contain '__' (reserved for {OPENING_SUFFIX}): {double}")
        check_ids = [c.id for c in self.checks]
        if len(set(check_ids)) != len(check_ids):
            raise ValueError("duplicate identity check ids")
        clash = set(metric_ids) & set(driver_ids)
        if clash:
            raise ValueError(f"ids used as both metric and driver: {sorted(clash)}")
        known = set(metric_ids)
        by_id = {m.id: m for m in self.metrics}
        graph: dict[str, set[str]] = {}
        for m in self.metrics:
            if m.derivation:
                names = referenced_names(m.derivation)
                unknown = names - known
                if unknown:
                    raise ValueError(f"metric {m.id!r} derivation references unknown metrics {sorted(unknown)}")
                mixed = sorted(n for n in names if by_id[n].period_type is not m.period_type)
                if mixed:
                    raise ValueError(
                        f"metric {m.id!r} ({m.period_type.value}) derivation uses {mixed} of a different period type; "
                        "flow-to-balance ratios need an explicit balance basis: define them as analytics"
                    )
                graph[m.id] = set(names)
        _assert_acyclic(graph)

        analytic_ids = [a.id for a in self.analytics]
        if len(set(analytic_ids)) != len(analytic_ids):
            raise ValueError("duplicate analytic ids")
        overlap = set(analytic_ids) & (known | set(driver_ids))
        if overlap:
            raise ValueError(f"analytic ids collide with metric or driver ids: {sorted(overlap)}")
        series_ids = known | set(analytic_ids)
        analytic_graph: dict[str, set[str]] = {}
        for a in self.analytics:
            unknown = a.references - series_ids
            if unknown:
                raise ValueError(f"analytic {a.id!r} references unknown metrics or analytics {sorted(unknown)}")
            if a.kind == "ratio" and a.denominator_basis != "period_end":
                balance_terms = [n for n in referenced_names(a.denominator) if n in by_id and by_id[n].period_type is PeriodType.INSTANT]
                if not balance_terms:
                    raise ValueError(f"analytic {a.id!r}: denominator_basis {a.denominator_basis!r} needs a balance-sheet (instant) term")
            analytic_graph[a.id] = set(a.references) & set(analytic_ids)
        _assert_acyclic(analytic_graph)
        chart_ids = [c.id for c in self.charts]
        if len(set(chart_ids)) != len(chart_ids):
            raise ValueError("duplicate chart ids")
        for c in self.charts:
            unknown_series = set(c.series) - series_ids
            if unknown_series:
                raise ValueError(f"chart {c.id!r} references unknown series {sorted(unknown_series)}")
        unknown_targets = set(self.forecast_targets) - known
        if unknown_targets:
            raise ValueError(f"forecast_targets reference unknown metrics {sorted(unknown_targets)}")
        if len(set(self.forecast_targets)) != len(self.forecast_targets):
            raise ValueError("duplicate forecast_targets")
        for d in self.drivers:
            unknown_affects = set(d.affects) - known
            if unknown_affects:
                raise ValueError(f"driver {d.id!r} affects unknown metrics {sorted(unknown_affects)}")
            if d.formula:
                unknown_inputs = referenced_names(d.formula) - series_ids
                if unknown_inputs:
                    raise ValueError(f"driver {d.id!r} formula references unknown metrics or analytics "
                                     f"{sorted(unknown_inputs)}")
        for check in self.checks:
            for name in check.names:
                base = IdentityCheck.base_metric(name)
                if base not in known:
                    raise ValueError(f"identity check {check.id!r} references unknown metric {base!r}")
                if name != base and self.metric(base).period_type is not PeriodType.INSTANT:
                    raise ValueError(f"identity check {check.id!r}: {OPENING_SUFFIX} applies only to instant metrics ({base})")
        if not self.valuation.preferred:
            raise ValueError("framework resolves to no preferred valuation methods")


def _assert_acyclic(graph: dict[str, set[str]]) -> None:
    state: dict[str, int] = {}  # 1 = visiting, 2 = done

    def visit(node: str, path: list[str]) -> None:
        if state.get(node) == 2:
            return
        if state.get(node) == 1:
            cycle = path[path.index(node):] + [node]
            raise ValueError(f"circular derivation: {' -> '.join(cycle)}")
        state[node] = 1
        for dep in graph.get(node, ()):
            visit(dep, path + [node])
        state[node] = 2

    for n in graph:
        visit(n, [])
