import shutil
import textwrap

import pytest

from research_engine.errors import FrameworkError
from research_engine.frameworks import FrameworkRegistry
from research_engine.schemas import ValuationFamily, ValuationMethod


def test_all_shipped_frameworks_resolve(frameworks_dir):
    resolved = FrameworkRegistry(frameworks_dir).validate_all()
    assert {"generic", "banks", "software", "industrials"} <= set(resolved)


def test_bank_inheritance_removes_industrial_concepts(frameworks_dir):
    banks = FrameworkRegistry(frameworks_dir).get("banks")
    ids = banks.metric_ids
    assert {"net_income", "total_assets", "net_interest_margin", "cet1_ratio"} <= ids  # inherited + own
    assert not {"gross_profit", "free_cash_flow", "cost_of_revenue"} & ids             # removed
    assert banks.metric("revenue").derivation == "net_interest_income + noninterest_income"  # overridden
    assert ValuationMethod.EV_EBITDA in banks.valuation.excluded
    assert banks.valuation.methods_for([ValuationFamily.RELATIVE]) == (
        ValuationMethod.PTBV, ValuationMethod.PB, ValuationMethod.PE)
    assert "capital_and_credit" in banks.report_sections


def test_sic_candidates(frameworks_dir):
    reg = FrameworkRegistry(frameworks_dir)
    assert reg.candidates_for_sic(6021) == ["banks"]
    assert reg.candidates_for_sic(7372) == ["software"]
    assert reg.candidates_for_sic(3531) == ["industrials"]
    assert reg.candidates_for_sic(5812) == []


@pytest.fixture
def scratch(tmp_path, frameworks_dir):
    shutil.copy(frameworks_dir / "generic.yaml", tmp_path / "generic.yaml")
    return tmp_path


def add(dirpath, name, body):
    (dirpath / f"{name}.yaml").write_text(textwrap.dedent(body), encoding="utf-8")


def test_unknown_metric_in_formula(scratch):
    add(scratch, "bad", """
        name: bad
        display_name: Bad
        extends: generic
        drivers:
          - id: x
            name: X
            affects: [revenue]
            formula: revenue * made_up_metric
    """)
    with pytest.raises(FrameworkError, match="made_up_metric"):
        FrameworkRegistry(scratch).get("bad")


def test_removal_that_breaks_derivation(scratch):
    add(scratch, "bad", """
        name: bad
        display_name: Bad
        extends: generic
        remove_metrics: [operating_cash_flow]
    """)
    with pytest.raises(FrameworkError, match="operating_cash_flow"):
        FrameworkRegistry(scratch).get("bad")


def test_circular_derivation(scratch):
    add(scratch, "bad", """
        name: bad
        display_name: Bad
        extends: generic
        metrics:
          - {id: a, name: A, unit_kind: ratio, statement: operating, period_type: duration, derivation: b * 2}
          - {id: b, name: B, unit_kind: ratio, statement: operating, period_type: duration, derivation: a / 2}
    """)
    with pytest.raises(FrameworkError, match="circular"):
        FrameworkRegistry(scratch).get("bad")


def test_inheritance_cycle_and_unknown_parent(scratch):
    add(scratch, "x", "name: x\ndisplay_name: X\nextends: y\n")
    add(scratch, "y", "name: y\ndisplay_name: Y\nextends: x\n")
    add(scratch, "orphan", "name: orphan\ndisplay_name: O\nextends: nowhere\n")
    reg = FrameworkRegistry(scratch)
    with pytest.raises(FrameworkError, match="cycle"):
        reg.get("x")
    with pytest.raises(FrameworkError, match="unknown framework 'nowhere'"):
        reg.get("orphan")


def test_name_must_match_file(scratch):
    add(scratch, "mismatch", "name: other\ndisplay_name: X\nextends: generic\n")
    with pytest.raises(FrameworkError, match="must match file name"):
        FrameworkRegistry(scratch)


def test_preferred_and_excluded_conflict(scratch):
    add(scratch, "bad", """
        name: bad
        display_name: Bad
        extends: generic
        valuation:
          preferred: [pe]
          excluded: {pe: "contradiction"}
    """)
    with pytest.raises(FrameworkError, match="both preferred and excluded"):
        FrameworkRegistry(scratch)


