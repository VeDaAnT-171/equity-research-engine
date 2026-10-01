"""The forecast stage rewrites lineage.json. It must not drop what `analyze` recorded.

This is a regression test for a real defect: the first version of the forecast stage rebuilt the
lineage graph from documents and facts only, so running `forecast` silently destroyed the
chart -> analytic value -> fact -> document -> source chains that Phase 4 had written. Nothing
failed at the time; the loss only surfaced when something tried to audit a historical figure.
"""

import json

import pytest

from research_engine.config import load_project_config
from research_engine.frameworks import FrameworkRegistry
from research_engine.pipeline import run_ingestion
from research_engine.pipeline.analysis import run_analysis_stage
from research_engine.pipeline.forecast import run_forecast_stage
from research_engine.pipeline.quality import run_quality_stage

from .conftest import FakeFetcher


@pytest.fixture
def frameworks(frameworks_dir):
    return FrameworkRegistry(frameworks_dir)


@pytest.fixture
def prepared(sec_bank_config_path, tmp_path, frameworks):
    config = load_project_config(sec_bank_config_path)
    run_ingestion(config, workspace=tmp_path, frameworks=frameworks, fetcher=FakeFetcher())
    run_quality_stage(config, workspace=tmp_path, frameworks=frameworks)
    run_analysis_stage(config, workspace=tmp_path, frameworks=frameworks)
    return config


def _graph(tmp_path):
    graph = json.loads((tmp_path / "output" / "lineage.json").read_text(encoding="utf-8"))
    parents: dict[str, list[str]] = {}
    for edge in graph["edges"]:
        parents.setdefault(edge["child"], []).append(edge["parent"])
    return {n["id"]: n for n in graph["nodes"]}, parents


def _chains(node, nodes, parents):
    if node not in parents:
        return [[nodes[node]["kind"]]]
    return [[nodes[node]["kind"], *rest] for p in parents[node] for rest in _chains(p, nodes, parents)]


def test_forecast_preserves_the_analysis_lineage(prepared, tmp_path, frameworks):
    before_nodes, _ = _graph(tmp_path)
    analytic_ids = {n for n in before_nodes if n.startswith("ana_")}
    chart_ids = {n for n in before_nodes if n.startswith("chart:")}
    assert analytic_ids and chart_ids, "the analysis stage should have written both"

    run_forecast_stage(prepared, workspace=tmp_path, frameworks=frameworks)

    after_nodes, parents = _graph(tmp_path)
    assert analytic_ids <= set(after_nodes), "forecast dropped analytic values from the lineage"
    assert chart_ids <= set(after_nodes), "forecast dropped charts from the lineage"

    chart = sorted(chart_ids)[0]
    chains = _chains(chart, after_nodes, parents)
    assert ["chart", "model_output", "fact", "document", "source"] in chains
    assert all(chain[-1] == "source" for chain in chains)


def test_forecast_lineage_holds_both_histories_at_once(prepared, tmp_path, frameworks):
    result = run_forecast_stage(prepared, workspace=tmp_path, frameworks=frameworks)
    nodes, parents = _graph(tmp_path)
    kinds = {n["kind"] for n in nodes.values()}
    assert {"source", "document", "fact", "assumption", "model_output", "chart"} <= kinds

    projected = next(v for v in result.forecast.values if v.method == "growth")
    chains = _chains(projected.value_id, nodes, parents)
    assert any("assumption" in chain for chain in chains)
    assert all(chain[-1] == "source" for chain in chains)
