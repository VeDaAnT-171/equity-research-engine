import json

import pytest

from research_engine.cli import main
from research_engine.config import load_project_config
from research_engine.errors import ConfigError, ExtractionError
from research_engine.frameworks import FrameworkRegistry
from research_engine.lineage import NodeKind
from research_engine.pipeline import run_ingestion
from research_engine.schemas import DocumentStatus

from .conftest import ANNUAL_REPORT_URL, COMPANYFACTS_URL, SUBMISSIONS_URL, FakeFetcher


@pytest.fixture
def frameworks(frameworks_dir):
    return FrameworkRegistry(frameworks_dir)


@pytest.fixture
def config(sec_bank_config_path):
    return load_project_config(sec_bank_config_path)


def ingest(config, tmp_path, frameworks, fetcher, **kw):
    return run_ingestion(config, workspace=tmp_path, frameworks=frameworks, fetcher=fetcher, **kw)


def test_end_to_end_outputs(config, tmp_path, frameworks, fake_fetcher):
    result = ingest(config, tmp_path, frameworks, fake_fetcher)
    assert not result.failures
    assert result.framework.name == "banks" and result.framework.method == "sic_match"
    assert result.calendar.fiscal_year_end_month == 12  # from SEC, not config
    out = tmp_path / "output"
    for name in ("facts.jsonl", "facts_current.csv", "historical_annual.csv", "historical_interim.csv", "lineage.json",
                 "extraction_report.json", "extraction_report.md", "sources.md", "manifest.json"):
        assert (out / name).is_file(), name
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["framework"]["name"] == "banks" and manifest["parser_version"]
    annual = (out / "historical_annual.csv").read_text(encoding="utf-8").splitlines()
    assert "revenue,USD,50000000000,54500000000,60000000000" in annual
    # The configured PDF is studied, and a document that cannot be checked is reported, not used.
    assert result.documents and all(d["status"] != "verified" for d in result.documents)
    assert json.loads((out / "documents.json").read_text(encoding="utf-8"))["documents"]


def test_lineage_from_csv_value_to_source_url(config, tmp_path, frameworks, fake_fetcher):
    ingest(config, tmp_path, frameworks, fake_fetcher)
    graph = json.loads((tmp_path / "output" / "lineage.json").read_text(encoding="utf-8"))
    nodes = {n["id"]: n for n in graph["nodes"]}
    parents = {}
    for e in graph["edges"]:
        parents.setdefault(e["child"], []).append(e["parent"])
    fact = next(n for n in graph["nodes"] if n["kind"] == NodeKind.FACT.value and n["label"] == "revenue")
    doc = nodes[parents[fact["id"]][0]]
    src = nodes[parents[doc["id"]][0]]
    assert doc["kind"] == "document" and src["label"] == COMPANYFACTS_URL
    assert fact["attributes"]["filing_accession"].startswith("0009999002-")


def test_second_run_uses_cache(config, tmp_path, frameworks, fake_fetcher):
    ingest(config, tmp_path, frameworks, fake_fetcher)
    calls = len(fake_fetcher.calls)
    result = ingest(config, tmp_path, frameworks, fake_fetcher)
    assert len(fake_fetcher.calls) == calls
    assert {o.action for o in result.outcomes} == {"cached"}


def test_refresh_with_changed_filing_creates_new_version(config, tmp_path, frameworks, fake_fetcher):
    first = ingest(config, tmp_path, frameworks, fake_fetcher)
    body = json.loads(fake_fetcher.responses[COMPANYFACTS_URL])
    body["facts"]["us-gaap"]["NetIncomeLoss"]["units"]["USD"].append({
        "start": "2026-01-01", "end": "2026-03-31", "val": 4000000000, "accn": "0009999002-26-000020",
        "fy": 2026, "fp": "Q1", "form": "10-Q", "filed": "2026-05-01"})
    fake_fetcher.responses[COMPANYFACTS_URL] = json.dumps(body).encode()
    second = ingest(config, tmp_path, frameworks, fake_fetcher, refresh=True)
    actions = {o.record.source_url: o.action for o in second.outcomes}
    assert actions[COMPANYFACTS_URL] == "new_version" and actions[SUBMISSIONS_URL] == "cached"
    assert len(second.facts) == len(first.facts) + 1
    sources = (tmp_path / "output" / "sources.md").read_text(encoding="utf-8")
    assert "superseded" in sources


