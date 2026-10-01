"""A document added to a company's library: studied on ingest, used only to fill gaps, saved when verified."""

from __future__ import annotations

import base64
import json
import time

import pytest

from research_engine.config import load_project_config
from research_engine.documents.library import DocumentLibrary
from research_engine.frameworks import FrameworkRegistry
from research_engine.hosted.github import GitHubPersister
from research_engine.pipeline import run_ingestion

from .conftest import FakeFetcher
from .test_documents import ix_filing
from .test_hosted import fetcher as index_fetcher


@pytest.fixture
def frameworks(frameworks_dir):
    return FrameworkRegistry(frameworks_dir)


def _ingest(sec_bank_config_path, workspace, frameworks):
    config = load_project_config(sec_bank_config_path)
    return run_ingestion(config, workspace=workspace, frameworks=frameworks, fetcher=FakeFetcher())


def test_a_verified_document_fills_only_what_the_sec_data_lacks(sec_bank_config_path, tmp_path, frameworks):
    DocumentLibrary(tmp_path).add_file(ix_filing(), original_name="exbk-10k.htm", kind="annual_report")
    result = _ingest(sec_bank_config_path, tmp_path, frameworks)
    [report] = [d for d in result.documents if d["entry_id"]]
    assert report["status"] == "verified" and report["checked"] >= 3
    assert set(report["contributed"]) == {"cet1_ratio", "average_interest_earning_assets", "tangible_common_equity"}
    from_document = [f for f in result.facts if f.source and f.source.document_id == report["document_id"]]
    assert {f.metric_id for f in from_document} == set(report["contributed"])
    # net income is in the SEC data already: the document confirms it but never replaces it
    assert not [f for f in from_document if f.metric_id == "net_income"]
    written = json.loads((tmp_path / "output" / "documents.json").read_text(encoding="utf-8"))["documents"]
    assert any(d["status"] == "verified" for d in written)
    # the lineage of a figure read from a table names the table, and the file only by its name
    lineage = json.loads((tmp_path / "output" / "lineage.json").read_text(encoding="utf-8"))
    nodes = {n["id"]: n for n in lineage["nodes"]}
    aiea = next(f for f in from_document if f.metric_id == "average_interest_earning_assets")
    assert nodes[aiea.fact_id]["attributes"]["table"].startswith("Table")
    assert not any(str(tmp_path) in n["label"] for n in lineage["nodes"])


def test_a_document_that_contradicts_the_sec_data_adds_nothing(sec_bank_config_path, tmp_path, frameworks):
    tampered = ix_filing(net_income_2025="21,000").replace(b">33,000<", b">39,000<").replace(b">3,200<", b">3,900<")
    DocumentLibrary(tmp_path).add_file(tampered, original_name="fake.htm", kind="annual_report", added_by="visitor")
    result = _ingest(sec_bank_config_path, tmp_path, frameworks)
    [report] = [d for d in result.documents if d["entry_id"]]
    assert report["status"] == "rejected" and not report["contributed"]
    assert not [f for f in result.facts if f.source and f.source.document_id == report["document_id"]]


def test_a_second_run_reuses_the_library_without_refetching(sec_bank_config_path, tmp_path, frameworks):
    DocumentLibrary(tmp_path).add_file(ix_filing(), original_name="exbk-10k.htm", kind="annual_report")
    _ingest(sec_bank_config_path, tmp_path, frameworks)
    again = _ingest(sec_bank_config_path, tmp_path, frameworks)
    assert [d["status"] for d in again.documents if d["entry_id"]] == ["verified"]


# ---- hosted -----------------------------------------------------------------------------------------

fastapi = pytest.importorskip("fastapi", reason="dashboard extras not installed")
from fastapi.testclient import TestClient  # noqa: E402

from research_engine.hosted.app import create_hosted_app  # noqa: E402


class RecordingPersister:
    def __init__(self):
        self.proposed = []

    def propose(self, company_id, workspace, entry, *, summary=""):
        self.proposed.append((company_id, entry.id, summary))
        return f"https://github.com/owner/repo/pull/{len(self.proposed)}"


