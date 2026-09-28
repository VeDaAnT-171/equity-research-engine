from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
from enum import Enum


class Severity(str, Enum):
    ERROR = "error"      # an accounting identity or hard constraint is violated
    WARNING = "warning"  # likely extraction/definition problem; needs review
    INFO = "info"        # unusual but plausible; context for the analyst


@dataclass
class QualityIssue:
    check: str
    severity: Severity
    message: str
    metric_id: str | None = None
    period: str | None = None
    fact_ids: tuple[str, ...] = ()
    details: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["severity"] = self.severity.value
        return d


@dataclass
class CheckSummary:
    check: str
    description: str
    passed: int = 0
    failed: int = 0
    not_evaluable: int = 0  # required inputs missing: absence of data is never reported as a pass

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class QualityReport:
    company_id: str
    framework: str
    historical_years: int
    issues: list[QualityIssue] = field(default_factory=list)
    checks: dict[str, CheckSummary] = field(default_factory=dict)
    derivations: Counter = field(default_factory=Counter)
    coverage: dict[str, dict[str, str]] = field(default_factory=dict)  # metric -> fiscal year label -> R/D

    def summary(self, check: str, description: str) -> CheckSummary:
        return self.checks.setdefault(check, CheckSummary(check, description))

    def add(self, issue: QualityIssue) -> None:
        self.issues.append(issue)

    def counts(self) -> dict[str, int]:
        c = Counter(i.severity.value for i in self.issues)
        return {s.value: c.get(s.value, 0) for s in Severity}

    def to_dict(self) -> dict:
        order = {Severity.ERROR: 0, Severity.WARNING: 1, Severity.INFO: 2}
        return {
            "company_id": self.company_id,
            "framework": self.framework,
            "historical_years": self.historical_years,
            "counts": self.counts(),
            "checks": {k: v.to_dict() for k, v in sorted(self.checks.items())},
            "derivations": dict(sorted(self.derivations.items())),
            "issues": [i.to_dict() for i in sorted(self.issues, key=lambda i: (order[i.severity], i.check, i.metric_id or "", i.period or ""))],
            "coverage": self.coverage,
        }
