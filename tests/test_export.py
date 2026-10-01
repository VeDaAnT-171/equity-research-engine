"""The published snapshot must be the API's own answers, and must never carry the local machine."""

import json

import pytest

from research_engine.config import load_project_config
from research_engine.frameworks import FrameworkRegistry
from research_engine.pipeline import run_ingestion
from research_engine.pipeline.analysis import run_analysis_stage
from research_engine.pipeline.forecast import run_forecast_stage
from research_engine.pipeline.quality import run_quality_stage

from .conftest import FakeFetcher

pytest.importorskip("fastapi", reason="dashboard extras not installed")
from fastapi.testclient import TestClient  # noqa: E402

from research_engine.api import create_app  # noqa: E402
from research_engine.api import export as export_module  # noqa: E402
from research_engine.api.export import ExportRefused, export_static  # noqa: E402


@pytest.fixture
def workspace(tmp_path, sec_bank_config_path, frameworks_dir):
    frameworks = FrameworkRegistry(frameworks_dir)
    root = tmp_path / "companies"
    company = root / "nyse-exbk"
    company.mkdir(parents=True)
    (company / "config.yaml").write_text(sec_bank_config_path.read_text(encoding="utf-8"), encoding="utf-8")
    config = load_project_config(company / "config.yaml")
    run_ingestion(config, workspace=company, frameworks=frameworks, fetcher=FakeFetcher())
    run_quality_stage(config, workspace=company, frameworks=frameworks)
    run_analysis_stage(config, workspace=company, frameworks=frameworks)
    run_forecast_stage(config, workspace=company, frameworks=frameworks)
    return root


def test_the_snapshot_is_the_apis_own_answers(workspace, tmp_path):
    result = export_static(workspace, tmp_path / "site")
    site = tmp_path / "site"
    live = TestClient(create_app(workspace))
    forecast = json.loads((site / "api/companies/nyse-exbk/forecast.json").read_text(encoding="utf-8"))
    assert forecast == live.get("/api/companies/nyse-exbk/forecast").json()
    assert result["lineage_traces"] > 0 and not result["skipped"]
    # every value the dashboard renders as a trace button has its lineage file
    for scenario in forecast["scenarios"]:
        for metric in scenario["metrics"]:
            for point in metric["points"]:
                assert (site / f"api/companies/nyse-exbk/lineage/{point['value_id']}.json").is_file()


def test_the_page_says_it_is_a_snapshot_and_works_under_a_project_path(workspace, tmp_path):
    export_static(workspace, tmp_path / "site")
    html = (tmp_path / "site/index.html").read_text(encoding="utf-8")
    assert 'name="research-engine-snapshot"' in html
    # GitHub serves a project site under /<repo>/, where an absolute /static/ path would miss
    assert 'href="static/styles.css"' in html and 'src="static/app.js"' in html
    assert (tmp_path / "site/static/app.js").is_file() and (tmp_path / "site/.nojekyll").is_file()


def test_local_fields_are_removed(workspace, tmp_path):
    export_static(workspace, tmp_path / "site")
    health = json.loads((tmp_path / "site/api/health.json").read_text(encoding="utf-8"))
    assert "companies_root" not in health
    for path in (tmp_path / "site").rglob("*.json"):
        assert str(workspace) not in path.read_text(encoding="utf-8")


def test_the_export_refuses_rather_than_publish_the_workspace_path(workspace, tmp_path, monkeypatch):
    """The scrub is the first line; the scan is the one that must not fail open."""
    monkeypatch.setattr(export_module, "_scrub", lambda value: value)
    with pytest.raises(ExportRefused, match="workspace path"):
        export_static(workspace, tmp_path / "site")
    assert not (tmp_path / "site").exists(), "a refused export must write nothing"
    assert not (tmp_path / "site.incoming").exists()


def test_a_stage_not_run_is_exported_as_a_refusal_not_a_gap(workspace, tmp_path):
    (workspace / "nyse-exbk/output/forecast_manifest.json").unlink()
    export_static(workspace, tmp_path / "site")
    body = json.loads((tmp_path / "site/api/companies/nyse-exbk/forecast.json").read_text(encoding="utf-8"))
    assert body["__status"] == 409 and body["detail"]["stage"] == "forecast"
