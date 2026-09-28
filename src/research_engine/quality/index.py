"""Lookup structure over the current fact set (latest reported values plus derived facts)."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from datetime import date, timedelta

from ..errors import ExtractionError
from ..schemas.financial import FinancialFact, Period, PeriodType
from ..schemas.framework import MetricSpec

# Opening balances: the instant one day before a period starts; 52/53-week calendars can leave a short gap.
OPENING_WINDOW_DAYS = 7


class FactIndex:
    def __init__(self, facts: Iterable[FinancialFact] = ()):
        self._duration: dict[tuple[str, date, date], FinancialFact] = {}
        self._instant: dict[tuple[str, date], FinancialFact] = {}
        self._by_metric: dict[str, list[FinancialFact]] = defaultdict(list)
        for fact in facts:
            self.add(fact)

    def add(self, fact: FinancialFact) -> None:
        p = fact.period
        if p.period_type is PeriodType.DURATION:
            duration_key = (fact.metric_id, p.start, p.end)
            existing = self._duration.get(duration_key)
            if existing is not None:
                raise ExtractionError(f"duplicate current fact for {fact.metric_id} {p.label} "
                                      f"({existing.fact_id}, {fact.fact_id})")
            self._duration[duration_key] = fact
        else:
            instant_key = (fact.metric_id, p.end)
            existing = self._instant.get(instant_key)
            if existing is not None:
                raise ExtractionError(f"duplicate current fact for {fact.metric_id} {p.label} "
                                      f"({existing.fact_id}, {fact.fact_id})")
            self._instant[instant_key] = fact
        self._by_metric[fact.metric_id].append(fact)

    def facts(self, metric_id: str) -> list[FinancialFact]:
        return sorted(self._by_metric.get(metric_id, []), key=lambda f: (f.period.end, f.period.start or f.period.end))

    def all(self) -> list[FinancialFact]:
        return [f for facts in self._by_metric.values() for f in facts]

    def duration(self, metric_id: str, start: date, end: date) -> FinancialFact | None:
        return self._duration.get((metric_id, start, end))

    def instant(self, metric_id: str, end: date) -> FinancialFact | None:
        return self._instant.get((metric_id, end))

    def at(self, spec: MetricSpec, anchor: Period) -> FinancialFact | None:
        """Value of a metric for an anchor period: same span for flows, period-end balance for stocks."""
        if spec.period_type is PeriodType.DURATION:
            if anchor.period_type is not PeriodType.DURATION:
                return None
            return self.duration(spec.id, anchor.start, anchor.end)
        return self.instant(spec.id, anchor.end)

    def opening(self, metric_id: str, anchor: Period) -> FinancialFact | None:
        if anchor.period_type is not PeriodType.DURATION:
            return None
        for back in range(1, OPENING_WINDOW_DAYS + 2):
            fact = self.instant(metric_id, anchor.start - timedelta(days=back))
            if fact is not None:
                return fact
        return None
