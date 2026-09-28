import json

import pyarrow.parquet as pq
import pytest

from research_engine.analysis.charts import render_chart
from research_engine.cli import main
from research_engine.config import load_project_config
from research_engine.errors import ConfigError
from research_engine.frameworks import FrameworkRegistry
from research_engine.pipeline import run_ingestion
from research_engine.pipeline.analysis import run_analysis_stage
from research_engine.pipeline.quality import run_quality_stage
from research_engine.schemas.framework import ChartSpec

from .conftest import FakeFetcher
from .factories import fy, reported


@pytest.fixture
def frameworks(frameworks_dir):
    return FrameworkRegistry(frameworks_dir)


@pytest.fixture
def prepared(sec_bank_config_path, tmp_path, frameworks):
    config = load_project_config(sec_bank_config_path)
    run_ingestion(config, workspace=tmp_path, frameworks=frameworks, fetcher=FakeFetcher())
    run_quality_stage(config, workspace=tmp_path, frameworks=frameworks)
    return config


def test_outputs_and_datasets(prepared, tmp_path, frameworks):
    result = run_analysis_stage(prepared, workspace=tmp_path, frameworks=frameworks)
    out = tmp_path / "output"
    for name in ("historical_financials.parquet", "historical_analytics.parquet", "historical_analytics.csv",
                 "historical_summary.json", "historical_analysis.md", "analysis_manifest.json", "charts/index.json"):
        assert (out / name).is_file(), name
    facts = pq.read_table(out / "historical_financials.parquet")
    analytics = pq.read_table(out / "historical_analytics.parquet")
    assert analytics.num_rows == len(result.analysis.values) > 0
    assert set(facts.column("provenance").to_pylist()) == {"reported", "derived"}
    roe = [r for r in analytics.to_pylist() if r["analytic_id"] == "return_on_average_equity"]
    assert roe and roe[0]["quality_flags"] == ["balance_sheet_identity"] and roe[0]["basis"] == "average"
    summary = json.loads((out / "historical_summary.json").read_text())
    assert summary["classification"] == "model_output" and "revenue_growth" in summary["summaries"]
    md = (out / "historical_analysis.md").read_text()
    assert "MODEL OUTPUT" in md and "† flagged FY2025" in md
    rendered = [c for c in result.charts if not c.skipped]
    assert rendered and all((out / f).is_file() for c in rendered for f in c.files)
    assert any(c.skipped == "fewer_than_two_points" for c in result.charts)


def test_chart_traces_to_source_url(prepared, tmp_path, frameworks):
    run_analysis_stage(prepared, workspace=tmp_path, frameworks=frameworks)
    graph = json.loads((tmp_path / "output" / "lineage.json").read_text())
    kinds = {n["id"]: n["kind"] for n in graph["nodes"]}
    parents = {}
    for e in graph["edges"]:
        parents.setdefault(e["child"], []).append(e["parent"])

    def paths(node):
        if node not in parents:
            return [[kinds[node]]]
        return [[kinds[node], *p] for parent in parents[node] for p in paths(parent)]

    chains = paths("chart:growth")
    assert ["chart", "model_output", "fact", "document", "source"] in chains
    assert all(chain[-1] == "source" for chain in chains)


def test_stale_inputs_are_rejected(prepared, tmp_path, frameworks, sec_bank_config_path):
    with pytest.raises(ConfigError, match="run `quality` first"):
        (tmp_path / "output" / "quality_manifest.json").rename(tmp_path / "qm.bak")
        run_analysis_stage(prepared, workspace=tmp_path, frameworks=frameworks)
    (tmp_path / "qm.bak").rename(tmp_path / "output" / "quality_manifest.json")
    # re-ingesting without re-running quality leaves the quality outputs stale
    run_ingestion(prepared, workspace=tmp_path, frameworks=frameworks, fetcher=FakeFetcher())
    with pytest.raises(ConfigError, match="re-run `quality`"):
        run_analysis_stage(prepared, workspace=tmp_path, frameworks=frameworks)


def test_framework_edit_is_detected(prepared, tmp_path, frameworks_dir):
    import shutil
    edited = tmp_path / "frameworks"
    shutil.copytree(frameworks_dir, edited)
    text = (edited / "banks.yaml").read_text().replace("name: Loans / deposits", "name: Loan-to-deposit ratio")
    (edited / "banks.yaml").write_text(text)
    with pytest.raises(ConfigError, match="framework 'banks' changed"):
        run_analysis_stage(prepared, workspace=tmp_path, frameworks=FrameworkRegistry(edited))


def test_chart_rendering_is_deterministic_and_marks_derived(tmp_path, frameworks_dir):
    framework = FrameworkRegistry(frameworks_dir).get("generic")
    facts = [reported("revenue", 100, fy(2023)), reported("revenue", 120, fy(2024))]
    spec = ChartSpec(id="revenue", title="Revenue", kind="bar", series=("revenue",), format="currency")
    a = render_chart(spec, framework, facts, {}, set(), tmp_path / "a", company_label="Test", engine_version="x")
    render_chart(spec, framework, facts, {}, set(), tmp_path / "b", company_label="Test", engine_version="x")
    assert (tmp_path / "a" / "revenue.svg").read_bytes() == (tmp_path / "b" / "revenue.svg").read_bytes()
    assert a.years == [2023, 2024] and a.derived_points == 0
    single = render_chart(spec, framework, facts[:1], {}, set(), tmp_path / "c", company_label="Test", engine_version="x")
    assert single.skipped == "fewer_than_two_points" and not (tmp_path / "c").exists()


def test_cli_analyze(prepared, sec_bank_config_path, tmp_path, frameworks_dir, capsys):
    code = main(["analyze", "--config", str(sec_bank_config_path), "--frameworks", str(frameworks_dir), "--workspace", str(tmp_path)])
    assert code == 0 and "charts:" in capsys.readouterr().out
