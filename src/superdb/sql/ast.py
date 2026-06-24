from __future__ import annotations

from dataclasses import dataclass

# Internal SQL AST (superdb.sql.ast — a submodule, so it never shadows the stdlib
# `ast`). Statements: CreateTable, Insert, Select. Expressions: ColumnRef, Literal,
# Comparison, BoolOp. Literals carry a kind ("INT" | "STRING" | "NULL") so the
# planner can type them later without re-parsing.

# --- expressions ---


@dataclass(slots=True, frozen=True)
class ColumnRef:
    name: str
    table: str | None = None  # qualified ref (users.id) — unused until JOIN milestone


@dataclass(slots=True, frozen=True)
class Literal:
    kind: str  # "INT" | "STRING" | "NULL"
    value: int | str | None


@dataclass(slots=True, frozen=True)
class Comparison:
    op: str  # = != < <= > >=
    left: Expr
    right: Expr


@dataclass(slots=True, frozen=True)
class BoolOp:
    op: str  # AND | OR
    left: Expr
    right: Expr


@dataclass(slots=True, frozen=True)
class FuncCall:
    name: str  # upper-cased: COUNT, SUM, MIN, MAX, AVG, LENGTH
    arg: Expr | None  # None means '*' (only valid for COUNT(*))


Expr = ColumnRef | Literal | Comparison | BoolOp | FuncCall

# --- statements ---


@dataclass(slots=True, frozen=True)
class ColumnDef:
    name: str
    col_type: str  # "INT" | "TEXT"


@dataclass(slots=True, frozen=True)
class CreateTable:
    table: str
    columns: tuple[ColumnDef, ...]


@dataclass(slots=True, frozen=True)
class Insert:
    table: str
    values: tuple[Literal, ...]


@dataclass(slots=True, frozen=True)
class OrderBy:
    column: str
    descending: bool


@dataclass(slots=True, frozen=True)
class Join:
    table: str               # the right-hand table
    left: ColumnRef          # ON left = right  (equi-join only)
    right: ColumnRef


@dataclass(slots=True, frozen=True)
class Select:
    # projections: None = SELECT *. Otherwise each item is an Expr — a ColumnRef
    # (possibly qualified) or a FuncCall (aggregate / scalar).
    projections: tuple[Expr, ...] | None
    table: str
    join: Join | None
    where: Expr | None
    group_by: str | None     # single bare column name (qualified not required)
    order_by: OrderBy | None
    limit: int | None


Statement = CreateTable | Insert | Select
