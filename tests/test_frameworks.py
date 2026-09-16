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
    (dirpath / f"{name}.yaml").write_text(textwrap.dedent(body))


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
