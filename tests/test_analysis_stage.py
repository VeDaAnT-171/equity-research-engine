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
    summary = json.loads((out / "historical_summary.json").read_text(encoding="utf-8"))
    assert summary["classification"] == "model_output" and "revenue_growth" in summary["summaries"]
    md = (out / "historical_analysis.md").read_text(encoding="utf-8")
    assert "MODEL OUTPUT" in md and "† flagged FY2025" in md
    rendered = [c for c in result.charts if not c.skipped]
    assert rendered and all((out / f).is_file() for c in rendered for f in c.files)
    assert any(c.skipped == "fewer_than_two_points" for c in result.charts)


def test_chart_traces_to_source_url(prepared, tmp_path, frameworks):
    run_analysis_stage(prepared, workspace=tmp_path, frameworks=frameworks)
    graph = json.loads((tmp_path / "output" / "lineage.json").read_text(encoding="utf-8"))
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
    text = (edited / "banks.yaml").read_text(encoding="utf-8").replace("name: Loans / deposits", "name: Loan-to-deposit ratio")
    (edited / "banks.yaml").write_text(text, encoding="utf-8")
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


def test_a_series_that_stops_early_is_recorded_not_silently_cropped(tmp_path, frameworks_dir):
    """A chart drawn only as far as its data goes looks complete when it is not.

    JPMorgan stopped using the loans concept this framework declares after FY2015, so the credit
    chart ran 2009-2015 while every sibling metric ran to 2025 — and said nothing. The axis now
    covers the company's reporting span, and the reason the series ended travels with the chart.
    """
    framework = FrameworkRegistry(frameworks_dir).get("generic")
    facts = [reported("revenue", 100 + i, fy(y)) for i, y in enumerate((2020, 2021, 2022))]
    spec = ChartSpec(id="revenue", title="Revenue", kind="line", series=("revenue",), format="currency")
    r = render_chart(spec, framework, facts, {}, set(), tmp_path / "t", company_label="Test",
                     engine_version="x", coverage_years=[2020, 2021, 2022, 2023, 2024, 2025],
                     not_computed={"revenue:input_missing:loans": 4})
    assert r.years == [2020, 2021, 2022, 2023, 2024, 2025], "the axis must show the years it lacks"
    assert r.truncated == {"revenue": 2022}
    assert any("no value after FY2022" in n and "No figure for loans is reported" in n for n in r.notes)
    assert not any("input_missing" in n for n in r.notes)
    assert "Incomplete over the period shown" in (tmp_path / "t" / "revenue.svg").read_text(encoding="utf-8")


def test_a_complete_series_gets_no_truncation_note(tmp_path, frameworks_dir):
    """The note has to stay rare, or it stops being read."""
    framework = FrameworkRegistry(frameworks_dir).get("generic")
    facts = [reported("revenue", 100 + i, fy(y)) for i, y in enumerate((2023, 2024, 2025))]
    spec = ChartSpec(id="revenue", title="Revenue", kind="line", series=("revenue",), format="currency")
    r = render_chart(spec, framework, facts, {}, set(), tmp_path / "t", company_label="Test",
                     engine_version="x", coverage_years=[2020, 2021, 2022, 2023, 2024, 2025])
    # a late start is when tagging began, not a gap: the axis is never extended backwards
    assert r.years == [2023, 2024, 2025] and not r.truncated and not r.notes


def test_cli_analyze(prepared, sec_bank_config_path, tmp_path, frameworks_dir, capsys):
    code = main(["analyze", "--config", str(sec_bank_config_path), "--frameworks", str(frameworks_dir), "--workspace", str(tmp_path)])
    assert code == 0 and "charts:" in capsys.readouterr().out


def test_a_line_is_broken_where_years_are_missing():
    """A polyline across a missing year draws a smooth path through years with no data.

    A bank's loan series has no net-loans value for one year between two taxonomy renames; a single
    stroke bridged it and the credit-cost chart showed a clean decline through a year the engine
    had no number for.
    """
    from research_engine.analysis.charts import consecutive_runs
    assert consecutive_runs([2016, 2017, 2018, 2021, 2022]) == [[0, 1, 2], [3, 4]]
    assert consecutive_runs([2020]) == [[0]]
    assert consecutive_runs([]) == []


def test_a_gap_inside_a_series_is_named_in_the_chart_notes(tmp_path, frameworks_dir):
    framework = FrameworkRegistry(frameworks_dir).get("generic")
    facts = [reported("revenue", 100 + i, fy(y)) for i, y in enumerate((2020, 2021, 2024, 2025))]
    spec = ChartSpec(id="revenue", title="Revenue", kind="line", series=("revenue",), format="currency")
    r = render_chart(spec, framework, facts, {}, set(), tmp_path / "t", company_label="Test",
                     engine_version="x", coverage_years=range(2020, 2026))
    assert r.years == [2020, 2021, 2022, 2023, 2024, 2025]
    assert any("no value for FY2022–FY2023" in n for n in r.notes)
    assert not r.truncated, "a gap is not a truncation: the series runs to the end of the span"