def _poll(client, job_id, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/api/runs/{job_id}").json()
        if body["state"] in ("done", "failed"):
            return body
        time.sleep(0.05)
    raise AssertionError("run did not finish")


@pytest.fixture
def app(tmp_path, frameworks_dir):
    persister = RecordingPersister()
    app = create_hosted_app(tmp_path / "data", frameworks_dir, index_fetcher, start_seeds=False,
                            persister=persister, uploads_per_hour=3)
    client = TestClient(app)
    first = client.post("/api/runs", json={"cik": "9999002"}).json()
    assert _poll(client, first["job_id"])["state"] == "done"
    return client, persister, tmp_path / "data"


def test_a_visitor_adds_a_document_and_everyone_sees_what_it_added(app):
    client, persister, _ = app
    info = client.get("/api/app").json()
    assert info["documents"] is True and info["documents_saved"] is True
    put = client.put("/api/companies/nyse-exbk/library", params={"filename": "exbk-10k.htm", "kind": "annual_report"},
                     content=ix_filing(), headers={"Content-Type": "text/html"})
    assert put.status_code == 202 and put.json()["new"] is True
    assert _poll(client, put.json()["run"]["job_id"])["state"] == "done"
    library = client.get("/api/companies/nyse-exbk/library").json()
    [doc] = library["documents"]
    assert doc["status"] == "verified" and doc["status_label"] == "In use" and doc["added_by"] == "visitor"
    assert {c["label"] for c in doc["contributed"]} >= {"CET1 ratio", "Average interest-earning assets"}
    assert persister.proposed and persister.proposed[0][:2] == ("nyse-exbk", doc["id"])
    financials = client.get("/api/companies/nyse-exbk/financials").json()
    cells = [v for s in financials["statements"] for r in s["rows"] for v in r["values"].values() if v.get("document")]
    assert cells and all(v["document"] == doc["title"] for v in cells)
    # the same file again is recognised, not re-analysed
    again = client.put("/api/companies/nyse-exbk/library", params={"filename": "copy.htm"}, content=ix_filing())
    assert again.status_code == 200 and again.json()["new"] is False


def test_uploads_are_checked_before_anything_runs(app):
    client, persister, _ = app
    bad = client.put("/api/companies/nyse-exbk/library", params={"filename": "x.exe"}, content=b"MZ\x90\x00")
    assert bad.status_code == 400
    assert client.put("/api/companies/nope/library", content=ix_filing()).status_code == 409
    assert client.put("/api/companies/../etc/library", content=ix_filing()).status_code in (404, 405)
    assert client.post("/api/companies/nyse-exbk/library", json={"url": "https://example.com/a.pdf"}).status_code == 400
    over = client.put("/api/companies/nyse-exbk/library", params={"filename": "x.pdf"}, content=b"%PDF-1.4 x")
    assert over.status_code in (200, 202)
    limited = client.put("/api/companies/nyse-exbk/library", params={"filename": "y.pdf"}, content=b"%PDF-1.4 y")
    assert limited.status_code == 429 and int(limited.headers["retry-after"]) > 0
    assert not persister.proposed


def test_a_rejected_upload_is_never_proposed_for_the_permanent_library(app):
    client, persister, _ = app
    tampered = ix_filing(net_income_2025="21,000").replace(b">33,000<", b">39,000<").replace(b">3,200<", b">3,900<")
    put = client.put("/api/companies/nyse-exbk/library", params={"filename": "fake.htm"}, content=tampered)
    _poll(client, put.json()["run"]["job_id"])
    [doc] = client.get("/api/companies/nyse-exbk/library").json()["documents"]
    assert doc["status"] == "rejected" and not persister.proposed


def test_an_uploaded_html_file_is_downloaded_never_rendered(app):
    client, _, _ = app
    put = client.put("/api/companies/nyse-exbk/library", params={"filename": "exbk.htm"}, content=ix_filing())
    entry_id = put.json()["document"]["id"]
    got = client.get(f"/api/companies/nyse-exbk/library/{entry_id}/file")
    assert got.status_code == 200 and got.headers["content-type"] == "application/octet-stream"
    assert "attachment" in got.headers["content-disposition"] and got.headers["content-security-policy"] == "sandbox"
    assert client.get("/api/companies/nyse-exbk/library/zzzz/file").status_code == 404


# ---- the pull request -------------------------------------------------------------------------------

class FakeGitHub:
    def __init__(self, has_company=True):
        self.calls = []
        self.has_company = has_company

    def __call__(self, req):
        import io
        import urllib.error
        method, url = req.get_method(), req.full_url
        body = json.loads(req.data) if req.data else None
        self.calls.append((method, url.split("/repos/owner/repo")[1], body))
        path = url.split("/repos/owner/repo")[1]
        if path.startswith("/contents/") and ("index.json" in path or not self.has_company):
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        replies = {
            ("GET", "/git/ref/heads/main"): {"object": {"sha": "base"}},
            ("GET", "/git/commits/base"): {"tree": {"sha": "tree0"}},
            ("POST", "/git/blobs"): {"sha": f"blob{len(self.calls)}"},
            ("POST", "/git/trees"): {"sha": "tree1"},
            ("POST", "/git/commits"): {"sha": "commit1"},
            ("POST", "/git/refs"): {},
            ("POST", "/pulls"): {"html_url": "https://github.com/owner/repo/pull/7"},
        }
        key = (method, path.split("?")[0])
        reply = replies.get(key, {"content": base64.b64encode(b"company: {}").decode()})

        class Response(io.BytesIO):
            pass
        return Response(json.dumps(reply).encode())


@pytest.mark.parametrize("has_company", [True, False])
def test_a_pull_request_carries_the_file_its_index_and_a_new_company(tmp_path, has_company):
    (tmp_path / "config.yaml").write_text("company: {}", encoding="utf-8")
    library = DocumentLibrary(tmp_path)
    entry, _ = library.add_file(ix_filing(), original_name="exbk.htm", kind="annual_report", added_by="visitor")
    github = FakeGitHub(has_company)
    url = GitHubPersister("t", "owner/repo", opener=github).propose("nyse-exbk", tmp_path, entry, summary="Agrees.")
    assert url.endswith("/pull/7")
    tree = next(body for method, path, body in github.calls if path == "/git/trees")["tree"]
    paths = {t["path"] for t in tree}
    assert f"companies/nyse-exbk/documents/{entry.file}" in paths and "companies/nyse-exbk/documents/index.json" in paths
    assert ("companies/nyse-exbk/config.yaml" in paths) is (not has_company)
    pull = next(body for method, path, body in github.calls if path == "/pulls")
    assert pull["head"] == f"library/nyse-exbk-{entry.id}" and pull["base"] == "main"
    assert all(req[0] in ("GET", "POST") for req in github.calls)  # never force-pushes, never touches main
