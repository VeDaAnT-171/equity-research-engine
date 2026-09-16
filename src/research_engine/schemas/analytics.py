"""Historical analytic values: model outputs computed from facts, with lineage to their inputs."""

from __future__ import annotations

import hashlib
import re
from decimal import Decimal
from typing import Literal, Optional

from pydantic import Field, model_validator

from .common import FACT_ID_PATTERN, METRIC_ID_PATTERN, SLUG_PATTERN, StrictModel

ANALYTIC_VALUE_ID_PATTERN = r"^ana_[0-9a-f]{16}$"


class AnalyticValue(StrictModel):
    value_id: str = Field(pattern=ANALYTIC_VALUE_ID_PATTERN)
    company_id: str = Field(pattern=SLUG_PATTERN)
    analytic_id: str = Field(pattern=METRIC_ID_PATTERN)
    category: str
    kind: str
    unit_kind: str
    fiscal_year: int = Field(ge=1900, le=2200)
    value: Decimal
    currency: Optional[str] = None
    formula: str = Field(min_length=1)
    basis: Literal["period_end", "average", "opening", "year_on_year"]
    input_fact_ids: tuple[str, ...] = ()
    input_value_ids: tuple[str, ...] = ()
    # Warning-severity data-quality checks that touched any input (directly or through input analytics)
    quality_flags: tuple[str, ...] = ()
    uses_derived_facts: bool = False

    @model_validator(mode="after")
    def _lineage(self) -> "AnalyticValue":
        if not self.value.is_finite():
            raise ValueError("analytic value must be finite")
        if not self.input_fact_ids and not self.input_value_ids:
            raise ValueError("analytic values require input facts or input analytic values")
        for fid in self.input_fact_ids:
            if not re.fullmatch(FACT_ID_PATTERN, fid):
                raise ValueError(f"invalid input fact id {fid!r}")
        return self


def make_value_id(company_id: str, analytic_id: str, fiscal_year: int, input_ids: tuple[str, ...]) -> str:
    raw = "|".join([company_id, analytic_id, str(fiscal_year), *sorted(input_ids)])
    return "ana_" + hashlib.sha256(raw.encode()).hexdigest()[:16]
