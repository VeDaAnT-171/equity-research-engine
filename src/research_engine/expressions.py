"""Safe arithmetic expressions for framework derivations and driver formulas.

Formulas live in YAML (data), so they are untrusted. They are parsed with a whitelisted
AST and evaluated with Decimal arithmetic. `eval` is never used.
"""

from __future__ import annotations

import ast
from decimal import Decimal
from typing import Mapping

_ALLOWED_NODES = (
    ast.Expression, ast.BinOp, ast.UnaryOp, ast.Name, ast.Load, ast.Constant,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.UAdd, ast.USub,
)
_MAX_LEN = 500


class ExpressionError(ValueError):
    """Invalid, unsafe, or unevaluable expression."""


def _parse(expr: str) -> ast.Expression:
    if not isinstance(expr, str) or not expr.strip():
        raise ExpressionError("expression must be a non-empty string")
    if len(expr) > _MAX_LEN:
        raise ExpressionError(f"expression longer than {_MAX_LEN} characters")
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise ExpressionError(f"invalid expression {expr!r}: {exc.msg}") from None
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            raise ExpressionError(
                f"disallowed syntax '{type(node).__name__}' in {expr!r}; only + - * / "
                "and parentheses over identifiers and numbers are permitted"
            )
        if isinstance(node, ast.Constant) and (
            isinstance(node.value, bool) or not isinstance(node.value, (int, float))
        ):
            raise ExpressionError(f"only numeric literals are permitted in {expr!r}")
    return tree


def is_additive(expr: str) -> bool:
    """True if the expression only adds/subtracts identifiers (the form accounting identities take)."""
    for node in ast.walk(_parse(expr)):
        if isinstance(node, ast.BinOp) and not isinstance(node.op, (ast.Add, ast.Sub)):
            return False
        if isinstance(node, ast.Constant):
            return False
    return True


def referenced_names(expr: str) -> frozenset[str]:
    return frozenset(n.id for n in ast.walk(_parse(expr)) if isinstance(n, ast.Name))


def evaluate(expr: str, env: Mapping[str, Decimal | int | float | str]) -> Decimal:
    tree = _parse(expr)
    missing = referenced_names(expr) - set(env)
    if missing:
        raise ExpressionError(f"missing inputs for {expr!r}: {', '.join(sorted(missing))}")

    def ev(node: ast.AST) -> Decimal:
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant):
            return Decimal(str(node.value))
        if isinstance(node, ast.Name):
            value = env[node.id]
            return value if isinstance(value, Decimal) else Decimal(str(value))
        if isinstance(node, ast.UnaryOp):
            operand = ev(node.operand)
            return -operand if isinstance(node.op, ast.USub) else operand
        if isinstance(node, ast.BinOp):
            left, right = ev(node.left), ev(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if right == 0:
                raise ExpressionError(f"division by zero in {expr!r}")
            return left / right
        raise ExpressionError(f"unsupported node {type(node).__name__}")  # unreachable after _parse

    return ev(tree)
