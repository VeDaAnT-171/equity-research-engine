from decimal import Decimal

import pytest

from research_engine.expressions import ExpressionError, evaluate, referenced_names


def test_names_and_evaluation():
    assert referenced_names("(a + b) * c / 2") == {"a", "b", "c"}
    assert evaluate("(a + b) * c / 2", {"a": Decimal("1"), "b": 2, "c": "3.5"}) == Decimal("5.25")
    assert evaluate("-a + 1", {"a": 4}) == Decimal("-3")


@pytest.mark.parametrize("expr", [
    "__import__('os')", "a.b", "a[0]", "f(a)", "a ** 2", "lambda: 1", "'text'", "True", "a if b else c", "",
])
def test_rejects_unsafe_or_invalid(expr):
    with pytest.raises(ExpressionError):
        referenced_names(expr)


def test_missing_inputs_and_division_by_zero():
    with pytest.raises(ExpressionError, match="missing inputs"):
        evaluate("a / b", {"a": 1})
    with pytest.raises(ExpressionError, match="division by zero"):
        evaluate("a / b", {"a": 1, "b": 0})


def test_decimal_precision_preserved():
    assert evaluate("a + b", {"a": "0.1", "b": "0.2"}) == Decimal("0.3")
