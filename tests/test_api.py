"""The API must never invent a number, and must never present a missing stage as an empty result."""

import json

import pytest

from research_engine.config import load_project_config
from research_engine.frameworks import FrameworkRegistry
from research_engine.pipeline import run_ingestion
from research_engine.pipeline.analysis import run_analysis_stage
from research_engine.pipeline.forecast import run_forecast_stage
from research_engine.pipeline.quality import run_quality_stage

from .conftest import FakeFetcher

fastapi = pytest.importorskip("fastapi", reason="dashboard extras not installed")
from fastapi.testclient import TestClient  # noqa: E402

from research_engine.api import create_app  # noqa: E402


@pytest.fixture
def frameworks(frameworks_dir):
    return FrameworkRegistry(frameworks_dir)


@pytest.fixture
def workspace(tmp_path, sec_bank_config_path, frameworks):
    """A companies root holding one company, taken through the whole pipeline."""
    root = tmp_path / "companies"
    company = root / "nyse-exbk"
    company.mkdir(parents=True)
    (company / "config.yaml").write_text(sec_bank_config_path.read_text(), encoding="utf-8")
    config = load_project_config(company / "config.yaml")
    run_ingestion(config, workspace=company, frameworks=frameworks, fetcher=FakeFetcher())
    run_quality_stage(config, workspace=company, frameworks=frameworks)
    run_analysis_stage(config, workspace=company, frameworks=frameworks)
    run_forecast_stage(config, workspace=company, frameworks=frameworks)
    return root


@pytest.fixture
def client(workspace):
    return TestClient(create_app(workspace))


@pytest.fixture
def bare(tmp_path, sec_bank_config_path):
    """A company that has been configured but never ingested."""
    root = tmp_path / "bare"
    company = root / "nyse-exbk"
    company.mkdir(parents=True)
    (company / "config.yaml").write_text(sec_bank_config_path.read_text(), encoding="utf-8")
    return TestClient(create_app(root))


# ---- discovery -----------------------------------------------------------------------------

