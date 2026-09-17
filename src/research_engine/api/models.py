"""Response models for the API.

These exist so the OpenAPI document describes real shapes rather than `{}`, and so a client
knows which fields can be absent. They are read models only: nothing here computes a financial
figure, and every field is populated from a file a pipeline stage wrote.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class Model(BaseModel):
    """Permissive on input, explicit on output: pipeline manifests grow between versions."""

    model_config = {"extra": "allow"}


class Stages(Model):
    ingest: bool
    quality: bool
    analyze: bool
    forecast: bool


class CompanySummary(Model):
    company_id: str
    name: str
    ticker: str
    exchange: str
    sector: Optional[str] = None
    stages: Stages


class FrameworkChoice(Model):
    name: Optional[str] = None
    method: Optional[str] = Field(default=None, description="How the framework was chosen")
    evidence: Optional[str] = Field(default=None, description="The filing evidence behind it")


class ForecastHorizon(Model):
    base_year: Optional[int] = None
    years: list[int] = []
    scenarios: list[str] = []
    assumptions_file: Optional[str] = None


class Overview(Model):
    company_id: str
    name: str
    ticker: str
    exchange: str
    country: str
    sector: Optional[str] = None
    reporting_currency: Optional[str] = None
    stages: Stages
    framework: Optional[FrameworkChoice] = None
    fiscal_calendar: Optional[dict[str, Any]] = None
    versions: Optional[dict[str, Optional[str]]] = None
    counts: dict[str, Any] = {}
    warnings: list[str] = []
    generated_at: Optional[str] = None
    forecast_horizon: Optional[ForecastHorizon] = None


class Document(Model):
    document_id: str
    status: str
    file_hash: Optional[str] = None
    source: Optional[str] = None


# ---- historical -----------------------------------------------------------------------------

class AnalyticPoint(Model):
    fiscal_year: int
    value: float
    basis: str
    formula: str
    quality_flags: list[str] = []
    uses_derived_facts: bool = False
    value_id: str = Field(description="Lineage id; trace it at /lineage/{node_id}")


class AnalyticSeries(Model):
    analytic_id: str
    category: str
    kind: str
    unit_kind: str
    currency: Optional[str] = None
    points: list[AnalyticPoint]
    summary: Optional[dict[str, Any]] = None


class Analytics(Model):
    fiscal_years: list[int]
    classification: Optional[str] = None
    series: list[AnalyticSeries]
    not_computed: dict[str, int] = Field(
        default={}, description="Analytic-years the engine declined to compute, by reason")


class Chart(Model):
    chart_id: str
    title: str
    files: list[str] = []
    series: dict[str, list[str]] = {}
    years: list[int] = []
    derived_points: int = 0
    flagged_points: int = 0
    notes: list[str] = []


class ChartData(Model):
    """The accessible fallback for a chart: the same numbers, as a table."""

    chart_id: str
    title: str
    unit_kind: Optional[str] = None
    currency: Optional[str] = None
    columns: list[str]
    rows: list[dict[str, Any]]


# ---- forecast -------------------------------------------------------------------------------

class ForecastPoint(Model):
    fiscal_year: int
    value: float
    method: str = Field(description="actual | driver_formula | derivation | growth | level")
    formula: str
    assumption_ids: list[str] = []
    assumption_types: list[str] = []
    value_id: str


class ForecastMetric(Model):
    metric_id: str
    unit_kind: str
    currency: Optional[str] = None
    points: list[ForecastPoint]


class Scenario(Model):
    id: str
    name: str
    description: str = ""
    metrics: list[ForecastMetric] = []


class ProjectionRule(Model):
    metric_id: str
    method: str
    formula: str
    driver_id: Optional[str] = None
    reason: str = ""
    metric_inputs: list[str] = []
    assumption_keys: list[str] = []


class Forecast(Model):
    base_year: Optional[int] = None
    forecast_years: list[int] = []
    scenarios: list[Scenario] = []
    projection_plan: list[ProjectionRule] = []
    not_projected: dict[str, int] = {}
    unseeded: dict[str, str] = {}
    demoted_derivations: dict[str, str] = {}
    unresolved_targets: dict[str, str] = {}
    assumptions_file: Optional[str] = None


class Assumption(Model):
    assumption_id: str
    description: str
    value: Optional[float] = None
    unit: str
    type: str
    period: Optional[str] = None
    scenario: Optional[str] = None
    rationale: Optional[str] = None
    source_document_id: Optional[str] = None
    source_fact_ids: list[str] = []


class Assumptions(Model):
    resolution_order: list[str]
    assumptions: list[Assumption]
    scenarios: list[dict[str, Any]] = []
    unseeded: dict[str, str] = {}
    base_year: Optional[int] = None
    forecast_years: list[int] = []


# ---- quality --------------------------------------------------------------------------------

class Check(Model):
    check: str
    description: str = ""
    passed: int = 0
    failed: int = 0
    not_evaluable: int = 0


class Issue(Model):
    check: str
    severity: str
    message: str
    metric_id: Optional[str] = None
    period: Optional[str] = None
    fact_ids: list[str] = []


class Quality(Model):
    company_id: str
    framework: Optional[str] = None
    historical_years: Optional[int] = None
    counts: dict[str, int] = {}
    checks: dict[str, Check] = {}
    issues: list[Issue] = []
    coverage: Any = None
    derivations: Any = None


# ---- lineage --------------------------------------------------------------------------------

class LineageNode(Model):
    id: str
    kind: str
    label: str
    attributes: dict[str, str] = {}


class LineageEdge(Model):
    child: str
    parent: str


class Lineage(Model):
    node_id: str
    nodes: list[LineageNode]
    edges: list[LineageEdge]
    roots: list[str]
    source_urls: list[str]
    truncated: bool = False
    depth: int = 0


class Health(Model):
    status: str
    engine_version: str
    schema_version: str
    parser_version: str
    companies_root: str
