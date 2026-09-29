"""The hosted app: search the SEC index, analyse a company on demand, never expose the machine."""

import json
import os
import time

import pytest

from research_engine.config import load_project_config
from research_engine.frameworks import FrameworkRegistry
from research_engine.hosted.jobs import JobRunner, RunRefused
from research_engine.hosted.lookup import (
    INDEX_URL,
    CompanyIndex,
    IndexUnavailable,
    parse_index,
    write_config,
)

from .conftest import FakeFetcher

INDEX = json.dumps({
    "fields": ["cik", "name", "ticker", "exchange"],
    "data": [
        [9999002, "Example Bank Corp", "EXBK", "NYSE"],
        [9999002, "Example Bank Corp", "EXBK-PA", "NYSE"],    # a preferred series of the same company
        [9999003, "Paper Filer Trust", "PAPR", None],           # has no XBRL at the SEC
        [9999004, "Examplify Software Inc.", "EXSW", "Nasdaq"],
        [9999005, "Bank of Examples", "BOEX", "NYSE"],
    ],
}).encode()


def fetcher(**overrides):
    return FakeFetcher(overrides={INDEX_URL: INDEX, **overrides})


def wait(runner, job_id, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job, _ = runner.get(job_id)
        if job.state in ("done", "failed"):
            return job
        time.sleep(0.05)
    raise AssertionError("run did not finish")


# ---- the SEC index ------------------------------------------------------------------------

def test_one_company_is_one_cik_whatever_ticker_is_typed():
    primaries, by_ticker = parse_index(INDEX)
    assert [e.ticker for e in primaries] == ["EXBK", "PAPR", "EXSW", "BOEX"]
    # a secondary ticker resolves to the primary, so it cannot create a second workspace
    assert by_ticker["EXBK-PA"].ticker == "EXBK" and by_ticker["EXBK-PA"].cik == "0009999002"


def test_search_puts_an_exact_ticker_first_then_names(tmp_path):
    index = CompanyIndex(tmp_path / "index.json", fetcher())
    assert [e.ticker for e in index.search("boex")][0] == "BOEX"
    assert [e.ticker for e in index.search("Exampl")] == ["EXBK", "EXSW", "BOEX"]  # prefix, then contains
    assert index.search("exbk-pa")[0].ticker == "EXBK"
    assert index.search("nothing like this") == []


def test_a_failed_refresh_keeps_the_last_good_index(tmp_path):
    cache = tmp_path / "index.json"
    CompanyIndex(cache, fetcher()).search("EXBK")
    os.utime(cache, (0, 0))  # make it stale
    stale = CompanyIndex(cache, FakeFetcher())  # the index URL now 404s
    assert stale.search("EXBK")[0].ticker == "EXBK"
    with pytest.raises(IndexUnavailable):
        CompanyIndex(tmp_path / "none.json", FakeFetcher()).search("EXBK")


def test_the_config_is_built_from_the_index_not_from_the_visitor(tmp_path):
    entry = CompanyIndex(tmp_path / "i.json", fetcher()).get("9999002")
    path = write_config(entry, tmp_path / "companies")
    config = load_project_config(path)
    assert config.company_id == "nyse-exbk" and config.company.identifiers.cik == "0009999002"
    urls = [ref.url for ref in config.sources.structured_filings]
    assert urls == ["https://data.sec.gov/api/xbrl/companyfacts/CIK0009999002.json",
                    "https://data.sec.gov/submissions/CIK0009999002.json"]


# ---- running --------------------------------------------------------------------------------

@pytest.fixture
def runner(tmp_path, frameworks_dir):
    return JobRunner(tmp_path / "companies", FrameworkRegistry(frameworks_dir), fetcher)


@pytest.fixture
def index(tmp_path):
    return CompanyIndex(tmp_path / "index.json", fetcher())


def test_a_company_runs_through_every_stage_and_is_then_reused(runner, index):
    entry = index.get("9999002")
    job = wait(runner, runner.submit(entry, "a").job_id)
    assert job.state == "done", job.error
    assert job.stages_done == ["ingest", "quality", "analyze", "forecast"]
    assert (runner.root / "nyse-exbk" / "output" / "forecast_manifest.json").is_file()
    again = runner.submit(entry, "b")
    assert again.state == "done" and again.cached


def test_two_visitors_asking_for_one_company_share_a_run(runner, index):
    entry = index.get("9999002")
    first, second = runner.submit(entry, "a"), runner.submit(entry, "b")
    assert first.job_id == second.job_id
    wait(runner, first.job_id)


def test_a_company_without_xbrl_fails_clearly_and_leaves_nothing_behind(runner, index):
    job = wait(runner, runner.submit(index.get("9999003"), "a").job_id)
    assert job.state == "failed" and "Paper Filer Trust" in job.error
    assert "no structured financial statements" in job.error and "DocumentOutcome" not in job.error
    assert not (runner.root / "sec-papr").exists()
    assert "<workspace>" in job.error or str(runner.root) not in job.error


def test_new_runs_are_limited_per_visitor(tmp_path, frameworks_dir, index):
    limited = JobRunner(tmp_path / "c", FrameworkRegistry(frameworks_dir), fetcher, runs_per_requester=1)
    wait(limited, limited.submit(index.get("9999002"), "visitor").job_id)
    with pytest.raises(RunRefused) as refused:
        limited.submit(index.get("9999004"), "visitor")
    assert refused.value.retry_after and refused.value.retry_after > 0
    # a company already analysed is never refused: reuse costs nothing
    assert limited.submit(index.get("9999002"), "visitor").cached


# ---- the app ----------------------------------------------------------------------------------

fastapi = pytest.importorskip("fastapi", reason="dashboard extras not installed")
from fastapi.testclient import TestClient  # noqa: E402

from research_engine.hosted.app import create_hosted_app  # noqa: E402


@pytest.fixture
def hosted(tmp_path, frameworks_dir):
    data = tmp_path / "data"
    app = create_hosted_app(data, frameworks_dir, fetcher, start_seeds=False)
    return TestClient(app), data, app


def poll(client, job_id, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/api/runs/{job_id}").json()
        if body["state"] in ("done", "failed"):
            return body
        time.sleep(0.05)
    raise AssertionError("run did not finish")


def test_search_run_and_view_a_company_end_to_end(hosted):
    client, _, _ = hosted
    assert client.get("/api/app").json()["hosted"] is True
    [match] = [m for m in client.get("/api/search", params={"q": "EXBK"}).json() if m["ticker"] == "EXBK"]
    assert match["analysed"] is False
    started = client.post("/api/runs", json={"cik": match["cik"]})
    assert started.status_code == 202
    done = poll(client, started.json()["job_id"])
    assert done["state"] == "done", done["error"]
    assert client.get("/api/search", params={"q": "EXBK"}).json()[0]["analysed"] is True
    assert client.get("/api/companies/nyse-exbk/forecast").status_code == 200
    # asking again is immediate
    assert client.post("/api/runs", json={"cik": match["cik"]}).status_code == 200


def test_only_ciks_the_sec_knows_can_be_analysed(hosted):
    client, _, _ = hosted
    assert client.post("/api/runs", json={"cik": "123"}).status_code == 404
    assert client.post("/api/runs", json={"cik": "../../etc"}).status_code == 404


def test_no_response_reveals_where_the_server_keeps_its_files(hosted):
    client, data, _ = hosted
    job = client.post("/api/runs", json={"cik": "9999002"}).json()
    poll(client, job["job_id"])
    assert "companies_root" not in client.get("/api/health").json()
    base = "/api/companies/nyse-exbk"
    paths = ["/api/health", "/api/companies", base, f"{base}/documents", f"{base}/analytics",
             f"{base}/quality", f"{base}/forecast", f"{base}/assumptions", "/api/runs",
             "/api/companies/nope", f"{base}/lineage/nope"]
    for path in paths:
        text = client.get(path).text
        assert str(data) not in text and str(data.resolve()) not in text, path


def test_a_visitor_over_the_limit_gets_429_with_retry_after(tmp_path, frameworks_dir):
    app = create_hosted_app(tmp_path / "d", frameworks_dir, fetcher, start_seeds=False,
                            runner_options={"runs_per_requester": 1})
    client = TestClient(app)
    poll(client, client.post("/api/runs", json={"cik": "9999002"}).json()["job_id"])
    refused = client.post("/api/runs", json={"cik": "9999004"})
    assert refused.status_code == 429 and int(refused.headers["retry-after"]) > 0


def test_seed_companies_are_analysed_at_start_up(tmp_path, frameworks_dir):
    seeds = tmp_path / "seeds" / "nyse-exbk"
    seeds.mkdir(parents=True)
    entry = CompanyIndex(tmp_path / "i.json", fetcher()).get("9999002")
    write_config(entry, tmp_path / "seeds")
    app = create_hosted_app(tmp_path / "d", frameworks_dir, fetcher, seed_dir=tmp_path / "seeds")
    client = TestClient(app)
    active = client.get("/api/runs").json()
    finished = poll(client, active[0]["job_id"]) if active else {"state": "done"}
    assert finished["state"] == "done"
    assert client.get("/api/companies/nyse-exbk/forecast").status_code == 200
