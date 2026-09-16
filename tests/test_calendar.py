from datetime import date

import pytest

from research_engine.calendar import FiscalCalendar
from research_engine.schemas import PeriodType

DEC = FiscalCalendar(12)
JUN = FiscalCalendar(6)
SEP = FiscalCalendar(9)


def label(cal, start, end):
    c = cal.classify(start, end)
    return c.period.label if c.period else c.skip_reason


@pytest.mark.parametrize("cal, start, end, expected", [
    (DEC, date(2025, 1, 1), date(2025, 12, 31), "FY2025"),
    (DEC, date(2025, 1, 1), date(2025, 3, 31), "Q1-2025"),
    (DEC, date(2025, 10, 1), date(2025, 12, 31), "Q4-2025"),
    (DEC, date(2025, 1, 1), date(2025, 6, 30), "H1-2025"),
    (DEC, date(2025, 7, 1), date(2025, 12, 31), "H2-2025"),
    (DEC, date(2025, 1, 1), date(2025, 9, 30), "nine_month_year_to_date"),
    (DEC, date(2025, 1, 1), date(2025, 2, 28), "period_end_not_fiscal_quarter_end"),
    (DEC, date(2025, 1, 1), date(2025, 5, 31), "period_end_not_fiscal_quarter_end"),
    (DEC, date(2024, 7, 1), date(2025, 6, 30), "annual_duration_not_ending_at_fiscal_year_end"),
    (DEC, date(2025, 1, 1), date(2025, 1, 31), "period_end_not_fiscal_quarter_end"),
    (DEC, date(2025, 4, 1), date(2025, 9, 30), "half_year_not_aligned_to_fiscal_halves"),
    # June year end: the quarter ending September 2025 is Q1 of FY2026
    (JUN, date(2025, 7, 1), date(2025, 9, 30), "Q1-2026"),
    (JUN, date(2024, 7, 1), date(2025, 6, 30), "FY2025"),
    # 52/53-week years ending on the last Saturday of September
    (SEP, date(2023, 10, 1), date(2024, 9, 28), "FY2024"),
    (SEP, date(2024, 9, 29), date(2024, 12, 28), "Q1-2025"),
    # 53-week "December" year that ends on 3 January
    (DEC, date(2024, 12, 29), date(2026, 1, 3), "FY2025"),
])
def test_durations(cal, start, end, expected):
    assert label(cal, start, end) == expected


def test_instants_map_to_fiscal_period_ends():
    fy = DEC.classify(None, date(2025, 12, 31)).period
    assert fy.period_type is PeriodType.INSTANT and fy.label == "FY2025" and fy.start is None
    assert DEC.classify(None, date(2025, 6, 30)).period.label == "Q2-2025"
    assert DEC.classify(None, date(2026, 2, 10)).skip_reason == "period_end_not_fiscal_quarter_end"


def test_start_year_convention():
    assert label(FiscalCalendar(6, "start_year"), date(2024, 7, 1), date(2025, 6, 30)) == "FY2024"
    # December year ends are unaffected by the convention
    assert label(FiscalCalendar(12, "start_year"), date(2025, 1, 1), date(2025, 12, 31)) == "FY2025"


def test_invalid_month():
    with pytest.raises(ValueError):
        FiscalCalendar(13)
