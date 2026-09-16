from decimal import Decimal

import pytest

from research_engine.frameworks import FrameworkRegistry
from research_engine.quality import Severity, run_quality

from .factories import duration, fy, fy_end, instant, reported


@pytest.fixture(scope="module")
def generic(frameworks_dir):
    return FrameworkRegistry(frameworks_dir).get("generic")


def run(facts, framework):
    return run_quality(facts, framework=framework, company_id="test-co", historical_years=3)[2]


def issues(report, check):
    return [i for i in report.issues if i.check == check]


def test_balance_sheet_identity_within_rounding(generic):
    facts = [reported("total_assets", 3_000_001_000, fy_end(2025)), reported("total_liabilities", 2_000_000_000, fy_end(2025)),
             reported("total_equity", 1_000_000_000, fy_end(2025))]
    report = run(facts, generic)
    assert report.checks["balance_sheet_identity"].passed == 1  # 1,000 difference, three terms rounded to thousands


def test_balance_sheet_identity_failure_is_a_warning_with_explanation(generic):
    facts = [reported("total_assets", 3_000_000_000, fy_end(2025)), reported("total_liabilities", 2_000_000_000, fy_end(2025)),
             reported("total_equity", 950_000_000, fy_end(2025))]
    [issue] = issues(run(facts, generic), "balance_sheet_identity")
    assert issue.severity is Severity.WARNING and "non-controlling" in issue.message
    assert issue.details["difference"] == "50000000"


def cash_facts(opening_end="2024-12-31", closing=1_050_000_000, fx=None):
    facts = [
        reported("operating_cash_flow", 200_000_000, fy(2025)),
        reported("investing_cash_flow", -100_000_000, fy(2025)),
        reported("financing_cash_flow", -50_000_000, fy(2025)),
        reported("net_change_in_cash", 50_000_000, fy(2025)),
        reported("cash_and_restricted_cash", 1_000_000_000, instant(2024, "FY", opening_end)),
        reported("cash_and_restricted_cash", closing, fy_end(2025)),
    ]
    if fx is not None:
        facts.append(reported("fx_effect_on_cash", fx, fy(2025)))
    return facts


def test_cash_checks_pass_with_optional_fx_assumed_zero(generic):
    report = run(cash_facts(), generic)
    assert report.checks["cash_flow_statement_sum"].passed == 1
    assert report.checks["cash_roll_forward"].passed == 1
    assert report.counts()["error"] == 0


def test_cash_flow_sum_fails_when_fx_breaks_it(generic):
    [issue] = issues(run(cash_facts(fx=7_000_000), generic), "cash_flow_statement_sum")
    assert issue.severity is Severity.ERROR


def test_roll_forward_failure_and_52_week_opening(generic):
    [issue] = issues(run(cash_facts(closing=1_060_000_000), generic), "cash_roll_forward")
    assert issue.severity is Severity.ERROR and issue.details["difference"] == "-10000000"
    # opening balance dated a few days before the period start (52/53-week calendar) is still found
    assert run(cash_facts(opening_end="2024-12-28"), generic).checks["cash_roll_forward"].passed == 1


def test_missing_inputs_are_not_evaluable_not_passed(generic):
    report = run([reported("total_assets", 3_000_000_000, fy_end(2025))], generic)
    s = report.checks["balance_sheet_identity"]
    assert (s.passed, s.failed, s.not_evaluable) == (0, 0, 1)


def test_negative_derived_quarter_is_a_sign_error(generic):
    facts = [reported("revenue", 500, duration(2025, "9M", "2025-01-01", "2025-09-30")), reported("revenue", 460, fy(2025))]
    [issue] = issues(run(facts, generic), "sign")
    assert issue.severity is Severity.ERROR and issue.period == "Q4-2025" and issue.details["provenance"] == "derived"


def test_scale_break(generic):
    report = run([reported("total_assets", 3_000_000, fy_end(2024)), reported("total_assets", 3_100_000_000, fy_end(2025))], generic)
    [issue] = issues(report, "scale_break")
    assert issue.period == "FY2025"
    report = run([reported("total_assets", 3_000_000, fy_end(2024)), reported("total_assets", 6_000_000, fy_end(2025))], generic)
    assert not issues(report, "scale_break")


def test_unusual_change_needs_history_and_uses_robust_z(generic):
    values = [100, 105, 110, 116, 121, 127, 400]  # steady ~5% growth, then a jump
    facts = [reported("revenue", v, fy(2019 + i)) for i, v in enumerate(values)]
    [issue] = issues(run(facts, generic), "unusual_change")
    assert issue.period == "FY2025" and issue.severity is Severity.INFO
    short = run(facts[-3:], generic)
    assert not issues(short, "unusual_change") and short.checks["unusual_change"].not_evaluable >= 1


def test_missing_years(generic):
    facts = [reported("revenue", 100, fy(2021)), reported("revenue", 110, fy(2023))]
    [issue] = issues(run(facts, generic), "missing_years")
    assert issue.details["missing"] == [2022]


def test_coverage_marks_reported_and_derived(generic):
    report = run([reported("revenue", 1000, fy(2025)), reported("cost_of_revenue", 600, fy(2025))], generic)
    assert report.coverage["revenue"] == {"FY2025": "R"} and report.coverage["gross_profit"] == {"FY2025": "D"}
