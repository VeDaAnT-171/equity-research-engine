"""Response models for the API.

These exist so the OpenAPI document describes real shapes rather than `{}`, and so a client
knows which fields can be absent. They are read models only: nothing here computes a financial
figure, and every field is populated from a file a pipeline stage wrote.
"""

from __future__ import annotations

from typing import Any

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
    sector: str | None = None
    stages: Stages


class FrameworkChoice(Model):
    name: str | None = None
    method: str | None = Field(default=None, description="How the framework was chosen")
    evidence: str | None = Field(default=None, description="The filing evidence behind it")


class ForecastHorizon(Model):
    base_year: int | None = None
    years: list[int] = []
    scenarios: list[str] = []
    assumptions_file: str | None = None


class Overview(Model):
    company_id: str
    name: str
    ticker: str
    exchange: str
    country: str
    sector: str | None = None
    reporting_currency: str | None = None
    stages: Stages
    framework: FrameworkChoice | None = None
    fiscal_calendar: dict[str, Any] | None = None
    versions: dict[str, str | None] | None = None
    counts: dict[str, Any] = {}
    warnings: list[str] = []
    generated_at: str | None = None
    forecast_horizon: ForecastHorizon | None = None


class Document(Model):
    document_id: str
    status: str
    file_hash: str | None = None
    source: str | None = None


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
    currency: str | None = None
    points: list[AnalyticPoint]
    summary: dict[str, Any] | None = None


class Analytics(Model):
    fiscal_years: list[int]
    classification: str | None = None
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
    truncated: dict[str, int] = {}


class ChartData(Model):
    """The accessible fallback for a chart: the same numbers, as a table."""

    chart_id: str
    title: str
    unit_kind: str | None = None
    currency: str | None = None
    columns: list[str]
    rows: list[dict[str, Any]]
    notes: list[str] = []
    #: series id -> last fiscal year with a value, for series that stop before the table does.
    #: A blank trailing cell and a deliberately empty one look identical; this says which it is.
    truncated: dict[str, int] = {}


# ---- forecast -------------------------------------------------------------------------------

class ForecastPoint(Model):
    fiscal_year: int
    value: float
    method: str = Field(description="actual | driver_formula | derivation | growth | level")
    formula: str
    assumption_ids: list[str] = []
    assumption_types: list[str] = []
    value_id: str
    fallback_for: list[str] = Field(
        default=[], description="Metrics projected by fallback rather than their declared driver that "
                                "this value depends on; empty when the declared model produced every input.")


class ForecastMetric(Model):
    metric_id: str
    unit_kind: str
    currency: str | None = None
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
    driver_id: str | None = None
    reason: str = ""
    metric_inputs: list[str] = []
    assumption_keys: list[str] = []
    fallback_for: str | None = None


class Forecast(Model):
    base_year: int | None = None
    forecast_years: list[int] = []
    scenarios: list[Scenario] = []
    projection_plan: list[ProjectionRule] = []
    not_projected: dict[str, int] = {}
    unseeded: dict[str, str] = {}
    demoted_derivations: dict[str, str] = {}
    fallbacks: dict[str, str] = {}
    unresolved_targets: dict[str, str] = {}
    assumptions_file: str | None = None


class Assumption(Model):
    assumption_id: str
    description: str
    value: float | None = None
    unit: str
    type: str
    period: str | None = None
    scenario: str | None = None
    rationale: str | None = None
    source_document_id: str | None = None
    source_fact_ids: list[str] = []


class Assumptions(Model):
    resolution_order: list[str]
    assumptions: list[Assumption]
    scenarios: list[dict[str, Any]] = []
    unseeded: dict[str, str] = {}
    base_year: int | None = None
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
    metric_id: str | None = None
    period: str | None = None
    fact_ids: list[str] = []


class Quality(Model):
    company_id: str
    framework: str | None = None
    historical_years: int | None = None
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
