"""Forecast values and scenarios: model outputs projected from assumptions, with lineage.

A forecast value is never a fact. It records which assumption supplied each exogenous input,
which prior forecast values it consumed, and which base-year facts the projection started from,
so a projected number can always be traced to a declared assumption and a filed disclosure.
"""

from __future__ import annotations

import hashlib
from decimal import Decimal
from typing import Literal

from pydantic import Field, model_validator

from .common import FACT_ID_PATTERN, METRIC_ID_PATTERN, SLUG_PATTERN, StrictModel

FORECAST_VALUE_ID_PATTERN = r"^fcv_[0-9a-f]{16}$"
ASSUMPTION_ID_PATTERN = r"^[a-z][a-z0-9_.\-]*$"

BASE_SCENARIO = "base"

# How a metric's forecast value was produced.
#   actual          -- the last reported fiscal year, carried in as the projection's starting point
#   driver_formula  -- a framework DriverSpec formula affecting this metric
#   derivation      -- the metric's own framework derivation, re-applied to projected inputs
#   growth          -- exogenous: prior year multiplied by a growth-rate assumption
#   level           -- exogenous: the assumption is the value itself (margins, ratios, rates)
ForecastMethod = Literal["actual", "driver_formula", "derivation", "growth", "level"]


class ScenarioSpec(StrictModel):
    """An analyst-declared scenario. `base` always exists and needs no declaration."""

    id: str = Field(pattern=SLUG_PATTERN)
    name: str = Field(min_length=1)
    description: str = ""

    @model_validator(mode="after")
    def _base_is_reserved(self) -> ScenarioSpec:
        if self.id == BASE_SCENARIO and self.description.strip() == "":
            # Allowed, but the base case still deserves a name.
            pass
        return self


class ForecastValue(StrictModel):
    value_id: str = Field(pattern=FORECAST_VALUE_ID_PATTERN)
    company_id: str = Field(pattern=SLUG_PATTERN)
    scenario: str = Field(pattern=SLUG_PATTERN)
    metric_id: str = Field(pattern=METRIC_ID_PATTERN)
    fiscal_year: int = Field(ge=1900, le=2200)
    value: Decimal
    unit_kind: str = Field(min_length=1)
    currency: str | None = None
    method: ForecastMethod
    formula: str = Field(min_length=1)
    # Exogenous inputs: which assumption supplied the rate/level, and at which rung of the ladder.
    assumption_ids: tuple[str, ...] = ()
    assumption_types: tuple[str, ...] = ()
    # Lineage: prior-year or same-year forecast values, and the base-year facts underneath.
    input_value_ids: tuple[str, ...] = ()
    input_fact_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _lineage(self) -> ForecastValue:
        if not self.value.is_finite():
            raise ValueError("forecast value must be finite")
        if len(self.assumption_ids) != len(self.assumption_types):
            raise ValueError("every assumption id must carry its assumption type")
        if self.method == "actual":
            if not self.input_fact_ids:
                raise ValueError("an 'actual' forecast row must cite the fact it carries in")
            if self.assumption_ids:
                raise ValueError("actuals are reported, not assumed")
        elif self.method in ("growth", "level") and not self.assumption_ids:
            raise ValueError(f"'{self.method}' projections are exogenous and must cite an assumption")
        elif self.method in ("driver_formula", "derivation") and not (self.input_value_ids or self.assumption_ids):
            raise ValueError(f"'{self.method}' projections must cite the values they were computed from")
        for fid in self.input_fact_ids:
            import re
            if not re.fullmatch(FACT_ID_PATTERN, fid):
                raise ValueError(f"invalid input fact id {fid!r}")
        return self

    @property
    def is_actual(self) -> bool:
        return self.method == "actual"


def make_forecast_id(company_id: str, scenario: str, metric_id: str, fiscal_year: int,
                     inputs: tuple[str, ...]) -> str:
    raw = "|".join([company_id, scenario, metric_id, str(fiscal_year), *sorted(inputs)])
    return "fcv_" + hashlib.sha256(raw.encode()).hexdigest()[:16]
