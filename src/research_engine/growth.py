"""When a year-on-year growth rate carries meaning, and why it so often does not.

A percentage change describes movement along a scale that has a fixed direction. That description
survives only while both endpoints sit on the same side of zero. Two cases break it:

- The base is zero or negative. `-100 -> -50` computes to `-50%`, which reads as a decline while
  the quantity in fact improved by half.
- The base is positive and the current value is not. `+100 -> -50` computes to `-150%`, a number
  with no interpretation: the quantity did not shrink by one and a half times itself, it crossed
  a boundary that percentage change cannot express.

Refusing only the first case is the trap, because it looks like a guard while letting the second
through. A series that swings either side of zero — a bank's operating cash flow, most obviously —
then yields a table of numbers that are individually absurd and collectively summarisable, and a
median taken over them is compounded forward as though it were a trend.

Both the historical analytics and the forecast seeder ask the same question, so they ask it here.
A refusal names which case it is, because `growth_sign_change` and `growth_base_not_positive` mean
different things to a reader deciding whether the gap is a data problem or the series' nature.
"""

from __future__ import annotations

from decimal import Decimal

GROWTH_BASE_NOT_POSITIVE = "growth_base_not_positive"
GROWTH_SIGN_CHANGE = "growth_sign_change"


def growth_refusal(previous: Decimal | None, current: Decimal | None) -> str | None:
    """Why year-on-year growth cannot be stated for this pair, or None when it can.

    Both values must be present and strictly positive. Zero is refused as a base because the
    ratio is undefined, and as a current value because `x -> 0` is a total loss rather than a
    -100% rate that could be projected forward.
    """
    if previous is None or previous <= 0:
        return GROWTH_BASE_NOT_POSITIVE
    if current is None or current <= 0:
        return GROWTH_SIGN_CHANGE
    return None
