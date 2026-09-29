"""Assumption registry: engine-seeded history plus analyst-authored overrides.

Two sources, one namespace:

* The engine seeds a `historical` assumption for every exogenous input the driver graph needs,
  computed from the facts and analytics Phase 4 already verified, carrying the fact ids it used.
* The analyst supplies `management_guidance`, `consensus`, `analyst_assumption` and `scenario`
  values in `companies/<id>/assumptions.yaml`. Guidance and consensus cannot exist without a
  cited document (enforced by the Assumption schema), and the analyst file may not declare
  `historical` or `derived` assumptions: those are the engine's to compute, never to claim.

Resolution is a fixed precedence ladder, highest first:

    scenario > analyst_assumption > management_guidance > consensus > historical/derived

The analyst outranks guidance deliberately: deciding whether to believe management is the job.
Within one rung, an assumption pinned to a fiscal year beats one that applies to every year.
Every projected value records which assumption id and which rung it used.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

import yaml
from pydantic import ValidationError

from ..errors import ConfigError, format_validation_error
from ..growth import growth_refusal
from ..schemas.analytics import AnalyticValue
from ..schemas.assumption import Assumption, AssumptionType
from ..schemas.financial import FinancialFact, FiscalPeriodCode
from ..schemas.forecast import BASE_SCENARIO, ScenarioSpec

# Trailing fiscal years used to seed a historical assumption. A single year is noise;
# a long window buries regime changes. Three is a stated convention, not a discovery.
SEED_WINDOW_YEARS = 3

# The window is anchored to the forecast's base year, not to whichever years a metric happens to
# have. Taking the last three *observations* instead reaches back across every gap: one bank's
# loans series ended in FY2015, and a growth rate measured over FY2013-FY2015 was still seeded and
# would have compounded to FY2030 had the base year value survived. The same reach kept a decaying
# operating-cash-flow projection alive after the sign-crossing pairs it rested on were refused —
# the seeder simply stepped back over the refusals and stitched FY2019, FY2022 and FY2023 together
# into something it described as three observations. How *many* of the window's years must be
# usable is deliberately not a rule here: a company with two years of history has a measurable,
# fragile growth rate, and refusing it would be a judgement about sample size rather than about
# whether the measurement means anything.


def _window(base_year: int) -> range:
    """The fiscal years a historical assumption may be measured over."""
    return range(base_year - SEED_WINDOW_YEARS + 1, base_year + 1)

PRIORITY: dict[AssumptionType, int] = {
    AssumptionType.SCENARIO: 0,
    AssumptionType.ANALYST_ASSUMPTION: 1,
    AssumptionType.MANAGEMENT_GUIDANCE: 2,
    AssumptionType.CONSENSUS: 3,
    AssumptionType.HISTORICAL: 4,
    AssumptionType.DERIVED: 4,
}

# Types the analyst may declare. `historical`/`derived` are engine-computed: an analyst
# cannot cite fact ids for them, and letting them try invites invented provenance.
ANALYST_DECLARABLE = frozenset({
    AssumptionType.MANAGEMENT_GUIDANCE, AssumptionType.CONSENSUS,
    AssumptionType.ANALYST_ASSUMPTION, AssumptionType.SCENARIO,
})


def growth_key(metric_id: str) -> str:
    return f"growth.{metric_id}"


def level_key(metric_id: str) -> str:
    return f"level.{metric_id}"


def rate_key(analytic_id: str) -> str:
    return f"rate.{analytic_id}"


class AssumptionUnavailable(Exception):
    """No assumption resolves for a key, or the one that resolves has no value set."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _median(values: list[Decimal]) -> Decimal:
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    return ordered[mid] if n % 2 else (ordered[mid - 1] + ordered[mid]) / 2


@dataclass
class SeedInput:
    """Annual series for one exogenous name, with the fact ids behind each year."""

    values: dict[int, Decimal] = field(default_factory=dict)
    fact_ids: dict[int, tuple[str, ...]] = field(default_factory=dict)


def annual_metric_series(facts: Iterable[FinancialFact]) -> dict[str, SeedInput]:
    """Full-year reported and derived values by metric, with the fact id for each year."""
    out: dict[str, SeedInput] = {}
    for f in facts:
        if f.period.fiscal_period is not FiscalPeriodCode.FY:
            continue
        seed = out.setdefault(f.metric_id, SeedInput())
        seed.values[f.period.fiscal_year] = f.value
        seed.fact_ids[f.period.fiscal_year] = (f.fact_id,)
    return out


