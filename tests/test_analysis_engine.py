from decimal import Decimal

import pytest

from research_engine.analysis import run_analytics
from research_engine.frameworks import FrameworkRegistry

from .factories import fy, fy_end, reported


@pytest.fixture(scope="module")
def generic(frameworks_dir):
    return FrameworkRegistry(frameworks_dir).get("generic")


@pytest.fixture(scope="module")
def industrials(frameworks_dir):
    return FrameworkRegistry(frameworks_dir).get("industrials")


def run(facts, framework, quality=None):
    return run_analytics(framework, facts, company_id="test-co", quality_report=quality)


def val(result, analytic, year):
    return result.by_analytic[analytic][year]


def equity_history():
    return [reported("net_income", 120, fy(2025)), reported("total_equity", 1000, fy_end(2024)),
            reported("total_equity", 1400, fy_end(2025))]


def test_average_basis_return(generic):
    r = run(equity_history(), generic)
    roe = val(r, "return_on_average_equity", 2025)
    assert roe.value == Decimal("0.1")  # 120 / ((1000 + 1400) / 2), not 120 / 1400
    assert roe.basis == "average" and roe.formula == "(net_income) / average(total_equity)"
    assert len(roe.input_fact_ids) == 3


def test_missing_opening_balance_is_labelled(generic):
    r = run([reported("net_income", 120, fy(2025)), reported("total_equity", 1400, fy_end(2025))], generic)
    assert "return_on_average_equity" not in r.by_analytic
    assert r.not_computed["return_on_average_equity:start_of_history:total_equity"] == 1


def test_opening_basis(industrials):
    facts = [reported("revenue", 900, fy(2025)), reported("backlog", 1200, fy_end(2024)), reported("backlog", 5000, fy_end(2025))]
    assert val(run(facts, industrials), "backlog_conversion", 2025).value == Decimal("0.75")


def test_growth_rules(generic):
    r = run([reported("revenue", 100, fy(2023)), reported("revenue", 110, fy(2024)), reported("revenue", 99, fy(2025))], generic)
    assert val(r, "revenue_growth", 2024).value == Decimal("0.1") and val(r, "revenue_growth", 2025).value == Decimal("-0.1")
    r = run([reported("net_income", -50, fy(2024)), reported("net_income", 20, fy(2025))], generic)
    assert r.not_computed["net_income_growth:growth_base_not_positive:net_income"] == 1
    r = run([reported("revenue", 100, fy(2022)), reported("revenue", 120, fy(2024))], generic)
    assert r.not_computed["revenue_growth:prior_year_missing:revenue"] == 1


def test_non_positive_denominator_not_computed(generic):
    r = run([reported("net_income", 10, fy(2025)), reported("total_equity", -300, fy_end(2024)),
             reported("total_equity", -100, fy_end(2025))], generic)
    assert r.not_computed["return_on_average_equity:denominator_not_positive"] == 1


def test_expression_chains_through_analytics(generic):
    facts = [reported("operating_income", 200, fy(2025)), reported("income_tax_expense", 45, fy(2025)),
             reported("pre_tax_income", 180, fy(2025))]
    r = run(facts, generic)
    etr, nopat = val(r, "effective_tax_rate", 2025), val(r, "nopat", 2025)
    assert etr.value == Decimal("0.25") and nopat.value == Decimal("150")
    assert etr.value_id in nopat.input_value_ids and nopat.currency == "USD"


def test_degree_of_operating_leverage(generic):
    facts = [reported("revenue", 100, fy(2024)), reported("revenue", 110, fy(2025)),
             reported("operating_income", 20, fy(2024)), reported("operating_income", 25, fy(2025))]
    assert val(run(facts, generic), "degree_of_operating_leverage", 2025).value == Decimal("2.5")  # 25% / 10%


def test_error_issue_blocks_and_warning_flags_propagate(generic):
    facts = equity_history() + [reported("revenue", 1000, fy(2025))]
    ni = facts[0]
    closing_equity = facts[2]
    quality = {"issues": [
        {"severity": "warning", "check": "balance_sheet_identity", "fact_ids": [closing_equity.fact_id]},
    ]}
    r = run(facts, generic, quality)
    assert val(r, "return_on_average_equity", 2025).quality_flags == ("balance_sheet_identity",)
    assert val(r, "net_margin", 2025).quality_flags == ()
    blocked = {"issues": [{"severity": "error", "check": "sign", "metric_id": "net_income", "period": "FY2025",
                           "fact_ids": [ni.fact_id]}]}
    r = run(facts, generic, blocked)
    assert "net_margin" not in r.by_analytic
    assert r.not_computed["net_margin:input_failed_quality:sign"] == 1


def test_value_ids_are_deterministic(generic):
    facts = equity_history()  # factories assign fresh accession numbers per call, so build the inputs once
    a, b = run(facts, generic), run(list(reversed(facts)), generic)
    assert [v.value_id for v in a.values] == [v.value_id for v in b.values]
