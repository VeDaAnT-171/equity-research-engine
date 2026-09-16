from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
FRAMEWORKS = ROOT / "industry_frameworks"


@pytest.fixture
def root() -> Path:
    return ROOT


@pytest.fixture
def frameworks_dir() -> Path:
    return FRAMEWORKS


@pytest.fixture
def bank_config_path() -> Path:
    return FIXTURES / "companies" / "example_bank.yaml"


@pytest.fixture
def industrial_config_path() -> Path:
    return FIXTURES / "companies" / "example_industrial.yaml"


SEC_FIXTURES = FIXTURES / "sec"
COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK0009999002.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK0009999002.json"
ANNUAL_REPORT_URL = "https://example.com/exbk/annual-report-2025.pdf"


class FakeFetcher:
    """Serves fixture bytes by URL; unknown URLs fail like a 404."""

    def __init__(self, overrides=None, fail=()):
        from research_engine.ingestion import FetchResult  # noqa: F401
        self.responses = {
            COMPANYFACTS_URL: (SEC_FIXTURES / "companyfacts_CIK0009999002.json").read_bytes(),
            SUBMISSIONS_URL: (SEC_FIXTURES / "submissions_CIK0009999002.json").read_bytes(),
            ANNUAL_REPORT_URL: b"%PDF-1.7 synthetic annual report",
        }
        self.responses.update(overrides or {})
        self.fail = set(fail)
        self.calls = []

    def fetch(self, url):
        from research_engine.errors import FetchError
        from research_engine.ingestion import FetchResult
        self.calls.append(url)
        if url in self.fail or url not in self.responses:
            raise FetchError(f"HTTP 404 for {url}")
        ctype = "application/pdf" if url.endswith(".pdf") else "application/json"
        return FetchResult(self.responses[url], ctype, url)


@pytest.fixture
def fake_fetcher():
    return FakeFetcher()


@pytest.fixture
def sec_bank_config_path() -> Path:
    return FIXTURES / "companies" / "sec_bank.yaml"