def annual_analytic_series(values: Iterable[AnalyticValue]) -> dict[str, SeedInput]:
    """Analytic values by id, resolving each one's lineage back to the facts underneath it."""
    values = list(values)
    by_value_id = {v.value_id: v for v in values}

    def facts_for(value: AnalyticValue, seen: set[str] | None = None) -> tuple[str, ...]:
        seen = seen if seen is not None else set()
        if value.value_id in seen:
            return ()
        seen.add(value.value_id)
        ids = list(value.input_fact_ids)
        for vid in value.input_value_ids:
            parent = by_value_id.get(vid)
            if parent is not None:
                ids += [f for f in facts_for(parent, seen) if f not in ids]
        return tuple(dict.fromkeys(ids))

    out: dict[str, SeedInput] = {}
    for v in values:
        seed = out.setdefault(v.analytic_id, SeedInput())
        seed.values[v.fiscal_year] = v.value
        seed.fact_ids[v.fiscal_year] = facts_for(v)
    return out


def seed_growth(company_id: str, metric_id: str, seed: SeedInput, *, base_year: int) -> Assumption | None:
    """Median year-on-year growth over the window ending at the base year.

    Returns None unless the window itself holds enough usable pairs. Refusing to seed is the
    honest outcome for a series that is stale, or that spends the recent past crossing zero: the
    forecast then reports the metric as not projected, which is true, rather than carrying a rate
    assembled from whichever old years happened to survive.
    """
    pairs: list[tuple[int, Decimal]] = []
    for y in _window(base_year):
        prior = seed.values.get(y - 1)
        current = seed.values.get(y)
        # Same test the historical analytics apply, for the same reason: a median taken over
        # pairs that straddle zero is not a trend, and this one gets compounded forward.
        if current is None or growth_refusal(prior, current) is not None:
            continue
        assert prior is not None  # growth_refusal rejects None
        pairs.append((y, current / prior - 1))
    used = pairs
    if not used:
        return None
    fact_ids: list[str] = []
    for y, _ in used:
        for fid in (*seed.fact_ids.get(y, ()), *seed.fact_ids.get(y - 1, ())):
            if fid not in fact_ids:
                fact_ids.append(fid)
    if not fact_ids:
        return None
    span = f"FY{used[0][0]}-FY{used[-1][0]}"
    return Assumption(
        assumption_id=growth_key(metric_id), company_id=company_id,
        description=f"Median year-on-year growth of {metric_id} over {span} ({len(used)} observations)",
        value=_median([g for _, g in used]), unit="ratio", type=AssumptionType.HISTORICAL,
        source_fact_ids=tuple(fact_ids),
    )


def seed_level(company_id: str, name: str, seed: SeedInput, *, key: str, unit: str,
               label: str, base_year: int) -> Assumption | None:
    """Median level over the window ending at the base year.

    A level is a state rather than a rate, so one observation inside the window is usable where
    one growth pair is not. What is never usable is a state read off a year the company has long
    since left behind: a credit-loss rate last observable in FY2015 is not this bank's credit-loss
    rate, and seeding it would put a decade-old number into a five-year projection.
    """
    years = [y for y in _window(base_year) if y in seed.values]
    if not years:
        return None
    fact_ids: list[str] = []
    for y in years:
        for fid in seed.fact_ids.get(y, ()):
            if fid not in fact_ids:
                fact_ids.append(fid)
    if not fact_ids:
        return None
    span = f"FY{years[0]}-FY{years[-1]}" if len(years) > 1 else f"FY{years[0]}"
    return Assumption(
        assumption_id=key, company_id=company_id,
        description=f"Median {label} {name} over {span} ({len(years)} observations)",
        value=_median([seed.values[y] for y in years]), unit=unit, type=AssumptionType.HISTORICAL,
        source_fact_ids=tuple(fact_ids),
    )


