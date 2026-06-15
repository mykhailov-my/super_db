from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from . import logical_plan as L
from .engine import StorageEngine
from .errors import LogicalError
from .evaluate import evaluate, matches

# Physical plan + execution (HW Stage 5). Each physical operator is a node that
# knows how to produce rows. SELECT operators pull rows from their child as a
# generator (Volcano model — an iterator IS the pull-based contract in Python).
# A row flowing through the pipeline is a dict[column -> value]; DDL/DML
# operators (CreateTableExec/InsertExec) act once and return a status row.
#
# A query result is (columns, rows): the ordered output column names and the
# materialized row dicts. The CLI renders this via render_result, which is
# decoupled from any single table's schema (projections/joins/aggregates later
# produce columns that are not a table's columns).


@dataclass(slots=True, frozen=True)
class Result:
    columns: tuple[str, ...]
    rows: list[dict]


# --- physical operator nodes ---


@dataclass(slots=True, frozen=True)
class TableScanExec:
    table: str
    columns: tuple[str, ...]


@dataclass(slots=True, frozen=True)
class FilterExec:
    predicate: object
    child: object


@dataclass(slots=True, frozen=True)
class ProjectionExec:
    items: tuple[tuple[str, object], ...]  # (output_label, Expr)
    child: object


@dataclass(slots=True, frozen=True)
class JoinExec:
    left_key: str
    right_key: str
    left: object
    right: object


@dataclass(slots=True, frozen=True)
class AggregateExec:
    group_by: str | None
    aggregates: tuple[tuple[str, str | None, str], ...]  # (func, arg_col|None, label)
    child: object


@dataclass(slots=True, frozen=True)
class SortExec:
    column: str
    descending: bool
    child: object


@dataclass(slots=True, frozen=True)
class LimitExec:
    count: int
    child: object


@dataclass(slots=True, frozen=True)
class CreateTableExec:
    table: str
    columns: tuple[tuple[str, str], ...]


@dataclass(slots=True, frozen=True)
class InsertExec:
    table: str
    values: tuple[object, ...]


PhysicalNode = (
    TableScanExec | FilterExec | ProjectionExec | SortExec | LimitExec
    | CreateTableExec | InsertExec | JoinExec | AggregateExec
)


# --- lowering: logical plan -> physical plan ---

_LOWER = {
    L.Scan: lambda n: TableScanExec(n.table, n.columns),
    L.Filter: lambda n: FilterExec(n.predicate, lower(n.child)),
    L.Projection: lambda n: ProjectionExec(n.columns, lower(n.child)),
    L.Sort: lambda n: SortExec(n.column, n.descending, lower(n.child)),
    L.Limit: lambda n: LimitExec(n.count, lower(n.child)),
    L.CreateTable: lambda n: CreateTableExec(n.table, n.columns),
    L.Insert: lambda n: InsertExec(n.table, n.values),
    L.Join: lambda n: JoinExec(n.left_key, n.right_key, lower(n.left), lower(n.right)),
    L.Aggregate: lambda n: AggregateExec(n.group_by, n.aggregates, lower(n.child)),
}


def lower(node: L.LogicalNode) -> PhysicalNode:
    """Translate a logical plan into a physical plan (1:1 here — one physical
    operator per logical node)."""
    try:
        return _LOWER[type(node)](node)
    except KeyError:
        raise LogicalError(f"cannot lower logical node {type(node).__name__}") from None


# --- execution ---


def execute(node: PhysicalNode, engine: StorageEngine) -> Result:
    """Run a physical plan and return its Result. DDL/DML run once; SELECT
    pipelines materialize their output rows."""
    if isinstance(node, CreateTableExec):
        cols = [(name, ctype, True) for name, ctype in node.columns]
        try:
            engine.create_table(node.table, cols)
        except ValueError as e:  # e.g. table already exists → semantic error
            raise LogicalError(str(e)) from e
        return Result(("status",), [{"status": f"table {node.table!r} created"}])

    if isinstance(node, InsertExec):
        meta = engine.describe_table(node.table)
        record = {c.name: v for c, v in zip(meta.columns, node.values, strict=True)}
        rid = engine.insert(node.table, record)
        return Result(("status",), [{"status": f"inserted rid={rid}"}])

    columns = _output_columns(node)
    rows = list(_rows(node, engine))
    return Result(columns, rows)


