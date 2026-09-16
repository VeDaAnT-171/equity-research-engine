import json
from decimal import Decimal

import pytest

from research_engine.errors import ExtractionError
from research_engine.sources import SecCompanyFacts, SecSubmissions, find_source
from research_engine.sources.sec import SEC_SOURCES, normalize_unit

from .conftest import COMPANYFACTS_URL, SEC_FIXTURES, SUBMISSIONS_URL

DOC = "doc_0123456789abcdef"


def test_url_routing_and_identifiers():
    assert isinstance(find_source(COMPANYFACTS_URL, SEC_SOURCES), SecCompanyFacts)
    assert isinstance(find_source(SUBMISSIONS_URL, SEC_SOURCES), SecSubmissions)
    assert find_source("https://data.sec.gov/api/xbrl/frames/us-gaap/Assets/USD/CY2024Q4I.json", SEC_SOURCES) is None
    assert SecCompanyFacts().identifier_from_url(COMPANYFACTS_URL) == "0009999002"


def test_submissions_profile():
    p = SecSubmissions().normalize((SEC_FIXTURES / "submissions_CIK0009999002.json").read_bytes(), DOC)
    assert (p.cik, p.tickers, p.sic, p.fiscal_year_end_month) == ("0009999002", ("EXBK",), 6021, 12)


@pytest.mark.parametrize("fye, month", [("0930", 9), ("0103", 12), ("0628", 6)])
def test_fiscal_year_end_parsing(fye, month):
    body = json.dumps({"cik": "1", "name": "X", "fiscalYearEnd": fye}).encode()
    assert SecSubmissions().normalize(body, DOC).fiscal_year_end_month == month


def test_submissions_errors():
    with pytest.raises(ExtractionError, match="not valid JSON"):
        SecSubmissions().normalize(b"<html>blocked</html>", DOC)
    with pytest.raises(ExtractionError, match="missing field"):
        SecSubmissions().normalize(b'{"cik": "1"}', DOC)
    with pytest.raises(ExtractionError, match="fiscalYearEnd"):
        SecSubmissions().normalize(b'{"cik": "1", "name": "X", "fiscalYearEnd": "1340"}', DOC)


def test_companyfacts_parsing():
    parsed = SecCompanyFacts().normalize((SEC_FIXTURES / "companyfacts_CIK0009999002.json").read_bytes(), DOC)
    assert parsed.cik == "0009999002"
    assert parsed.malformed == 1  # the row without an accession number
    eps = [o for o in parsed.observations if o.concept == "EarningsPerShareDiluted"]
    assert eps[0].value == Decimal("5.25") and eps[0].unit == "USD/shares"
    instants = [o for o in parsed.observations if o.concept == "Assets"]
    assert all(o.start is None for o in instants)


def test_companyfacts_structure_errors():
    with pytest.raises(ExtractionError, match="missing 'cik' or 'facts'"):
        SecCompanyFacts().normalize(b'{"entityName": "X"}', DOC)


def test_unit_normalisation():
    assert normalize_unit("USD-per-shares") == "USD/shares"
    assert normalize_unit("USD") == "USD"
