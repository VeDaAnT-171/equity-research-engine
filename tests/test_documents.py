"""Documents: inline XBRL and table reading, verification against SEC data, and the library."""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from research_engine.calendar import FiscalCalendar
from research_engine.documents.extract import study_document, verify
from research_engine.documents.ixbrl import parse_number, read_inline_xbrl
from research_engine.documents.library import DocumentLibrary, sec_archive_accession
from research_engine.documents.tables import html_tables, pdf_tables
from research_engine.errors import ConfigError
from research_engine.frameworks import FrameworkRegistry
from research_engine.schemas.financial import (
    ExtractionMethod,
    FinancialFact,
    FiscalPeriodCode,
    Period,
    PeriodType,
    Provenance,
    SourceLocation,
)

CIK = "0009999002"


def ix_filing(cik: str = CIK, net_income_2025: str = "17,000") -> bytes:
    """A small inline XBRL 10-K: tagged statements, a dimensioned capital ratio, untagged tables."""
    return f"""<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"
      xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:xbrldi="http://xbrl.org/2006/xbrldi"
      xmlns:iso4217="http://www.xbrl.org/2003/iso4217" xmlns:us-gaap="http://fasb.org/us-gaap/2025"
      xmlns:dei="http://xbrl.sec.gov/dei/2025" xmlns:exbk="http://example.com/exbk"
      xmlns:srt="http://fasb.org/srt/2025">
<head><title>10-K</title></head><body>
<div style="display:none"><ix:header><ix:resources>
  <xbrli:context id="d2025"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">{cik}</xbrli:identifier></xbrli:entity>
    <xbrli:period><xbrli:startDate>2025-01-01</xbrli:startDate><xbrli:endDate>2025-12-31</xbrli:endDate></xbrli:period></xbrli:context>
  <xbrli:context id="d2024"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">{cik}</xbrli:identifier></xbrli:entity>
    <xbrli:period><xbrli:startDate>2024-01-01</xbrli:startDate><xbrli:endDate>2024-12-31</xbrli:endDate></xbrli:period></xbrli:context>
  <xbrli:context id="i2025"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">{cik}</xbrli:identifier></xbrli:entity>
    <xbrli:period><xbrli:instant>2025-12-31</xbrli:instant></xbrli:period></xbrli:context>
  <xbrli:context id="std"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">{cik}</xbrli:identifier>
    <xbrli:segment><xbrldi:explicitMember dimension="us-gaap:RiskWeightedAssetsCalculationMethodologyAxis">exbk:StandardizedMember</xbrldi:explicitMember>
    <xbrldi:explicitMember dimension="srt:ConsolidatedEntitiesAxis">srt:ParentCompanyMember</xbrldi:explicitMember></xbrli:segment></xbrli:entity>
    <xbrli:period><xbrli:instant>2025-12-31</xbrli:instant></xbrli:period></xbrli:context>
  <xbrli:context id="adv"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">{cik}</xbrli:identifier>
    <xbrli:segment><xbrldi:explicitMember dimension="us-gaap:RiskWeightedAssetsCalculationMethodologyAxis">exbk:AdvancedMember</xbrldi:explicitMember>
    <xbrldi:explicitMember dimension="srt:ConsolidatedEntitiesAxis">srt:ParentCompanyMember</xbrldi:explicitMember></xbrli:segment></xbrli:entity>
    <xbrli:period><xbrli:instant>2025-12-31</xbrli:instant></xbrli:period></xbrli:context>
  <xbrli:context id="bank"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">{cik}</xbrli:identifier>
    <xbrli:segment><xbrldi:explicitMember dimension="us-gaap:RiskWeightedAssetsCalculationMethodologyAxis">exbk:StandardizedMember</xbrldi:explicitMember>
    <xbrldi:explicitMember dimension="dei:LegalEntityAxis">exbk:BankSubsidiaryMember</xbrldi:explicitMember></xbrli:segment></xbrli:entity>
    <xbrli:period><xbrli:instant>2025-12-31</xbrli:instant></xbrli:period></xbrli:context>
  <xbrli:unit id="usd"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>
  <xbrli:unit id="number"><xbrli:measure>xbrli:pure</xbrli:measure></xbrli:unit>
</ix:resources></ix:header></div>
<p>CIK <ix:nonNumeric name="dei:EntityCentralIndexKey" contextRef="d2025">{cik}</ix:nonNumeric>
   form <ix:nonNumeric name="dei:DocumentType" contextRef="d2025">10-K</ix:nonNumeric></p>
<table>
 <tr><td>(in millions)</td><td>2025</td><td>2024</td></tr>
 <tr><td>Net income</td><td><ix:nonFraction name="us-gaap:NetIncomeLoss" contextRef="d2025" unitRef="usd" scale="6" decimals="-6" format="ixt:num-dot-decimal">{net_income_2025}</ix:nonFraction></td>
     <td><ix:nonFraction name="us-gaap:NetIncomeLoss" contextRef="d2024" unitRef="usd" scale="6" decimals="-6">15,000</ix:nonFraction></td></tr>
 <tr><td>Net interest income</td><td><ix:nonFraction name="us-gaap:InterestIncomeExpenseNet" contextRef="d2025" unitRef="usd" scale="6">33,000</ix:nonFraction></td><td></td></tr>
 <tr><td>Total assets</td><td><ix:nonFraction name="us-gaap:Assets" contextRef="i2025" unitRef="usd" scale="9">3,200</ix:nonFraction></td><td></td></tr>
</table>
<table>
 <tr><td>CET1 ratio</td>
   <td><ix:nonFraction name="exbk:CommonEquityTier1CapitaltoRiskWeightedAssets" contextRef="std" unitRef="number" scale="-2">13.5</ix:nonFraction></td>
   <td><ix:nonFraction name="exbk:CommonEquityTier1CapitaltoRiskWeightedAssets" contextRef="adv" unitRef="number" scale="-2">12.9</ix:nonFraction></td>
   <td><ix:nonFraction name="exbk:CommonEquityTier1CapitaltoRiskWeightedAssets" contextRef="bank" unitRef="number" scale="-2">14.1</ix:nonFraction></td></tr>
</table>
<p>Net interest income analysis</p>
<table>
 <tr><td colspan="3">Year ended December 31, (in millions, except rates)</td><td colspan="2">2025</td><td colspan="2">2024</td></tr>
 <tr><td colspan="3">Average interest-earning assets(a)</td><td>$</td><td>2,800,000</td><td>$</td><td>2,650,000</td></tr>
 <tr><td colspan="3">Net yield on average interest-earning assets</td><td>1.18</td><td>%</td><td>1.13</td><td>%</td></tr>
</table>
<table>
 <tr><td rowspan="3">(in millions)</td><td colspan="2">Period-end</td><td colspan="2">Average</td></tr>
 <tr><td rowspan="2">Dec 31, 2025</td><td rowspan="2">Dec 31, 2024</td><td colspan="2">Year ended December 31,</td></tr>
 <tr><td>2025</td><td>2024</td></tr>
 <tr><td>Tangible common equity</td><td>250,000</td><td>238,000</td><td>244,000</td><td>231,000</td></tr>
</table>
</body></html>""".encode()