def test_health_reports_engine_version(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok" and body["engine_version"] == "0.7.0"


def test_companies_are_discovered_from_configs(client):
    body = client.get("/api/companies").json()
    assert [c["company_id"] for c in body] == ["nyse-exbk"]
    assert body[0]["stages"] == {"ingest": True, "quality": True, "analyze": True, "forecast": True}


def test_unknown_company_is_404(client):
    r = client.get("/api/companies/not-a-company")
    assert r.status_code == 404 and "nyse-exbk" in r.json()["detail"]


def test_overview_carries_framework_evidence_and_counts(client):
    body = client.get("/api/companies/nyse-exbk").json()
    assert body["framework"]["name"] == "banks"
    assert "SIC" in body["framework"]["evidence"]
    assert body["counts"]["facts_current"] > 0
    assert body["counts"]["issues"] == {"error": 0, "warning": 1, "info": 2}


# ---- the values served are the values on disk -----------------------------------------------

def test_analytics_match_the_parquet_the_pipeline_wrote(client, workspace):
    import pyarrow.parquet as pq
    rows = pq.read_table(workspace / "nyse-exbk" / "output" / "historical_analytics.parquet").to_pylist()
    body = client.get("/api/companies/nyse-exbk/analytics").json()
    served = {(s["analytic_id"], p["fiscal_year"]): p["value"]
              for s in body["series"] for p in s["points"]}
    assert served == {(r["analytic_id"], r["fiscal_year"]): r["value"] for r in rows}


def test_forecast_matches_the_parquet_and_keeps_assumption_provenance(client, workspace):
    import pyarrow.parquet as pq
    rows = pq.read_table(workspace / "nyse-exbk" / "output" / "forecast.parquet").to_pylist()
    body = client.get("/api/companies/nyse-exbk/forecast").json()
    served = [(m["metric_id"], p["fiscal_year"], p["value"])
              for s in body["scenarios"] for m in s["metrics"] for p in m["points"]]
    assert sorted(served) == sorted((r["metric_id"], r["fiscal_year"], r["value"]) for r in rows)
    points = [p for s in body["scenarios"] for m in s["metrics"] for p in m["points"]]
    projected = [p for p in points if p["method"] != "actual"]
    assert projected and all(p["assumption_types"] or p["method"] in ("derivation", "driver_formula")
                             for p in projected)


def test_forecast_reports_refusals_and_demotions(client):
    body = client.get("/api/companies/nyse-exbk/forecast").json()
    assert body["not_projected"], "a sparse fixture must report what it could not project"
    assert "net_interest_margin" in body["demoted_derivations"]


def test_scenario_filter(client):
    r = client.get("/api/companies/nyse-exbk/forecast", params={"scenario": "base"})
    assert [s["id"] for s in r.json()["scenarios"]] == ["base"]
    assert client.get("/api/companies/nyse-exbk/forecast", params={"scenario": "bull"}).status_code == 404


def test_assumptions_expose_the_resolution_ladder(client):
    body = client.get("/api/companies/nyse-exbk/assumptions").json()
    assert body["resolution_order"] == ["scenario", "analyst_assumption", "management_guidance",
                                        "consensus", "historical"]
    assert all(a["type"] in {"historical", "derived"} for a in body["assumptions"]), \
        "with no analyst file, every assumption must be engine-seeded"


def test_quality_report_is_served_verbatim(client, workspace):
    on_disk = json.loads((workspace / "nyse-exbk" / "output" / "data_quality_report.json").read_text())
    assert client.get("/api/companies/nyse-exbk/quality").json() == on_disk


# ---- lineage --------------------------------------------------------------------------------

def test_any_analytic_value_traces_to_a_source_url(client):
    analytics = client.get("/api/companies/nyse-exbk/analytics").json()
    value_id = analytics["series"][0]["points"][0]["value_id"]
    trace = client.get(f"/api/companies/nyse-exbk/lineage/{value_id}").json()
    assert trace["source_urls"], "a model output with no source document is unauditable"
    assert {n["kind"] for n in trace["nodes"]} >= {"fact", "document", "source"}


def test_a_forecast_value_traces_through_its_assumption(client):
    body = client.get("/api/companies/nyse-exbk/forecast").json()
    projected = next(p for s in body["scenarios"] for m in s["metrics"]
                     for p in m["points"] if p["method"] == "growth")
    trace = client.get(f"/api/companies/nyse-exbk/lineage/{projected['value_id']}").json()
    assert "assumption" in {n["kind"] for n in trace["nodes"]}
    assert trace["source_urls"]


def test_unknown_lineage_node_is_404(client):
    assert client.get("/api/companies/nyse-exbk/lineage/fcv_deadbeefdeadbeef").status_code == 404


# ---- charts ---------------------------------------------------------------------------------

def test_charts_are_listed_and_served(client):
    charts = client.get("/api/companies/nyse-exbk/charts").json()
    assert charts and all(not c.get("skipped") for c in charts), "skipped charts must not be offered"
    r = client.get(f"/api/companies/nyse-exbk/charts/{charts[0]['chart_id']}.svg")
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/svg")


def test_chart_ids_cannot_escape_the_charts_directory(client):
    for attempt in ("../../manifest", "..%2f..%2fmanifest", "nope"):
        assert client.get(f"/api/companies/nyse-exbk/charts/{attempt}.svg").status_code in (404, 400)


# ---- missing stages are not empty results ----------------------------------------------------

def test_a_stage_that_never_ran_is_409_not_an_empty_payload(bare):
    assert bare.get("/api/companies").json()[0]["stages"]["ingest"] is False
    for path in ("/analytics", "/forecast", "/quality", "/assumptions", "/charts"):
        r = bare.get(f"/api/companies/nyse-exbk{path}")
        assert r.status_code == 409, path
        detail = r.json()["detail"]
        assert detail["stage"] in {"ingest", "quality", "analyze", "forecast"}
        assert "run `make" in detail["error"]


def test_overview_works_before_any_stage_has_run(bare):
    body = bare.get("/api/companies/nyse-exbk").json()
    assert body["name"] and body["framework"] is None and body["counts"] == {}


# ---- reports and the dashboard shell ----------------------------------------------------------

def test_markdown_reports_are_served(client):
    md = client.get("/api/companies/nyse-exbk/reports/forecast.md").text
    assert "MODEL OUTPUT" in md
    assert client.get("/api/companies/nyse-exbk/reports/passwd").status_code == 404


def test_dashboard_shell_and_assets_load(client):
    assert "Equity Research Engine" in client.get("/").text
    for asset in ("/static/app.js", "/static/styles.css"):
        assert client.get(asset).status_code == 200


def test_empty_workspace_is_not_an_error(tmp_path):
    (tmp_path / "empty").mkdir()
    c = TestClient(create_app(tmp_path / "empty"))
    assert c.get("/api/companies").json() == []
    assert c.get("/api/health").status_code == 200


# ---- forecast charts and the accessible fallbacks ---------------------------------------------

def test_forecast_charts_are_listed_and_served(client):
    charts = client.get("/api/companies/nyse-exbk/forecast/charts").json()
    assert charts, "at least one projected metric should be charted"
    assert all(not c.get("skipped") for c in charts), "skipped charts must not be offered"
    r = client.get(f"/api/companies/nyse-exbk/forecast/charts/{charts[0]['chart_id']}.svg")
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/svg")


def test_forecast_chart_ids_cannot_escape_their_directory(client):
    for attempt in ("../../manifest", "..%2f..%2fforecast", "nope"):
        assert client.get(
            f"/api/companies/nyse-exbk/forecast/charts/{attempt}.svg").status_code in (404, 400)


def test_every_chart_has_a_data_table_with_the_same_values(client):
    """A picture of a line is useless to a screen reader; the table is the accessible equivalent."""
    for c in client.get("/api/companies/nyse-exbk/charts").json():
        d = client.get(f"/api/companies/nyse-exbk/charts/{c['chart_id']}/data").json()
        assert d["columns"][0] == "series"
        assert [col for col in d["columns"] if col.startswith("FY")] == [f"FY{y}" for y in c["years"]]
        assert d["rows"] and all("series" in row for row in d["rows"])


def test_forecast_chart_table_matches_the_forecast_rows(client):
    charts = client.get("/api/companies/nyse-exbk/forecast/charts").json()
    chart = charts[0]
    table = client.get(
        f"/api/companies/nyse-exbk/forecast/charts/{chart['chart_id']}/data").json()
    forecast = client.get("/api/companies/nyse-exbk/forecast").json()
    metric = chart["metric_id"]
    for row in table["rows"]:
        scenario = next(s for s in forecast["scenarios"] if s["id"] == row["series"])
        served = next(m for m in scenario["metrics"] if m["metric_id"] == metric)
        for point in served["points"]:
            assert row[f"FY{point['fiscal_year']}"] == point["value"]


def test_chart_data_for_an_unknown_chart_is_404(client):
    assert client.get("/api/companies/nyse-exbk/charts/nope/data").status_code == 404
    assert client.get("/api/companies/nyse-exbk/forecast/charts/nope/data").status_code == 404


def test_coverage_matrix_is_served(client):
    body = client.get("/api/companies/nyse-exbk/coverage").json()
    assert body["framework"] == "banks" and body["coverage"] is not None


# ---- the schema is described, not left as a bare dict -----------------------------------------

def test_openapi_documents_real_response_shapes(client):
    spec = client.get("/api/openapi.json").json()
    assert len(spec["components"]["schemas"]) > 20
    analytics = spec["paths"]["/api/companies/{company_id}/analytics"]["get"]
    ref = analytics["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
    assert ref.endswith("/Analytics")
    assert "value_id" in json.dumps(spec["components"]["schemas"]["AnalyticPoint"])
