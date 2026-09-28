"""Fiscal calendar: maps (start, end) dates onto fiscal years and periods.

Deliberately ignores the `fy`/`fp` fields in SEC data: those describe the *filing* that reported a
value, not the value's own period (a FY2025 10-K carries FY2024 and FY2023 comparatives tagged fy=2025).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

from .schemas.financial import FiscalPeriodCode, Period, PeriodType

# Duration buckets in days (inclusive). Widths absorb 52/53-week fiscal years.
ANNUAL_DAYS = (350, 380)
QUARTER_DAYS = (80, 100)
HALF_DAYS = (170, 195)
NINE_MONTH_DAYS = (260, 285)
# 52/53-week years can end in the first days of the following month (e.g. Jan 3 for a "December" year).
WEEK_YEAR_SPILLOVER_DAYS = 7

_QUARTER_CODES = {1: FiscalPeriodCode.Q1, 2: FiscalPeriodCode.Q2, 3: FiscalPeriodCode.Q3, 4: FiscalPeriodCode.Q4}


def effective_month(d: date) -> tuple[int, int]:
    if d.day <= WEEK_YEAR_SPILLOVER_DAYS:
        return (d.year - 1, 12) if d.month == 1 else (d.year, d.month - 1)
    return d.year, d.month


@dataclass(frozen=True)
class Classification:
    period: Period | None
    skip_reason: str | None = None


@dataclass(frozen=True)
class FiscalCalendar:
    fiscal_year_end_month: int
    convention: Literal["end_year", "start_year"] = "end_year"

    def __post_init__(self) -> None:
        if not 1 <= self.fiscal_year_end_month <= 12:
            raise ValueError("fiscal_year_end_month must be 1-12")

    def quarter_index(self, month: int) -> int | None:
        offset = (month - self.fiscal_year_end_month) % 12
        if offset % 3:
            return None
        return 4 if offset == 0 else offset // 3

    def fiscal_year(self, year: int, month: int) -> int:
        end_year = year if month <= self.fiscal_year_end_month else year + 1
        if self.convention == "end_year" or self.fiscal_year_end_month == 12:
            return end_year
        return end_year - 1

    def classify(self, start: date | None, end: date) -> Classification:
        year, month = effective_month(end)
        quarter = self.quarter_index(month)
        if quarter is None:
            return Classification(None, "period_end_not_fiscal_quarter_end")
        fy = self.fiscal_year(year, month)

        if start is None:
            code = FiscalPeriodCode.FY if quarter == 4 else _QUARTER_CODES[quarter]
            return Classification(Period(period_type=PeriodType.INSTANT, end=end, fiscal_year=fy, fiscal_period=code))

        if start >= end:
            return Classification(None, "invalid_period_dates")
        days = (end - start).days + 1
        if ANNUAL_DAYS[0] <= days <= ANNUAL_DAYS[1]:
            if quarter != 4:
                return Classification(None, "annual_duration_not_ending_at_fiscal_year_end")
            code = FiscalPeriodCode.FY
        elif QUARTER_DAYS[0] <= days <= QUARTER_DAYS[1]:
            code = _QUARTER_CODES[quarter]
        elif HALF_DAYS[0] <= days <= HALF_DAYS[1]:
            if quarter == 2:
                code = FiscalPeriodCode.H1
            elif quarter == 4:
                code = FiscalPeriodCode.H2
            else:
                return Classification(None, "half_year_not_aligned_to_fiscal_halves")
        elif NINE_MONTH_DAYS[0] <= days <= NINE_MONTH_DAYS[1]:
            if quarter != 3:
                return Classification(None, "nine_month_not_ending_at_fiscal_q3")
            code = FiscalPeriodCode.M9
        else:
            return Classification(None, "unsupported_duration")
        return Classification(Period(period_type=PeriodType.DURATION, start=start, end=end, fiscal_year=fy, fiscal_period=code))
