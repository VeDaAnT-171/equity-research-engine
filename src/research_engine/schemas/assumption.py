"""Assumption registry schema. Type rules make fabrication structurally hard:
guidance and consensus cannot exist without a cited document."""

from __future__ import annotations

from decimal import Decimal
from enum import Enum
from typing import Optional

from pydantic import Field, model_validator

from .common import DOCUMENT_ID_PATTERN, FISCAL_PERIOD_LABEL_PATTERN, SLUG_PATTERN, StrictModel


class AssumptionType(str, Enum):
    HISTORICAL = "historical"
    MANAGEMENT_GUIDANCE = "management_guidance"
    CONSENSUS = "consensus"
    ANALYST_ASSUMPTION = "analyst_assumption"
    DERIVED = "derived"
    SCENARIO = "scenario"


class Assumption(StrictModel):
    assumption_id: str = Field(pattern=r"^[a-z][a-z0-9_.\-]*$")
    company_id: str = Field(pattern=SLUG_PATTERN)
    description: str = Field(min_length=1)
    value: Optional[Decimal] = None  # None = declared but not yet set; models must refuse unset inputs
    unit: str = Field(min_length=1)
    period: Optional[str] = Field(default=None, pattern=FISCAL_PERIOD_LABEL_PATTERN)
    type: AssumptionType
    source_document_id: Optional[str] = Field(default=None, pattern=DOCUMENT_ID_PATTERN)
    source_fact_ids: tuple[str, ...] = ()
    scenario: Optional[str] = Field(default=None, pattern=SLUG_PATTERN)
    rationale: Optional[str] = None

    @property
    def is_set(self) -> bool:
        return self.value is not None

    @model_validator(mode="after")
    def _type_rules(self) -> "Assumption":
        t = self.type
        if self.value is not None and not self.value.is_finite():
            raise ValueError("assumption value must be finite")
        if t in {AssumptionType.MANAGEMENT_GUIDANCE, AssumptionType.CONSENSUS} and not self.source_document_id:
            raise ValueError(f"'{t.value}' assumptions must cite source_document_id; guidance and consensus are never inferred")
        if t in {AssumptionType.HISTORICAL, AssumptionType.DERIVED} and not self.source_fact_ids:
            raise ValueError(f"'{t.value}' assumptions must reference the facts they come from (source_fact_ids)")
        if t is AssumptionType.ANALYST_ASSUMPTION and not self.rationale:
            raise ValueError("analyst assumptions require a written rationale")
        if t is AssumptionType.SCENARIO and not (self.scenario and self.rationale):
            raise ValueError("scenario assumptions require a scenario name and a rationale")
        if self.scenario and t is not AssumptionType.SCENARIO:
            raise ValueError("only 'scenario' assumptions may be bound to a scenario")
        return self