def test_document_failure_is_recorded_and_pipeline_continues(config, tmp_path, frameworks):
    result = ingest(config, tmp_path, frameworks, FakeFetcher(fail={ANNUAL_REPORT_URL}))
    [failure] = result.failures
    assert failure.record.status is DocumentStatus.FAILED and "404" in failure.record.error
    assert result.facts
    # retry succeeds on a later run
    retry = ingest(config, tmp_path, frameworks, FakeFetcher())
    assert not retry.failures


def test_offline_without_cache_fails_each_document(config, tmp_path, frameworks):
    result = ingest(config, tmp_path, frameworks, None, offline=True)
    assert len(result.failures) == 3 and not result.facts
    assert any("no XBRL companyfacts" in w for w in result.warnings)


def test_ticker_mismatch_is_fatal(tmp_path, frameworks, sec_bank_config_path):
    text = sec_bank_config_path.read_text(encoding="utf-8").replace('ticker: "EXBK"', 'ticker: "WRONG"')
    cfg_path = tmp_path / "c.yaml"
    cfg_path.write_text(text, encoding="utf-8")
    with pytest.raises(ExtractionError, match="identity mismatch: ticker WRONG"):
        ingest(load_project_config(cfg_path), tmp_path, frameworks, FakeFetcher())


def test_cik_mismatch_caught_before_network(tmp_path, frameworks, sec_bank_config_path):
    text = sec_bank_config_path.read_text(encoding="utf-8").replace('cik: "0009999002"', 'cik: "0009999003"')
    cfg_path = tmp_path / "c.yaml"
    cfg_path.write_text(text, encoding="utf-8")
    fetcher = FakeFetcher()
    with pytest.raises(ConfigError, match="CIK mismatch"):
        ingest(load_project_config(cfg_path), tmp_path, frameworks, fetcher)
    assert fetcher.calls == []


def test_fiscal_year_end_conflict(tmp_path, frameworks, sec_bank_config_path):
    text = sec_bank_config_path.read_text(encoding="utf-8").replace('reporting_currency: "USD"', 'reporting_currency: "USD"\n  fiscal_year_end_month: 6')
    cfg_path = tmp_path / "c.yaml"
    cfg_path.write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError, match="SEC reports month 12"):
        ingest(load_project_config(cfg_path), tmp_path, frameworks, FakeFetcher())


def test_config_override_beats_sic(tmp_path, frameworks, sec_bank_config_path):
    text = sec_bank_config_path.read_text(encoding="utf-8").replace('reporting_currency: "USD"', 'reporting_currency: "USD"\n  industry_framework: generic')
    cfg_path = tmp_path / "c.yaml"
    cfg_path.write_text(text, encoding="utf-8")
    result = ingest(load_project_config(cfg_path), tmp_path, frameworks, FakeFetcher())
    assert result.framework.method == "config_override" and "suggests banks" in result.framework.evidence


def test_cli_ingest_offline_after_cache(config, sec_bank_config_path, tmp_path, frameworks, fake_fetcher, frameworks_dir, capsys):
    ingest(config, tmp_path, frameworks, fake_fetcher)
    code = main(["ingest", "--config", str(sec_bank_config_path), "--frameworks", str(frameworks_dir),
                 "--workspace", str(tmp_path), "--offline", "--env-file", str(tmp_path / "none.env")])
    out = capsys.readouterr().out
    assert code == 0 and "framework:  banks via sic_match" in out


def test_cli_ingest_requires_sec_user_agent(sec_bank_config_path, tmp_path, frameworks_dir, monkeypatch, capsys):
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    code = main(["ingest", "--config", str(sec_bank_config_path), "--frameworks", str(frameworks_dir),
                 "--workspace", str(tmp_path), "--env-file", str(tmp_path / "none.env")])
    assert code == 1 and "SEC_USER_AGENT" in capsys.readouterr().err
