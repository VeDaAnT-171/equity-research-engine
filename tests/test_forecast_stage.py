import json

import pyarrow.parquet as pq
import pytest

from research_engine.cli import main
from research_engine.config import load_project_config
from research_engine.errors import ConfigError
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


def test_stage_writes_every_artifact(prepared, tmp_path, frameworks):
    result = run_forecast_stage(prepared, workspace=tmp_path, frameworks=frameworks)
    out = tmp_path / "output"
    for name in ("forecast.parquet", "forecast.csv", "forecast.md", "assumptions.json",
                 "forecast_manifest.json"):
        assert (out / name).is_file(), name
    table = pq.read_table(out / "forecast.parquet")
    assert table.num_rows == len(result.forecast.values) > 0
    assert set(table.column("scenario").to_pylist()) == {"base"}
    manifest = json.loads((out / "forecast_manifest.json").read_text())
    assert manifest["base_year"] == result.forecast.base_year
    assert manifest["assumptions_file"] is None  # none supplied in this fixture
    assert manifest["engine_version"] == "0.7.0"


def test_report_labels_model_output_and_shows_the_plan(prepared, tmp_path, frameworks):
    run_forecast_stage(prepared, workspace=tmp_path, frameworks=frameworks)
    md = (tmp_path / "output" / "forecast.md").read_text()
    assert "MODEL OUTPUT" in md
    assert "## Projection plan" in md
    assert "## Assumptions in force" in md
    assert "net_interest_income" in md and "average_interest_earning_assets * net_interest_margin" in md


def test_assumptions_json_records_the_plan_and_every_refusal(prepared, tmp_path, frameworks):
    run_forecast_stage(prepared, workspace=tmp_path, frameworks=frameworks)
    doc = json.loads((tmp_path / "output" / "assumptions.json").read_text())
    assert doc["resolution_order"] == ["scenario", "analyst_assumption", "management_guidance",
                                       "consensus", "historical"]
    plan = {row["metric_id"]: row for row in doc["projection_plan"]}
    # The fixture, like every filer read from XBRL alone, has no average interest-earning assets,
    # so the declared NII driver cannot run and its declared fallback is taken — and recorded.
    assert plan["net_interest_income"]["method"] == "growth"
    assert plan["net_interest_income"]["fallback_for"] == "net_interest_income_engine"
    assert "average_interest_earning_assets" in doc["fallbacks"]["net_interest_income"]
    # with the driver unused, the margin is not pulled into the plan, so nothing is demoted
    assert "net_interest_margin" not in plan and not doc["demoted_derivations"]
    # the sparse fixture cannot support most of the chain; every gap is named, none is filled in
    assert doc["unseeded"] and doc["not_projected"]
    assert all(":" in key for key in doc["not_projected"])


def test_analyst_file_is_read_and_changes_the_forecast(prepared, tmp_path, frameworks):
    baseline = run_forecast_stage(prepared, workspace=tmp_path, frameworks=frameworks)
    base_equity = baseline.forecast.by_scenario["base"]["total_equity"][baseline.forecast.base_year + 1].value

    (tmp_path / "assumptions.yaml").write_text("""
scenarios:
  - {id: bull, name: Bull case, description: Faster balance-sheet growth}
assumptions:
  - id: growth.total_equity
    description: Analyst view on retained earnings growth
    value: 0.02
    unit: ratio
    type: analyst_assumption
    rationale: Buybacks offset most retained earnings
  - id: growth.total_equity
    description: Bull case equity growth
    value: 0.30
    unit: ratio
    type: scenario
    scenario: bull
    rationale: No buybacks and a strong earnings year
""", encoding="utf-8")

    result = run_forecast_stage(prepared, workspace=tmp_path, frameworks=frameworks)
    year = result.forecast.base_year + 1
    assert set(result.forecast.by_scenario) == {"base", "bull"}
    assert result.forecast.by_scenario["base"]["total_equity"][year].value != base_equity
    assert result.forecast.by_scenario["base"]["total_equity"][year].assumption_types == ("analyst_assumption",)
    assert (result.forecast.by_scenario["bull"]["total_equity"][year].value
            > result.forecast.by_scenario["base"]["total_equity"][year].value)
    manifest = json.loads((tmp_path / "output" / "forecast_manifest.json").read_text())
    assert manifest["assumptions_file"] == "assumptions.yaml" and manifest["assumptions_sha256"]
    assert sorted(manifest["scenarios"]) == ["base", "bull"]


def test_a_bad_analyst_file_fails_the_stage(prepared, tmp_path, frameworks):
    (tmp_path / "assumptions.yaml").write_text(
        "assumptions:\n  - {id: growth.total_equity, description: g, value: 0.1, unit: ratio, "
        "type: management_guidance}\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="must cite source_document_id"):
        run_forecast_stage(prepared, workspace=tmp_path, frameworks=frameworks)


def test_forecast_traces_to_a_source_url(prepared, tmp_path, frameworks):
    result = run_forecast_stage(prepared, workspace=tmp_path, frameworks=frameworks)
    graph = json.loads((tmp_path / "output" / "lineage.json").read_text())
    kinds = {n["id"]: n["kind"] for n in graph["nodes"]}
    parents: dict[str, list[str]] = {}
    for e in graph["edges"]:
        parents.setdefault(e["child"], []).append(e["parent"])

    def paths(node):
        if node not in parents:
            return [[kinds[node]]]
        return [[kinds[node], *p] for parent in parents[node] for p in paths(parent)]

    year = result.forecast.base_year + 2
    target = result.forecast.by_scenario["base"]["total_equity"][year].value_id
    chains = paths(target)
    assert all(chain[-1] == "source" for chain in chains)
    # every projected figure reaches a filed document, through the assumption and through the actual
    assert any("assumption" in chain for chain in chains)
    assert any(chain[:2] == ["model_output", "model_output"] for chain in chains)


def test_stale_analysis_is_rejected(prepared, tmp_path, frameworks):
    (tmp_path / "output" / "analysis_manifest.json").rename(tmp_path / "am.bak")
    with pytest.raises(ConfigError, match="run `analyze` first"):
        run_forecast_stage(prepared, workspace=tmp_path, frameworks=frameworks)
    (tmp_path / "am.bak").rename(tmp_path / "output" / "analysis_manifest.json")
    # a data-quality run newer than the historical analysis leaves the forecast's inputs stale
    quality_manifest = tmp_path / "output" / "quality_manifest.json"
    doc = json.loads(quality_manifest.read_text())
    doc["generated_at"] = "2099-01-01T00:00:00+00:00"
    quality_manifest.write_text(json.dumps(doc, indent=2))
    with pytest.raises(ConfigError, match="re-run `analyze`"):
        run_forecast_stage(prepared, workspace=tmp_path, frameworks=frameworks)


def test_cli_forecast(prepared, sec_bank_config_path, tmp_path, frameworks_dir, capsys):
    code = main(["forecast", "--config", str(sec_bank_config_path), "--frameworks", str(frameworks_dir),
                 "--workspace", str(tmp_path)])
    out = capsys.readouterr().out
    assert code == 0
    assert "scenarios:" in out and "assumptions:" in out
    assert "no assumptions.yaml" in out


def test_research_command_reports_forecast_as_implemented(sec_bank_config_path, frameworks_dir, capsys):
    code = main(["research", "--config", str(sec_bank_config_path), "--frameworks", str(frameworks_dir)])
    assert code == 2
    assert "`forecast`" in capsys.readouterr().err
