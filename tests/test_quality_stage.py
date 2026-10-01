import json

import pytest

from research_engine.cli import main
from research_engine.config import load_project_config
from research_engine.errors import ConfigError
from research_engine.frameworks import FrameworkRegistry
from research_engine.pipeline import run_ingestion
from research_engine.pipeline.quality import run_quality_stage

from .conftest import FakeFetcher


@pytest.fixture
def frameworks(frameworks_dir):
    return FrameworkRegistry(frameworks_dir)


@pytest.fixture
def ingested(sec_bank_config_path, tmp_path, frameworks):
    config = load_project_config(sec_bank_config_path)
    run_ingestion(config, workspace=tmp_path, frameworks=frameworks, fetcher=FakeFetcher())
    return config


def test_stage_outputs_and_findings(ingested, tmp_path, frameworks):
    result = run_quality_stage(ingested, workspace=tmp_path, frameworks=frameworks)
    out = tmp_path / "output"
    for name in ("facts_derived.jsonl", "financials_long.csv", "financials_annual.csv", "financials_interim.csv",
                 "data_quality_report.html", "data_quality_report.json", "quality_manifest.json", "lineage.json"):
        assert (out / name).is_file(), name
    report = json.loads((out / "data_quality_report.json").read_text(encoding="utf-8"))
    assert report["checks"]["balance_sheet_identity"]["failed"] == 1   # the fixture's 5bn NCI-style gap
    assert report["checks"]["cash_roll_forward"]["passed"] == 1
    assert report["checks"]["cash_flow_statement_sum"]["passed"] == 1
    assert any(i["check"] == "restatement" for i in report["issues"])
    interim = (out / "financials_interim.csv").read_text(encoding="utf-8")
    assert "revenue,USD,14000000000,29000000000,15000000000,44000000000,15000000000,31000000000,16000000000" in interim
    html = (out / "data_quality_report.html").read_text(encoding="utf-8")
    assert "balance_sheet_identity" in html and "<script" not in html
    assert result.report.counts()["error"] == 0


def test_derived_quarter_traces_to_source_documents(ingested, tmp_path, frameworks):
    result = run_quality_stage(ingested, workspace=tmp_path, frameworks=frameworks)
    q4 = next(f for f in result.derived if f.metric_id == "revenue" and f.period.label == "Q4-2025")
    graph = json.loads((tmp_path / "output" / "lineage.json").read_text(encoding="utf-8"))
    parents = {}
    for e in graph["edges"]:
        parents.setdefault(e["child"], []).append(e["parent"])
    kinds = {n["id"]: n["kind"] for n in graph["nodes"]}

    def roots(node):
        return {node} if node not in parents else set().union(*(roots(p) for p in parents[node]))

    assert {kinds[r] for r in roots(q4.fact_id)} == {"source"}


def test_requires_ingestion_and_unchanged_config(sec_bank_config_path, tmp_path, frameworks, ingested):
    empty = tmp_path / "empty"
    with pytest.raises(ConfigError, match="run `ingest` first"):
        run_quality_stage(ingested, workspace=empty, frameworks=frameworks)
    changed_path = tmp_path / "changed.yaml"
    changed_path.write_text(sec_bank_config_path.read_text(encoding="utf-8").replace('reporting_currency: "USD"', 'reporting_currency: "EUR"'), encoding="utf-8")
    with pytest.raises(ConfigError, match="config changed"):
        run_quality_stage(load_project_config(changed_path), workspace=tmp_path, frameworks=frameworks)


def test_cli_strict_mode(ingested, sec_bank_config_path, tmp_path, frameworks_dir, capsys):
    args = ["quality", "--config", str(sec_bank_config_path), "--frameworks", str(frameworks_dir), "--workspace", str(tmp_path)]
    assert main(args + ["--strict"]) == 0
    facts_path = tmp_path / "output" / "facts.jsonl"
    lines = facts_path.read_text(encoding="utf-8").splitlines()
    tampered = [line.replace('"value":"3200000000000"', '"value":"-3200000000000"') for line in lines]
    assert tampered != lines
    facts_path.write_text("\n".join(tampered) + "\n", encoding="utf-8")
    assert main(args) == 0
    assert main(args + ["--strict"]) == 4
    assert "sign" in capsys.readouterr().out