def make_pdf(lines: list[str]) -> bytes:
    """A minimal one-page PDF whose text is `lines`, one per line."""
    esc = [line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") for line in lines]
    stream = "BT /F1 10 Tf 14 TL 40 780 Td " + " ".join(f"({t}) '" for t in esc) + " ET"
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream",
    ]
    out, offsets = "%PDF-1.4\n", []
    for i, body in enumerate(objects, 1):
        offsets.append(len(out.encode()))
        out += f"{i} 0 obj\n{body}\nendobj\n"
    xref = len(out.encode())
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n" + "".join(f"{o:010d} 00000 n \n" for o in offsets)
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n"
    return out.encode()


@pytest.fixture(scope="module")
def banks(frameworks_dir):
    return FrameworkRegistry(frameworks_dir).get("banks")


def _fact(metric: str, value: str, year: int, instant: bool = False) -> FinancialFact:
    from datetime import date

    from research_engine.schemas.financial import make_fact_id
    period = Period(period_type=PeriodType.INSTANT if instant else PeriodType.DURATION,
                    start=None if instant else date(year, 1, 1), end=date(year, 12, 31),
                    fiscal_year=year, fiscal_period=FiscalPeriodCode.FY)
    return FinancialFact(
        fact_id=make_fact_id("nyse-exbk", metric, period, Provenance.REPORTED, value), company_id="nyse-exbk",
        metric_id=metric, value=Decimal(value), unit="USD", currency="USD", period=period,
        provenance=Provenance.REPORTED, extraction_method=ExtractionMethod.XBRL,
        source=SourceLocation(document_id="doc_0000000000000001", xbrl_concept="us-gaap:X"))


