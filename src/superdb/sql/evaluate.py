from __future__ import annotations

from superdb.errors import LogicalError
from superdb.sql.sql_ast import BoolOp, ColumnRef, Comparison, FuncCall, Literal

# Evaluate a WHERE expression (sql_ast.Expr) against one row. A row is a plain
# dict[column -> value]; the key is the column name, and a later milestone may
# use qualified keys like "users.id" — this evaluator looks the key up verbatim,
# so it already tolerates that without change.
#
# NULL follows SQL three-valued logic: a comparison involving NULL is unknown
# (treated as not-true), AND/OR propagate via Python truthiness of the bool
# result. We collapse unknown to False at the predicate boundary, which is
# correct for WHERE (rows where the predicate is not TRUE are filtered out).

_COMPARATORS = {
    "=": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
}


def evaluate(expr, row: dict) -> object:
    """Return the value of an expression for a row. For a top-level WHERE
    predicate the result is a bool; sub-expressions may return scalar values."""
    if isinstance(expr, Literal):
        return expr.value
    if isinstance(expr, ColumnRef):
        key = expr.name if expr.table is None else f"{expr.table}.{expr.name}"
        if key not in row:
            raise LogicalError(f"column {key!r} not available at evaluation")
        return row[key]
    if isinstance(expr, FuncCall):
        return _scalar(expr, row)
    if isinstance(expr, Comparison):
        return _compare(expr.op, evaluate(expr.left, row), evaluate(expr.right, row))
    if isinstance(expr, BoolOp):
        left = bool(evaluate(expr.left, row))
        if expr.op == "AND":
            return left and bool(evaluate(expr.right, row))
        return left or bool(evaluate(expr.right, row))
    raise LogicalError(f"cannot evaluate expression of type {type(expr).__name__}")


def matches(predicate, row: dict) -> bool:
    """True iff the WHERE predicate evaluates to exactly TRUE for this row.
    SQL unknown (from a NULL comparison) is not TRUE, so the row is excluded."""
    return evaluate(predicate, row) is True


def _scalar(fn: FuncCall, row: dict):
    # Scalar functions evaluated per-row in a projection. LENGTH(NULL) is NULL.
    if fn.name == "LENGTH":
        val = evaluate(fn.arg, row)
        return None if val is None else len(str(val))
    raise LogicalError(f"{fn.name} is not a scalar function")


def _compare(op: str, a, b):
    # A comparison involving NULL is SQL-unknown (None), not False — so a nested
    # comparison sees the unknown rather than a fake False. matches() collapses
    # unknown to "no match" at the predicate boundary.
    if a is None or b is None:
        return None
    try:
        return _COMPARATORS[op](a, b)
    except TypeError as e:
        # e.g. comparing INT to TEXT with an ordering operator.
        raise LogicalError(f"cannot compare {a!r} {op} {b!r}: {e}") from e