def _output_columns(node: PhysicalNode) -> tuple[str, ...]:
    """The columns a SELECT pipeline emits."""
    if isinstance(node, ProjectionExec):
        return tuple(label for label, _ in node.items)
    if isinstance(node, AggregateExec):
        cols = []
        if node.group_by is not None:
            cols.append(node.group_by)
        cols.extend(label for _, _, label in node.aggregates)
        return tuple(cols)
    if isinstance(node, TableScanExec):
        return node.columns
    if isinstance(node, JoinExec):
        return _output_columns(node.left) + _output_columns(node.right)
    if isinstance(node, (FilterExec, SortExec, LimitExec)):
        return _output_columns(node.child)
    raise LogicalError(f"node {type(node).__name__} has no output columns")


def _rows(node: PhysicalNode, engine: StorageEngine) -> Iterator[dict]:
    if isinstance(node, TableScanExec):
        for row in engine.scan(node.table):
            yield dict(row.values)

    elif isinstance(node, FilterExec):
        for row in _rows(node.child, engine):
            if matches(node.predicate, row):
                yield row

    elif isinstance(node, ProjectionExec):
        for row in _rows(node.child, engine):
            yield {label: evaluate(expr, row) for label, expr in node.items}

    elif isinstance(node, LimitExec):
        if node.count > 0:
            for i, row in enumerate(_rows(node.child, engine)):
                yield row
                if i + 1 >= node.count:
                    break

    elif isinstance(node, SortExec):
        rows = list(_rows(node.child, engine))
        rows.sort(key=lambda r: _sort_key(r[node.column]), reverse=node.descending)
        yield from rows

    elif isinstance(node, JoinExec):
        yield from _join_rows(node, engine)

    elif isinstance(node, AggregateExec):
        yield from _aggregate_rows(node, engine)

    else:
        raise LogicalError(f"cannot execute node {type(node).__name__}")


def _join_rows(node: JoinExec, engine: StorageEngine) -> Iterator[dict]:
    # Nested-loop equi-join. Rows from each side are re-keyed by "table.col" so
    # qualified references resolve unambiguously after the join.
    left_table = _scan_table(node.left)
    right_table = _scan_table(node.right)
    left_rows = [_qualify(r, left_table) for r in _rows(node.left, engine)]
    right_rows = [_qualify(r, right_table) for r in _rows(node.right, engine)]
    for lr in left_rows:
        for rr in right_rows:
            if lr[node.left_key] == rr[node.right_key] and lr[node.left_key] is not None:
                yield {**lr, **rr}


def _aggregate_rows(node: AggregateExec, engine: StorageEngine) -> Iterator[dict]:
    groups: dict = {}
    order: list = []
    for row in _rows(node.child, engine):
        key = row[node.group_by] if node.group_by is not None else None
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(row)
    if node.group_by is None and not order:
        order = [None]  # COUNT(*) over an empty table still yields one row
        groups[None] = []
    for key in order:
        out = {}
        if node.group_by is not None:
            out[node.group_by] = key
        for func, arg_col, label in node.aggregates:
            out[label] = _apply_aggregate(func, arg_col, groups[key])
        yield out


def _apply_aggregate(func: str, arg_col: str | None, rows: list[dict]):
    if func == "COUNT":
        if arg_col is None:
            return len(rows)
        return sum(1 for r in rows if r[arg_col] is not None)
    values = [r[arg_col] for r in rows if r[arg_col] is not None]
    if not values:
        return None  # SUM/MIN/MAX/AVG over no non-null values is NULL
    if func in ("SUM", "AVG") and not all(isinstance(v, int) for v in values):
        raise LogicalError(f"{func} requires a numeric column, got a non-numeric value")
    if func == "SUM":
        return sum(values)
    if func == "MIN":
        return min(values)
    if func == "MAX":
        return max(values)
    if func == "AVG":
        return sum(values) / len(values)
    raise LogicalError(f"unknown aggregate {func}")


def _qualify(row: dict, table: str) -> dict:
    # Make both bare and "table.col" keys available so the join condition and
    # downstream projection can reference either form.
    out = dict(row)
    for col, val in row.items():
        out[f"{table}.{col}"] = val
    return out


def _scan_table(node: PhysicalNode) -> str:
    if isinstance(node, TableScanExec):
        return node.table
    raise LogicalError("join children must be table scans")


def _sort_key(value):
    # NULLs sort first (ascending) — wrap in (is_not_null, value) so None never
    # gets compared against a non-None value (which would TypeError).
    return (value is not None, value)