KNOWN = [_fact("net_income", "17000000000", 2025), _fact("net_income", "15000000000", 2024),
         _fact("net_interest_income", "33000000000", 2025), _fact("total_assets", "3200000000000", 2025, instant=True)]


# ---- readers ----------------------------------------------------------------------------------------

def test_inline_xbrl_applies_scale_sign_and_dimensions():
    doc = read_inline_xbrl(ix_filing())
    assert doc is not None and doc.cik == CIK and doc.dei["DocumentType"] == "10-K"
    ni = [f for f in doc.facts if f.local_name == "NetIncomeLoss" and f.end.year == 2025]
    assert ni[0].value == Decimal("17000000000") and ni[0].unit == "USD" and ni[0].dimensions == ()
    cet1 = {dict(f.dimensions).get("RiskWeightedAssetsCalculationMethodologyAxis"): f.value
            for f in doc.facts if "CommonEquityTier1" in f.local_name and "ConsolidatedEntitiesAxis" in dict(f.dimensions)}
    assert cet1 == {"StandardizedMember": Decimal("0.135"), "AdvancedMember": Decimal("0.129")}


def test_a_plain_html_page_is_not_inline_xbrl():
    assert read_inline_xbrl(b"<html><body><table><tr><td>1</td></tr></table></body></html>") is None


@pytest.mark.parametrize("text,fmt,expected", [
    ("1,234.5", "ixt:num-dot-decimal", Decimal("1234.5")), ("1.234,5", "ixt:num-comma-decimal", Decimal("1234.5")),
    ("—", "ixt:fixed-zero", Decimal(0)), ("twelve", "ixt-sec:numwordsen", None)])
def test_number_formats(text, fmt, expected):
    assert parse_number(text, fmt) == expected


def test_table_headings_survive_rowspans_and_colspans():
    tables = list(html_tables(ix_filing()))
    tce = next(r for t in tables for r in t.rows if r.key == "tangible common equity")
    assert [(c.value, c.year) for c in tce.cells] == [(250000, 2025), (238000, 2024), (244000, 2025), (231000, 2024)]
    assert "Period-end" in tce.cells[0].heading and "Average" in tce.cells[2].heading
    aiea = next(r for t in tables for r in t.rows if r.key == "average interest-earning assets")
    assert [(c.value, c.year) for c in aiea.cells] == [(2800000, 2025), (2650000, 2024)]


def test_pdf_lines_become_year_keyed_rows():
    pdf = make_pdf(["Selected metrics (in millions)", "2025 2024", "Average interest-earning assets $ 2,800,000 $ 2,650,000",
                    "Net income $ 17,000 $ 15,000"])
    rows = {r.key: r for t in pdf_tables(pdf) for r in t.rows}
    assert [(c.value, c.year) for c in rows["net income"].cells] == [(17000, 2025), (15000, 2024)]


# ---- studying and verifying -------------------------------------------------------------------------

def _study(content: bytes, banks):
    return study_document(content, framework=banks, calendar=FiscalCalendar(12), currency="USD",
                          company_cik=CIK, known=KNOWN)


def test_a_filing_yields_tagged_and_printed_figures_with_their_source(banks):
    study = _study(ix_filing(), banks)
    got = {(f.metric_id, f.period.label): f for f in study.findings if not f.check_only}
    # The group's Standardized ratio: not the Advanced one, not the bank subsidiary's.
    assert got[("cet1_ratio", "FY2025")].value == Decimal("0.135")
    assert got[("cet1_ratio", "FY2025")].method is ExtractionMethod.XBRL
    aiea = got[("average_interest_earning_assets", "FY2025")]
    assert aiea.value == Decimal("2800000") * 10**6 and aiea.method is ExtractionMethod.HTML_TABLE
    assert "Average interest-earning assets" in aiea.source_text and aiea.where.startswith("Table")
    # Period-end column for a balance, never the average one.
    assert got[("tangible_common_equity", "FY2025")].value == Decimal("250000") * 10**6
    assert got[("tangible_common_equity", "FY2024")].value == Decimal("238000") * 10**6


