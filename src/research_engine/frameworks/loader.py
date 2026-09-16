"""Loads industry frameworks from YAML and resolves single inheritance."""

from __future__ import annotations

import hashlib
from pathlib import Path

import yaml
from pydantic import ValidationError

from ..errors import FrameworkError, format_validation_error
from ..schemas.framework import IndustryFramework, ValuationPolicy


def _merge_by_id(parent_items, child_items, removed):
    merged = {item.id: item for item in parent_items if item.id not in removed}
    for item in child_items:
        merged[item.id] = item
    return tuple(merged.values())


def _merge(parent: IndustryFramework, child: IndustryFramework) -> IndustryFramework:
    unknown_removals = (
        (set(child.remove_metrics) - parent.metric_ids)
        | (set(child.remove_drivers) - {d.id for d in parent.drivers})
        | (set(child.remove_checks) - {c.id for c in parent.checks})
        | (set(child.remove_analytics) - parent.analytic_ids)
        | (set(child.remove_charts) - {c.id for c in parent.charts})
    )
    if unknown_removals:
        raise FrameworkError(f"{child.name}: cannot remove items not defined by parent: {sorted(unknown_removals)}")
    child_pref = set(child.valuation.preferred)
    excluded = {m: r for m, r in parent.valuation.excluded.items() if m not in child_pref}
    excluded.update(child.valuation.excluded)
    preferred = child.valuation.preferred or tuple(
        m for m in parent.valuation.preferred if m not in child.valuation.excluded
    )
    return IndustryFramework(
        name=child.name,
        display_name=child.display_name,
        description=child.description or parent.description,
        extends=child.extends,
        classification=child.classification,  # classification is never inherited
        metrics=_merge_by_id(parent.metrics, child.metrics, set(child.remove_metrics)),
        drivers=_merge_by_id(parent.drivers, child.drivers, set(child.remove_drivers)),
        checks=_merge_by_id(parent.checks, child.checks, set(child.remove_checks)),
        analytics=_merge_by_id(parent.analytics, child.analytics, set(child.remove_analytics)),
        charts=_merge_by_id(parent.charts, child.charts, set(child.remove_charts)),
        valuation=ValuationPolicy(preferred=preferred, excluded=excluded),
        report_sections=child.report_sections or parent.report_sections,
    )


def framework_fingerprint(framework: IndustryFramework) -> str:
    """Hash of the resolved framework. Stages compare it to detect frameworks edited between runs."""
    return hashlib.sha256(framework.model_dump_json().encode()).hexdigest()


class FrameworkRegistry:
    def __init__(self, directory: str | Path):
        self.directory = Path(directory)
        if not self.directory.is_dir():
            raise FrameworkError(f"framework directory not found: {self.directory}")
        self._raw: dict[str, IndustryFramework] = {}
        self._resolved: dict[str, IndustryFramework] = {}
        for path in sorted(self.directory.glob("*.yaml")):
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8"))
            except yaml.YAMLError as exc:
                raise FrameworkError(f"{path.name}: invalid YAML: {exc}") from None
            if not isinstance(data, dict):
                raise FrameworkError(f"{path.name}: expected a mapping at the top level")
            try:
                framework = IndustryFramework.model_validate(data)
            except ValidationError as exc:
                raise FrameworkError(f"{path.name}: invalid framework\n{format_validation_error(exc)}") from None
            if framework.name != path.stem:
                raise FrameworkError(f"{path.name}: framework name {framework.name!r} must match file name {path.stem!r}")
            self._raw[framework.name] = framework
        if not self._raw:
            raise FrameworkError(f"no framework YAML files found in {self.directory}")

    def names(self) -> list[str]:
        return sorted(self._raw)

    def raw(self, name: str) -> IndustryFramework:
        if name not in self._raw:
            raise FrameworkError(f"unknown industry framework {name!r}; available: {', '.join(self.names())}")
        return self._raw[name]

    def get(self, name: str) -> IndustryFramework:
        if name in self._resolved:
            return self._resolved[name]
        self.raw(name)
        chain: list[str] = []
        current: str | None = name
        while current is not None:
            if current in chain:
                raise FrameworkError(f"framework inheritance cycle: {' -> '.join(chain + [current])}")
            if current not in self._raw:
                raise FrameworkError(f"framework {chain[-1]!r} extends unknown framework {current!r}")
            chain.append(current)
            current = self._raw[current].extends
        resolved = self._raw[chain[-1]]
        if any((resolved.remove_metrics, resolved.remove_drivers, resolved.remove_checks,
                resolved.remove_analytics, resolved.remove_charts)):
            raise FrameworkError(f"{resolved.name}: a root framework cannot remove inherited items")
        for child in reversed(chain[:-1]):
            try:
                resolved = _merge(resolved, self._raw[child])
            except ValidationError as exc:
                raise FrameworkError(f"{child}: invalid merged framework\n{format_validation_error(exc)}") from None
        try:
            resolved.check_references()
        except ValueError as exc:
            raise FrameworkError(f"{name}: {exc}") from None
        self._resolved[name] = resolved
        return resolved

    def validate_all(self) -> dict[str, IndustryFramework]:
        return {name: self.get(name) for name in self.names()}

    def candidates_for_sic(self, sic: int) -> list[str]:
        """Frameworks whose SIC rules match. Candidate only; full classification weighs
        filing evidence (segments, revenue mix) and is a later phase."""
        return [n for n in self.names() if self._raw[n].classification.matches_sic(sic)]
