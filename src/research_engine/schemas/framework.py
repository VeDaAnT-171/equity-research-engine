"""Industry framework schema: metrics, drivers and valuation policy as data, not code."""

from __future__ import annotations

import re
from enum import Enum
from typing import Iterable, Literal, Optional

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
    derivation: Optional[str] = None
    # For KPIs with no standard tag (company-defined operating metrics).
    extraction_hint: Optional[str] = None
    # Data-quality metadata. Levels like assets or revenue cannot be negative; flows like cash change can.
    expected_sign: Literal["non_negative", "any"] = "any"
    # Whether sub-period values sum to the full period (enables Q4 = FY - 9M). Default: currency flows.
    additive: Optional[bool] = None

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
    def _derivation(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            referenced_names(v)
        return v


class DriverSpec(StrictModel):
    id: str = Field(pattern=METRIC_ID_PATTERN)
    name: str = Field(min_length=1)
    description: str = ""
    affects: tuple[str, ...] = Field(min_length=1)
    # None = trend/assumption-driven. Otherwise expressed over metric ids only.
    formula: Optional[str] = None

    @field_validator("formula")
    @classmethod
    def _formula(cls, v: Optional[str]) -> Optional[str]:
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
    def _additive(self) -> "IdentityCheck":
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


class ValuationPolicy(StrictModel):
    preferred: tuple[ValuationMethod, ...] = ()
    excluded: dict[ValuationMethod, str] = Field(default_factory=dict)  # method -> reason

    @model_validator(mode="after")
    def _consistent(self) -> "ValuationPolicy":
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
    extends: Optional[str] = Field(default=None, pattern=SLUG_PATTERN)
    classification: ClassificationRule = Field(default_factory=ClassificationRule)
    metrics: tuple[MetricSpec, ...] = ()
    remove_metrics: tuple[str, ...] = ()  # drop inherited metrics that don't apply (e.g. COGS for banks)
    drivers: tuple[DriverSpec, ...] = ()
    remove_drivers: tuple[str, ...] = ()
    checks: tuple[IdentityCheck, ...] = ()
    remove_checks: tuple[str, ...] = ()
    valuation: ValuationPolicy = Field(default_factory=ValuationPolicy)
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
        graph: dict[str, set[str]] = {}
        for m in self.metrics:
            if m.derivation:
                names = referenced_names(m.derivation)
                unknown = names - known
                if unknown:
                    raise ValueError(f"metric {m.id!r} derivation references unknown metrics {sorted(unknown)}")
                graph[m.id] = set(names)
        _assert_acyclic(graph)
        for d in self.drivers:
            unknown = set(d.affects) - known
            if unknown:
                raise ValueError(f"driver {d.id!r} affects unknown metrics {sorted(unknown)}")
            if d.formula:
                unknown = referenced_names(d.formula) - known
                if unknown:
                    raise ValueError(f"driver {d.id!r} formula references unknown metrics {sorted(unknown)}")
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
            raise ValueError(f"circular metric derivation: {' -> '.join(cycle)}")
        state[node] = 1
        for dep in graph.get(node, ()):
            visit(dep, path + [node])
        state[node] = 2

    for n in graph:
        visit(n, [])
