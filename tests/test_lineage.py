from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from research_engine.errors import LineageError
from research_engine.lineage import LineageGraph, LineageNode, NodeKind
from research_engine.schemas import (
    DocumentRecord, DocumentStatus, DocumentType, ExtractionMethod, FinancialFact, FiscalPeriodCode, Period,
    PeriodType, Provenance, SourceLocation, make_fact_id,
)

NOW = datetime(2026, 2, 1, tzinfo=timezone.utc)
FY = Period(period_type=PeriodType.DURATION, start=date(2025, 1, 1), end=date(2025, 12, 31),
            fiscal_year=2025, fiscal_period=FiscalPeriodCode.FY)


def document(doc_id, url):
    return DocumentRecord(document_id=doc_id, company_id="nyse-tst", document_type=DocumentType.ANNUAL_REPORT,
                          source_url=url, status=DocumentStatus.REGISTERED, registered_at=NOW, updated_at=NOW)


def rep(metric, value, doc_id, page):
    return FinancialFact(
        fact_id=make_fact_id("nyse-tst", metric, FY, Provenance.REPORTED, f"{doc_id}:{page}"),
        company_id="nyse-tst", metric_id=metric, value=Decimal(value), unit="USD", currency="USD", period=FY,
        provenance=Provenance.REPORTED, extraction_method=ExtractionMethod.PDF_TABLE,
        source=SourceLocation(document_id=doc_id, page=page, table="Consolidated Statements of Income"),
    )


@pytest.fixture
def setup():
    d1 = document("doc_aaaaaaaaaaaaaaaa", "https://example.com/ar.pdf")
    d2 = document("doc_bbbbbbbbbbbbbbbb", "https://example.com/supplement.pdf")
    revenue = rep("revenue", "1000", d1.document_id, 84)
    cogs = rep("cost_of_revenue", "600", d2.document_id, 12)
    gross = FinancialFact(
        fact_id=make_fact_id("nyse-tst", "gross_profit", FY, Provenance.DERIVED, "revenue - cost_of_revenue"),
        company_id="nyse-tst", metric_id="gross_profit", value=Decimal("400"), unit="USD", currency="USD", period=FY,
        provenance=Provenance.DERIVED, extraction_method=ExtractionMethod.COMPUTED,
        inputs=(revenue.fact_id, cogs.fact_id), formula="revenue - cost_of_revenue",
    )
    graph = LineageGraph.from_records([d1, d2], [revenue, cogs, gross])
    return graph, revenue, gross


def test_report_chart_traces_to_source_url(setup):
    graph, revenue, _ = setup
    graph.add_node(LineageNode("model:revenue_fy2025", NodeKind.MODEL_OUTPUT, "model input"))
    graph.add_node(LineageNode("chart:revenue_history", NodeKind.CHART, "Revenue history"))
    graph.add_node(LineageNode("report:page5", NodeKind.REPORT_ELEMENT, "Historical analysis"))
    graph.link("model:revenue_fy2025", revenue.fact_id)
    graph.link("chart:revenue_history", "model:revenue_fy2025")
    graph.link("report:page5", "chart:revenue_history")
    [path] = graph.trace("report:page5")
    assert [n.kind for n in path] == [NodeKind.REPORT_ELEMENT, NodeKind.CHART, NodeKind.MODEL_OUTPUT,
                                      NodeKind.FACT, NodeKind.DOCUMENT, NodeKind.SOURCE]
    assert path[3].attributes["page"] == "84"
    assert path[-1].label == "https://example.com/ar.pdf"


def test_derived_fact_traces_to_every_source(setup):
    graph, _, gross = setup
    roots = {path[-1].label for path in graph.trace(gross.fact_id)}
    assert roots == {"https://example.com/ar.pdf", "https://example.com/supplement.pdf"}
    assert graph.broken_nodes() == []


def test_cycles_rejected(setup):
    graph, revenue, gross = setup
    with pytest.raises(LineageError, match="cycle"):
        graph.link(revenue.fact_id, gross.fact_id)


def test_broken_lineage_detected(setup):
    graph, *_ = setup
    graph.add_node(LineageNode("chart:orphan", NodeKind.CHART, "Unsourced chart"))
    assert [n.node_id for n in graph.broken_nodes()] == ["chart:orphan"]
    graph.add_node(LineageNode("assump:growth", NodeKind.ASSUMPTION, "Analyst growth"))
    graph.add_node(LineageNode("model:fc", NodeKind.MODEL_OUTPUT, "forecast"))
    graph.link("model:fc", "assump:growth")
    assert "model:fc" not in {n.node_id for n in graph.broken_nodes()}  # assumptions are legitimate roots


def test_fact_citing_unregistered_document():
    orphan = rep("revenue", "1", "doc_cccccccccccccccc", 1)
    with pytest.raises(LineageError, match="unregistered document"):
        LineageGraph.from_records([], [orphan])


def test_serialisable(setup):
    graph, *_ = setup
    data = graph.to_dict()
    assert len(data["nodes"]) == 7 and len(data["edges"]) == 6
