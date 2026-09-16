import textwrap

import pytest

from research_engine.config import load_project_config
from research_engine.errors import ConfigError
from research_engine.schemas import DocumentType, ValuationFamily


def write(tmp_path, body: str):
    p = tmp_path / "company.yaml"
    p.write_text(textwrap.dedent(body))
    return p


MINIMAL = """
company:
  name: Test Co
  ticker: TST
  exchange: NASDAQ
  country: United States
sources:
  annual_reports:
    - url: https://example.com/ar.pdf
"""


def test_loads_fixtures(bank_config_path, industrial_config_path):
    bank = load_project_config(bank_config_path)
    ind = load_project_config(industrial_config_path)
    assert bank.company_id == "nyse-exbk"
    assert bank.company.ticker == "EXBK"                 # normalised to upper case
    assert bank.company.identifiers.cik == "0009999002"  # zero-padded
    assert bank.research.valuation_methods == (ValuationFamily.INTRINSIC, ValuationFamily.RELATIVE)
    assert ind.company_id == "xetra-exin"
    assert ind.company.reporting_currency == "EUR"


def test_blank_placeholders_are_dropped(bank_config_path):
    cfg = load_project_config(bank_config_path)
    assert cfg.sources.optional_consensus is None
    assert len(cfg.sources.quarterly_reports) == 1
    types = [t for t, _ in cfg.sources.iter_documents()]
    assert types.count(DocumentType.QUARTERLY_REPORT) == 1
    assert DocumentType.INVESTOR_RELATIONS_SITE in types


def test_relative_paths_resolve_against_config_dir(industrial_config_path):
    cfg = load_project_config(industrial_config_path)
    path = cfg.sources.annual_reports[0].path
    assert path.is_absolute()
    assert path.parent.parent == industrial_config_path.parent.resolve()


def test_minimal_config_defaults(tmp_path):
    cfg = load_project_config(write(tmp_path, MINIMAL))
    assert cfg.research.historical_years == 10 and cfg.research.forecast_years == 5
    assert cfg.company.industry_framework is None


@pytest.mark.parametrize(
    "url, fragment",
    [
        ("http://example.com/a.pdf", "only https"),
        ("file:///etc/passwd", "only https"),
        ("https://127.0.0.1/a.pdf", "non-public"),
        ("https://10.0.0.5/a.pdf", "non-public"),
        ("https://localhost/a.pdf", "internal host"),
        ("https://user:pw@example.com/a.pdf", "credentials"),
    ],
)
def test_unsafe_urls_rejected(tmp_path, url, fragment):
    body = MINIMAL.replace("https://example.com/ar.pdf", url)
    with pytest.raises(ConfigError, match=fragment):
        load_project_config(write(tmp_path, body))


@pytest.mark.parametrize(
    "mutation, fragment",
    [
        (lambda s: s.replace("ticker: TST", "ticker: 'bad ticker!'"), "invalid ticker"),
        (lambda s: s.replace("country: United States", "country: United States\n  colour: blue"), "Extra inputs"),
        (lambda s: s.replace("- url: https://example.com/ar.pdf", "- url: https://example.com/ar.pdf\n      path: x.pdf"), "exactly one"),
        (lambda s: s.replace("- url: https://example.com/ar.pdf", "- url: https://example.com/ar.pdf\n      fiscal_period: 2025"), "fiscal_period"),
        (lambda s: s + "research:\n  forecast_years: 40\n", "forecast_years"),
        (lambda s: s + "research:\n  valuation_methods: [astrology]\n", "valuation_methods"),
    ],
)
def test_invalid_configs_rejected(tmp_path, mutation, fragment):
    with pytest.raises(ConfigError, match=fragment):
        load_project_config(write(tmp_path, mutation(MINIMAL)))


def test_requires_primary_source(tmp_path):
    body = """
    company: {name: A, ticker: A, exchange: X, country: Y}
    sources:
      investor_relations: {url: "https://example.com/ir"}
    """
    with pytest.raises(ConfigError, match="no primary source documents"):
        load_project_config(write(tmp_path, body))


def test_duplicate_sources_rejected(tmp_path):
    body = MINIMAL + "  quarterly_reports:\n    - url: https://example.com/ar.pdf\n"
    with pytest.raises(ConfigError, match="more than once"):
        load_project_config(write(tmp_path, body))


def test_missing_file_and_bad_yaml(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_project_config(tmp_path / "nope.yaml")
    with pytest.raises(ConfigError, match="invalid YAML"):
        load_project_config(write(tmp_path, "company: [unclosed"))
