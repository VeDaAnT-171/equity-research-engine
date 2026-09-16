from decimal import Decimal

from research_engine.quality.tolerance import presentation_unit, rounding_tolerance


def test_presentation_unit_inference():
    assert presentation_unit(Decimal("123456000000")) == Decimal("1000000")  # capped at millions
    assert presentation_unit(Decimal("1234567000")) == Decimal("1000")
    assert presentation_unit(Decimal("5.25")) == Decimal("0.01")
    assert presentation_unit(Decimal("0")) is None


def test_tolerance_scales_with_terms_and_finest_unit():
    assert rounding_tolerance([Decimal("3000000"), Decimal("1000"), Decimal("2999000")]) == Decimal("1500")
    assert rounding_tolerance([Decimal("0"), Decimal("0")]) == 0