def test_a_filing_that_agrees_with_the_sec_is_verified(banks):
    verdict = verify(_study(ix_filing(), banks), KNOWN)
    assert verdict.status == "verified" and verdict.checked >= 3 and verdict.agreed == verdict.checked


def test_a_filing_from_another_company_is_rejected(banks):
    verdict = verify(_study(ix_filing(cik="0000000042"), banks), KNOWN)
    assert verdict.status == "rejected" and "different company" in verdict.reason


def test_a_document_that_disagrees_with_the_sec_is_rejected(banks):
    tampered = ix_filing(net_income_2025="21,000").replace(b">33,000<", b">39,000<").replace(b">3,200<", b">3,900<")
    verdict = verify(_study(tampered, banks), KNOWN)
    assert verdict.status == "rejected" and verdict.disagreements


def test_a_document_with_nothing_to_check_is_not_used(banks):
    pdf = make_pdf(["(in millions)", "2025 2024", "Average interest-earning assets 2,800,000 2,650,000"])
    study = _study(pdf, banks)
    assert study.findings and verify(study, KNOWN).status == "unverified"


def test_a_pdf_that_matches_the_sec_figures_is_verified(banks):
    pdf = make_pdf(["Financial highlights (in millions)", "2025 2024", "Net income $ 17,000 $ 15,000",
                    "Net interest income 33,000 30,000", "Average interest-earning assets 2,800,000 2,650,000"])
    study = _study(pdf, banks)
    verdict = verify(study, [*KNOWN, _fact("net_interest_income", "30000000000", 2024)])
    assert verdict.status == "verified" and verdict.checked == 4
    assert any(f.metric_id == "average_interest_earning_assets" and not f.check_only for f in study.findings)


# ---- library ----------------------------------------------------------------------------------------

def test_library_stores_documents_by_content_and_refuses_other_files(tmp_path):
    library = DocumentLibrary(tmp_path)
    entry, new = library.add_file(ix_filing(), original_name="exbk-10k.htm", kind="annual_report", added_by="visitor")
    assert new and entry.media_type == "html" and library.path_of(entry).read_bytes() == ix_filing()
    again, new_again = library.add_file(ix_filing(), original_name="copy.htm", kind="annual_report")
    assert not new_again and again.id == entry.id
    with pytest.raises(ConfigError, match="HTML and PDF"):
        library.add_file(b"MZ\x90\x00 an executable", original_name="x.exe", kind="other")
    with pytest.raises(ConfigError, match="kind"):
        library.add_file(ix_filing(), original_name="x.htm", kind="spreadsheet")
    assert json.loads((tmp_path / "documents" / "index.json").read_text(encoding="utf-8"))["documents"][0]["id"] == entry.id


def test_only_edgar_archive_addresses_are_accepted_by_reference(tmp_path):
    url = "https://www.sec.gov/Archives/edgar/data/9999002/000999900226000010/exbk-20251231.htm"
    assert sec_archive_accession(url) == "0009999002-26-000010"
    entry, _ = DocumentLibrary(tmp_path).add_sec_filing(url, kind="annual_report")
    assert entry.url == url and entry.file is None
    for bad in ("https://example.com/report.pdf", "http://www.sec.gov/Archives/edgar/data/1/000000000000000001/a.htm",
                "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany"):
        with pytest.raises(ConfigError):
            DocumentLibrary(tmp_path).add_sec_filing(bad, kind="annual_report")


def test_uploaded_files_cannot_escape_the_library_folder(tmp_path):
    library = DocumentLibrary(tmp_path)
    entry, _ = library.add_file(ix_filing(), original_name="../../etc/passwd.htm", kind="other")
    assert entry.file == f"{entry.id}.htm" and entry.original_name == "passwd.htm"
    entry.file = "../config.yaml"
    assert library.path_of(entry) is None
