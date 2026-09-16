"""Rounding tolerance for additive comparisons.

Filers round each tagged value to a presentation unit (thousands, millions). A sum of n independently rounded
terms can differ from the rounded total by up to n x unit / 2. The unit is not in companyfacts JSON, so it is
inferred as the largest power of ten dividing every term, capped at one million: SEC financial statements are
presented in units no coarser than millions, and the cap stops coincidentally round values (common in
synthetic or rounded disclosures) from inflating the tolerance.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Iterable, Optional

MAX_PRESENTATION_UNIT = Decimal(10) ** 6


def presentation_unit(value: Decimal) -> Optional[Decimal]:
    if value == 0:
        return None
    exponent = value.normalize().as_tuple().exponent
    return min(Decimal(10) ** exponent, MAX_PRESENTATION_UNIT)


def rounding_tolerance(values: Iterable[Decimal]) -> Decimal:
    values = list(values)
    units = [u for u in (presentation_unit(v) for v in values) if u is not None]
    if not units:
        return Decimal(0)
    return Decimal(len(values)) * min(units) / 2