class AssumptionSet:
    """Every assumption available to a forecast, resolvable by key, year and scenario."""

    def __init__(self, assumptions: Iterable[Assumption], scenarios: Iterable[ScenarioSpec] = ()):
        self._by_key: dict[str, list[Assumption]] = {}
        for a in assumptions:
            self._by_key.setdefault(a.assumption_id, []).append(a)
        self.scenarios: dict[str, ScenarioSpec] = {
            BASE_SCENARIO: ScenarioSpec(id=BASE_SCENARIO, name="Base case",
                                        description="Built from the company's recent history and any analyst assumptions, with no scenario adjustments.")
        }
        for s in scenarios:
            self.scenarios[s.id] = s

    @property
    def all(self) -> list[Assumption]:
        return [a for group in self._by_key.values() for a in group]

    def keys(self) -> set[str]:
        return set(self._by_key)

    def candidates(self, key: str, year: int, scenario: str) -> list[Assumption]:
        period = f"FY{year}"
        out = []
        for a in self._by_key.get(key, ()):
            if a.type is AssumptionType.SCENARIO and a.scenario != scenario:
                continue
            if a.period is not None and a.period != period:
                continue
            out.append(a)
        return sorted(out, key=lambda a: (PRIORITY[a.type], 0 if a.period else 1))

    def resolve(self, key: str, year: int, scenario: str) -> Assumption:
        found = self.candidates(key, year, scenario)
        if not found:
            raise AssumptionUnavailable(f"assumption_missing:{key}")
        best = found[0]
        if not best.is_set:
            raise AssumptionUnavailable(f"assumption_unset:{key}")
        return best

    def merge(self, others: Iterable[Assumption]) -> AssumptionSet:
        return AssumptionSet([*self.all, *others], self.scenarios.values())


def load_assumptions_file(path: Path, *, company_id: str) -> tuple[list[Assumption], list[ScenarioSpec]]:
    """Read `companies/<id>/assumptions.yaml`. A missing file is not an error: the engine
    then forecasts on seeded history alone, which is a legitimate (and clearly labelled) base case."""
    path = Path(path)
    if not path.is_file():
        return [], []
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path}: invalid YAML: {exc}") from None
    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: expected a mapping at the top level")
    unknown = set(raw) - {"schema_version", "scenarios", "assumptions"}
    if unknown:
        raise ConfigError(f"{path}: unknown keys {sorted(unknown)}")

    scenarios: list[ScenarioSpec] = []
    for i, entry in enumerate(raw.get("scenarios") or []):
        try:
            scenarios.append(ScenarioSpec(**entry))
        except (ValidationError, TypeError) as exc:
            detail = format_validation_error(exc) if isinstance(exc, ValidationError) else str(exc)
            raise ConfigError(f"{path}: scenarios[{i}] is invalid:\n{detail}") from None
    declared = {s.id for s in scenarios} | {BASE_SCENARIO}
    if len(declared) != len(scenarios) + 1 - (1 if BASE_SCENARIO in {s.id for s in scenarios} else 0):
        raise ConfigError(f"{path}: duplicate scenario ids")

    assumptions: list[Assumption] = []
    for i, entry in enumerate(raw.get("assumptions") or []):
        if not isinstance(entry, dict):
            raise ConfigError(f"{path}: assumptions[{i}] must be a mapping")
        payload = dict(entry)
        payload["assumption_id"] = payload.pop("id", None)
        payload.setdefault("company_id", company_id)
        if payload.get("company_id") != company_id:
            raise ConfigError(f"{path}: assumptions[{i}] is for another company ({payload['company_id']!r})")
        try:
            a = Assumption(**payload)
        except (ValidationError, TypeError) as exc:
            detail = format_validation_error(exc) if isinstance(exc, ValidationError) else str(exc)
            raise ConfigError(f"{path}: assumptions[{i}] is invalid:\n{detail}") from None
        if a.type not in ANALYST_DECLARABLE:
            raise ConfigError(
                f"{path}: assumptions[{i}] ({a.assumption_id}) declares type {a.type.value!r}; "
                "'historical' and 'derived' assumptions are computed by the engine from verified facts "
                "and cannot be authored by hand"
            )
        if a.type is AssumptionType.SCENARIO and a.scenario not in declared:
            raise ConfigError(
                f"{path}: assumptions[{i}] ({a.assumption_id}) binds to undeclared scenario {a.scenario!r}; "
                f"declared: {sorted(declared)}"
            )
        assumptions.append(a)

    seen: set[tuple[str, str | None, str | None, AssumptionType]] = set()
    for a in assumptions:
        key = (a.assumption_id, a.period, a.scenario, a.type)
        if key in seen:
            raise ConfigError(
                f"{path}: duplicate assumption {a.assumption_id!r} "
                f"(type {a.type.value}, period {a.period or 'all years'}, scenario {a.scenario or 'none'})"
            )
        seen.add(key)
    return assumptions, scenarios