def test_unknown_framework_lists_available(frameworks_dir):
    with pytest.raises(FrameworkError, match="available: banks"):
        FrameworkRegistry(frameworks_dir).get("crypto")


def test_identity_checks_validated(scratch):
    add(scratch, "ratio_check", """
        name: ratio_check
        display_name: Bad
        extends: generic
        checks:
          - {id: bad, description: ratios are not identities, left: revenue / total_assets, right: revenue}
    """)
    with pytest.raises(FrameworkError, match="only \\+ and - are allowed"):
        FrameworkRegistry(scratch)


def test_identity_check_references(scratch):
    add(scratch, "bad", """
        name: bad
        display_name: Bad
        extends: generic
        checks:
          - {id: opening_flow, description: x, left: revenue__opening, right: revenue}
    """)
    with pytest.raises(FrameworkError, match="applies only to instant metrics"):
        FrameworkRegistry(scratch).get("bad")


def test_checks_inherit_and_can_be_removed(scratch, frameworks_dir):
    add(scratch, "child", """
        name: child
        display_name: Child
        extends: generic
        remove_checks: [balance_sheet_identity]
    """)
    reg = FrameworkRegistry(scratch)
    assert {c.id for c in reg.get("child").checks} == {"cash_flow_statement_sum", "cash_roll_forward"}
    assert {c.id for c in FrameworkRegistry(frameworks_dir).get("banks").checks} >= {"balance_sheet_identity"}


def test_flow_to_balance_derivation_rejected(scratch):
    add(scratch, "bad", """
        name: bad
        display_name: Bad
        extends: generic
        metrics:
          - {id: roe_naive, name: ROE, unit_kind: ratio, statement: operating, period_type: duration, derivation: net_income / total_equity}
    """)
    with pytest.raises(FrameworkError, match="explicit balance basis"):
        FrameworkRegistry(scratch).get("bad")


@pytest.mark.parametrize("analytic, fragment", [
    ("{id: x, name: X, category: growth, kind: growth, unit_kind: ratio}", "requires \\['metric'\\]"),
    ("{id: x, name: X, category: returns, kind: ratio, unit_kind: ratio, numerator: net_income, denominator: revenue, denominator_basis: average}", "needs a balance-sheet"),
    ("{id: x, name: X, category: growth, kind: growth, unit_kind: currency, metric: revenue}", "growth is a ratio"),
    ("{id: x, name: X, category: growth, kind: level, unit_kind: ratio, metric: made_up}", "unknown metrics or analytics"),
    ("{id: revenue, name: X, category: growth, kind: level, unit_kind: ratio, metric: revenue}", "collide"),
])
def test_analytic_validation(scratch, analytic, fragment):
    add(scratch, "bad", f"""
        name: bad
        display_name: Bad
        extends: generic
        analytics:
          - {analytic}
    """)
    with pytest.raises(FrameworkError, match=fragment):
        FrameworkRegistry(scratch).get("bad")


def test_analytic_cycle_and_chart_references(scratch):
    add(scratch, "cyclic", """
        name: cyclic
        display_name: Cyclic
        extends: generic
        analytics:
          - {id: a1, name: A, category: operating, kind: expression, unit_kind: ratio, formula: a2 * 2}
          - {id: a2, name: B, category: operating, kind: expression, unit_kind: ratio, formula: a1 / 2}
    """)
    add(scratch, "badchart", """
        name: badchart
        display_name: Bad chart
        extends: generic
        charts:
          - {id: c, title: C, kind: line, series: [nowhere], format: percent}
    """)
    reg = FrameworkRegistry(scratch)
    with pytest.raises(FrameworkError, match="circular"):
        reg.get("cyclic")
    with pytest.raises(FrameworkError, match="unknown series"):
        reg.get("badchart")


def test_bank_analytics_replace_industrial_ones(frameworks_dir):
    banks = FrameworkRegistry(frameworks_dir).get("banks")
    ids = banks.analytic_ids
    assert {"rotce", "credit_loss_rate", "efficiency_ratio", "return_on_average_equity"} <= ids
    assert not {"roic", "gross_margin", "degree_of_operating_leverage", "net_debt"} & ids
    assert banks.analytic("credit_loss_rate").denominator_basis == "average"
    assert {c.id for c in banks.charts} >= {"revenue", "bank_returns"} and "margins" not in {c.id for c in banks.charts}
