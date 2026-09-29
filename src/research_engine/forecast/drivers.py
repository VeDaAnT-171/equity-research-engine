"""Driver graph: how each metric in the forecast gets its value, and in what order.

The framework declares drivers as data (`DriverSpec.affects` and an optional `formula`).
This module turns those declarations into an evaluation plan over metrics.

How a metric is produced, in order:

1. **A driver formula that affects it.** This is the forecasting model the framework declares.
2. **Its own framework derivation**, re-applied to projected inputs, so a metric that is the sum
   of its parts in history stays the sum of its parts in the forecast.
3. **Exogenous** otherwise: the analyst sets it. Ratio-valued metrics (margins, capital ratios)
   take the level as the assumption; everything else takes a growth rate, because growing a
   margin is meaningless and levelling a revenue line is useless.

**Cycle breaking.** Historical derivations and forecast drivers legitimately run in opposite
directions. A bank framework both derives `net_interest_margin = net_interest_income /
average_interest_earning_assets` (how you measure a margin from actuals) and drives
`net_interest_income = average_interest_earning_assets * net_interest_margin` (how you forecast
income). Applying both inside one period is circular. Forward, the margin is the assumption and
income is the output, so when a derivation would close a cycle, the metric in that cycle which
another driver formula models *with* is demoted to exogenous. The demotion is recorded and shown
in the forecast report; it is never silent.

**Fallbacks.** A driver formula is only a model if the company reports what it models with. A
bank's net interest income is earning assets times margin, but average interest-earning assets
has no standard XBRL element, so for any filer read from structured data alone that formula has
nothing to multiply and the whole income statement below it goes unprojected. A driver may
declare `fallback: trend`: when a metric its formula needs has no base-year value for this
company, the metrics it affects are projected on their own history instead. The substitution is
recorded on the graph, and the projector marks every value that depends on it, however far
downstream — a revenue line built from a trend-extrapolated income line is not the modelled
revenue line, and a reader has to be able to tell.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass, field, replace

from ..errors import ForecastError
from ..expressions import referenced_names
from ..schemas.forecast import ForecastMethod
from ..schemas.framework import IndustryFramework
from .assumptions import growth_key, level_key, rate_key


@dataclass(frozen=True)
class ProjectionRule:
    """How one metric is produced in a forecast year."""

    metric_id: str
    method: ForecastMethod                   # driver_formula | derivation | growth | level
    formula: str                             # recorded on every value produced
    driver_id: str | None = None
    metric_inputs: tuple[str, ...] = ()      # same-year metric dependencies
    assumption_keys: tuple[str, ...] = ()    # exogenous inputs resolved from the registry
    reason: str = ""                         # why this rule and not another
    fallback_for: str | None = None          # the declared driver this rule stands in for

    @property
    def is_exogenous(self) -> bool:
        return self.method in ("growth", "level")


@dataclass
class DriverGraph:
    rules: dict[str, ProjectionRule]
    order: tuple[str, ...]
    targets: tuple[str, ...]
    assumption_keys: tuple[str, ...] = ()
    unresolved: dict[str, str] = field(default_factory=dict)
    demoted: dict[str, str] = field(default_factory=dict)
    fallbacks: dict[str, str] = field(default_factory=dict)  # metric -> why its driver was not used
    fallback_inputs: dict[str, tuple[str, ...]] = field(default_factory=dict)  # metric -> missing inputs

    def rule(self, metric_id: str) -> ProjectionRule:
        return self.rules[metric_id]


def _find_cycle(rules: dict[str, ProjectionRule]) -> list[str] | None:
    state: dict[str, int] = {}

    def visit(node: str, path: list[str]) -> list[str] | None:
        if state.get(node) == 2:
            return None
        if state.get(node) == 1:
            return path[path.index(node):] + [node]
        state[node] = 1
        for dep in rules[node].metric_inputs:
            if dep in rules:
                found = visit(dep, path + [node])
                if found:
                    return found
        state[node] = 2
        return None

    for node in sorted(rules):
        found = visit(node, [])
        if found:
            return found
    return None


def _topological(rules: dict[str, ProjectionRule]) -> tuple[str, ...]:
    state: dict[str, int] = {}
    order: list[str] = []

    def visit(node: str) -> None:
        if state.get(node) == 2:
            return
        state[node] = 1
        for dep in rules[node].metric_inputs:
            if dep in rules:
                visit(dep)
        state[node] = 2
        order.append(node)

    for node in sorted(rules):
        visit(node)
    return tuple(order)


def build_driver_graph(framework: IndustryFramework, *, extra_targets: tuple[str, ...] = (),
                       available: Collection[str] | None = None) -> DriverGraph:
    """Plan how every forecast target is produced.

    `available` is the set of metrics this company has a base-year value for. Without it the plan
    is the framework's alone and no fallback is taken; with it, a driver whose formula needs a
    metric outside that set uses its declared fallback, if it has one.
    """
    metrics = {m.id: m for m in framework.metrics}
    analytic_ids = framework.analytic_ids

    fallen_back: dict[str, tuple[str, str]] = {}  # metric -> (driver id, reason)
    missing_inputs: dict[str, tuple[str, ...]] = {}
    formula_by_metric: dict[str, tuple[str, str]] = {}
    consumed_by_formula: set[str] = set()
    for d in framework.drivers:
        if not d.formula:
            continue
        needs = sorted(n for n in referenced_names(d.formula) if n in metrics)
        missing = [n for n in needs if available is not None and n not in available]
        if missing and d.fallback == "trend":
            reason = (f"fallback: driver {d.id!r} (`{d.formula}`) needs {', '.join(missing)}, which "
                      "this company does not report for the base year, so the metric is projected on "
                      "its own history instead")
            for target in d.affects:
                fallen_back[target] = (d.id, reason)
                missing_inputs[target] = tuple(missing)
            continue
        consumed_by_formula |= set(needs)
        for target in d.affects:
            formula_by_metric[target] = (d.id, d.formula)

    driver_of: dict[str, str] = {}
    for d in framework.drivers:
        for target in d.affects:
            driver_of.setdefault(target, d.id)

    def exogenous(metric_id: str, reason: str) -> ProjectionRule:
        spec = metrics[metric_id]
        if spec.unit_kind == "ratio":
            key = level_key(metric_id)
            return ProjectionRule(metric_id=metric_id, method="level", formula=f"{key} (assumed level)",
                                  driver_id=driver_of.get(metric_id), assumption_keys=(key,),
                                  reason=f"{reason}; ratio-valued, so the assumption is the level")
        key = growth_key(metric_id)
        return ProjectionRule(metric_id=metric_id, method="growth", formula=f"{metric_id}[t-1] * (1 + {key})",
                              driver_id=driver_of.get(metric_id), assumption_keys=(key,), reason=reason)

    def rule_for(metric_id: str, demoted: dict[str, str]) -> ProjectionRule:
        spec = metrics[metric_id]
        if metric_id in demoted:
            return exogenous(metric_id, demoted[metric_id])
        if metric_id in fallen_back:
            driver_id, reason = fallen_back[metric_id]
            return replace(exogenous(metric_id, reason), fallback_for=driver_id)
        if metric_id in formula_by_metric:
            driver_id, formula = formula_by_metric[metric_id]
            names = sorted(referenced_names(formula))
            return ProjectionRule(
                metric_id=metric_id, method="driver_formula", formula=formula, driver_id=driver_id,
                metric_inputs=tuple(n for n in names if n in metrics),
                assumption_keys=tuple(rate_key(n) for n in names if n in analytic_ids),
                reason=f"driver {driver_id!r}",
            )
        if spec.derivation:
            return ProjectionRule(
                metric_id=metric_id, method="derivation", formula=spec.derivation,
                driver_id=driver_of.get(metric_id),
                metric_inputs=tuple(sorted(referenced_names(spec.derivation))),
                reason="framework derivation, re-applied to projected inputs",
            )
        if metric_id in consumed_by_formula:
            return exogenous(metric_id, "driver input: another driver's formula models with this metric")
        return exogenous(metric_id, f"trend driver {driver_of[metric_id]!r}" if metric_id in driver_of
                         else "no driver or derivation produces this metric")

    targets: list[str] = []
    for d in framework.drivers:
        targets += [m for m in d.affects if m not in targets]
    for m in (*framework.forecast_targets, *extra_targets):
        if m not in targets:
            targets.append(m)
    unresolved = {m: "not a metric in this framework" for m in targets if m not in metrics}
    known_targets = [m for m in targets if m in metrics]

    def closure(demoted: dict[str, str]) -> dict[str, ProjectionRule]:
        rules: dict[str, ProjectionRule] = {}
        queue = list(known_targets)
        while queue:
            metric_id = queue.pop()
            if metric_id in rules or metric_id not in metrics:
                continue
            rule = rule_for(metric_id, demoted)
            rules[metric_id] = rule
            queue.extend(dep for dep in rule.metric_inputs if dep not in rules and dep in metrics)
        return rules

    demoted: dict[str, str] = {}
    for _ in range(len(metrics) + 1):
        rules = closure(demoted)
        cycle = _find_cycle(rules)
        if cycle is None:
            break
        members = [m for m in dict.fromkeys(cycle) if rules[m].method == "derivation"]
        # Prefer the metric another driver formula models with, and among those a ratio:
        # forward, a margin is the assumption and the income line is the output.
        candidates = [m for m in members if m in consumed_by_formula] or members
        if not candidates:
            raise ForecastError(
                "circular forecast dependency with no derivation to break it: " + " -> ".join(cycle)
            )
        candidates.sort(key=lambda m: (metrics[m].unit_kind != "ratio", m not in consumed_by_formula, m))
        chosen = candidates[0]
        demoted[chosen] = (
            f"derivation `{metrics[chosen].derivation}` would be circular in a forecast "
            f"({' -> '.join(cycle)}), so this metric is an assumption and the driver it feeds is the output"
        )
    else:  # pragma: no cover - the loop terminates well before this bound
        raise ForecastError("could not break forecast dependency cycles")

    order = _topological(rules)
    keys = sorted({k for r in rules.values() for k in r.assumption_keys})
    fallbacks = {m: fallen_back[m][1] for m in rules if m in fallen_back}
    return DriverGraph(rules=rules, order=order, targets=tuple(known_targets),
                       assumption_keys=tuple(keys), unresolved=unresolved, demoted=demoted,
                       fallbacks=fallbacks,
                       fallback_inputs={m: missing_inputs[m] for m in fallbacks})
