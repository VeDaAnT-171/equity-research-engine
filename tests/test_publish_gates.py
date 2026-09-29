"""The exit codes the Pages workflow relies on to refuse publishing.

The workflow publishes to a public page on a schedule, with nobody watching. Its only protection is
that a bad run exits non-zero before the export step. These tests pin the two exits it depends on,
so a refactor of the CLI cannot quietly turn a failed run into a published one.
"""

from pathlib import Path
from types import SimpleNamespace

from research_engine import cli
from research_engine.quality.model import QualityIssue, QualityReport, Severity


def _report(*severities):
    report = QualityReport(company_id="nyse-exbk", framework="banks", historical_years=10)
    for i, severity in enumerate(severities):
        report.add(QualityIssue(f"check_{i}", severity, "synthetic", None))
    return report


def _quality_result(report):
    return SimpleNamespace(report=report, current=[], derived=[], output_dir=Path("out"))


def test_quality_strict_fails_the_run_on_an_error(monkeypatch, sec_bank_config_path, frameworks_dir):
    monkeypatch.setattr(cli, "run_quality_stage", lambda *a, **k: _quality_result(_report(Severity.ERROR)))
    args = ["quality", "--config", str(sec_bank_config_path), "--frameworks", str(frameworks_dir), "--strict"]
    assert cli.main(args) == 4


def test_quality_strict_publishes_through_warnings(monkeypatch, sec_bank_config_path, frameworks_dir):
    """Warnings are findings shown on the page, not failures; blocking on them would never publish."""
    report = _report(Severity.WARNING, Severity.INFO)
    monkeypatch.setattr(cli, "run_quality_stage", lambda *a, **k: _quality_result(report))
    args = ["quality", "--config", str(sec_bank_config_path), "--frameworks", str(frameworks_dir), "--strict"]
    assert cli.main(args) == 0


def test_ingest_fails_the_run_when_a_source_could_not_be_retrieved(monkeypatch, sec_bank_config_path,
                                                                   frameworks_dir, tmp_path):
    result = SimpleNamespace(company_id="nyse-exbk", output_dir=tmp_path, outcomes=[], profile=None,
                             framework=None, extraction=None, warnings=[], failures=["companyfacts: 503"])
    monkeypatch.setattr(cli, "run_ingestion", lambda *a, **k: result)
    monkeypatch.setenv("SEC_USER_AGENT", "Test Runner test@example.com")
    args = ["ingest", "--config", str(sec_bank_config_path), "--frameworks", str(frameworks_dir),
            "--workspace", str(tmp_path)]
    assert cli.main(args) == 3
