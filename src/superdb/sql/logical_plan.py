from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from superdb.catalog.catalog import describe_table
from superdb.catalog.schema import ColumnType, TableMeta
from superdb.errors import LogicalError
from superdb.sql.sql_ast import BoolOp, ColumnRef, Comparison, Expr, FuncCall, Statement
from superdb.sql.sql_ast import CreateTable as CreateTableAST
from superdb.sql.sql_ast import Insert as InsertAST
from superdb.sql.sql_ast import Select as SelectAST

# AST → Logical Plan (HW Stage 4). Builds a tree of logical operators (not an
# AST copy) and binds it against the Catalog: table/column existence, INSERT
# arity, duplicate columns, supported types. SELECT lowers to
# Limit(Sort(Projection(Filter(Scan)))) — each wrapper present only when its
# clause exists. No execution here; the binder reads the Catalog only.

# --- logical nodes ---


@dataclass(slots=True, frozen=True)
class CreateTable:
    table: str
    columns: tuple[tuple[str, str], ...]  # (name, type)


@dataclass(slots=True, frozen=True)
class Insert:
    table: str
    values: tuple[object, ...]  # bound literal Python values (int / str / None)


@dataclass(slots=True, frozen=True)
class Scan:
    table: str
    columns: tuple[str, ...]  # full table column list, in schema order


@dataclass(slots=True, frozen=True)
class Filter:
    predicate: Expr  # the WHERE Expr from sql_ast, columns checked to exist
    child: object


@dataclass(slots=True, frozen=True)
class Projection:
    columns: tuple[tuple[str, object], ...]  # (output_label, Expr) pairs
    child: object


@dataclass(slots=True, frozen=True)
class Sort:
    column: str
    descending: bool
    child: object


@dataclass(slots=True, frozen=True)
class Limit:
    count: int
    child: object


@dataclass(slots=True, frozen=True)
class Join:
    # INNER equi-join: left_key = right_key. Children are Scan nodes; rows from
    # each side are namespaced by table so qualified refs (users.id) resolve.
    left_key: str   # qualified "table.col"
    right_key: str
    left: object
    right: object


@dataclass(slots=True, frozen=True)
class Aggregate:
    # group_by is a column name (or None for whole-table aggregation). aggregates
    # is the list of (func, arg_column_or_None, output_label) to compute. output
    # is the labels in SELECT-list order (the group column sits where it was
    # written, not forced first).
    group_by: str | None
    aggregates: tuple[tuple[str, str | None, str], ...]
    output: tuple[str, ...]
    child: object


LogicalNode = (
    CreateTable | Insert | Scan | Filter | Projection | Sort | Limit | Join | Aggregate
)


def build_plan(stmt: Statement, db_dir: Path) -> LogicalNode:
    """Bind and lower a parsed statement into a logical plan. Raises
    LogicalError on any semantic problem."""
    if isinstance(stmt, CreateTableAST):
        return _create_table(stmt)
    if isinstance(stmt, InsertAST):
        return _insert(stmt, db_dir)
    if isinstance(stmt, SelectAST):
        return _select(stmt, db_dir)
    raise LogicalError(f"cannot plan statement of type {type(stmt).__name__}")


# --- CREATE TABLE ---


def _create_table(stmt: CreateTableAST) -> CreateTable:
    if not stmt.columns:
        raise LogicalError(f"table {stmt.table!r} must have at least one column")
    # Match the catalog's case-insensitive dedup so the binder doesn't green-light
    # a CREATE the storage layer will then reject.
    lowered = [c.name.lower() for c in stmt.columns]
    dupes = {c.name for c in stmt.columns if lowered.count(c.name.lower()) > 1}
    if dupes:
        raise LogicalError(f"duplicate column name(s): {', '.join(sorted(dupes))}")
    for c in stmt.columns:
        if c.col_type not in ColumnType.__members__:
            raise LogicalError(f"unsupported column type {c.col_type!r} for {c.name!r}")
    return CreateTable(stmt.table, tuple((c.name, c.col_type) for c in stmt.columns))


# --- INSERT ---


def _insert(stmt: InsertAST, db_dir: Path) -> Insert:
    meta = _require_table(db_dir, stmt.table)
    if len(stmt.values) != len(meta.columns):
        raise LogicalError(
            f"table {stmt.table!r} has {len(meta.columns)} columns "
            f"but {len(stmt.values)} values were given"
        )
    return Insert(stmt.table, tuple(v.value for v in stmt.values))


# --- SELECT ---


def _select(stmt: SelectAST, db_dir: Path) -> LogicalNode:
    # Resolve the column namespace: single table → bare names; JOIN → qualified
    # "table.col" names plus bare names that are unambiguous across both tables.
    scope = _build_scope(stmt, db_dir)
    node = scope.source

    if stmt.where is not None:
        _check_scope_expr(stmt.where, scope)
        node = Filter(stmt.where, node)

    # Aggregate query? (any aggregate func in projections, or a GROUP BY)
    if stmt.group_by is not None or _has_aggregate(stmt.projections):
        node = _aggregate(stmt, scope, node)
        # Output columns are the SELECT-list labels in written order; ORDER BY /
        # LIMIT apply on top of the grouped result.
        return _order_and_limit(stmt, node, node.output)

    # SQL eval order: WHERE → ORDER BY → projection → LIMIT. Sort below Projection
    # so ORDER BY can name a column the projection drops.
    if stmt.order_by is not None:
        scope.require(stmt.order_by.column)
        node = Sort(stmt.order_by.column, stmt.order_by.descending, node)

    node = Projection(_projection_items(stmt, scope), node)

    if stmt.limit is not None:
        node = Limit(stmt.limit, node)
    return node


def _order_and_limit(
    stmt: SelectAST, node: LogicalNode, available: tuple[str, ...]
) -> LogicalNode:
    """Wrap a grouped result in Sort/Limit. ORDER BY may only reference a column
    present in the aggregate output (the group column or an aggregate label)."""
    if stmt.order_by is not None:
        if stmt.order_by.column not in available:
            raise LogicalError(
                f"ORDER BY column {stmt.order_by.column!r} is not in the result"
            )
        node = Sort(stmt.order_by.column, stmt.order_by.descending, node)
    if stmt.limit is not None:
        node = Limit(stmt.limit, node)
    return node


def _aggregate(stmt: SelectAST, scope: _Scope, child: LogicalNode) -> Aggregate:
    if stmt.group_by is not None:
        scope.require(stmt.group_by)
    aggs: list[tuple[str, str | None, str]] = []
    output: list[str] = []  # labels in SELECT-list order
    for label, expr in _projection_items(stmt, scope):
        if isinstance(expr, FuncCall) and expr.name in _AGG_FUNCS:
            arg = None if expr.arg is None else scope.resolve(expr.arg)
            aggs.append((expr.name, arg, label))
            output.append(label)
        elif isinstance(expr, ColumnRef):
            # A non-aggregate column is only valid if it's the GROUP BY column.
            if stmt.group_by is None or scope.resolve(expr) != scope.resolve_name(stmt.group_by):
                raise LogicalError(
                    f"column {label!r} must appear in GROUP BY or an aggregate"
                )
            output.append(label)
        else:
            raise LogicalError(f"unsupported aggregate projection {label!r}")
    if not aggs:
        raise LogicalError("aggregate query needs at least one aggregate function")
    return Aggregate(stmt.group_by, tuple(aggs), tuple(output), child)


# --- column scope: single-table or two-table JOIN namespace ---

_AGG_FUNCS = frozenset({"COUNT", "SUM", "MIN", "MAX", "AVG"})
_SCALAR_FUNCS = frozenset({"LENGTH"})


@dataclass(slots=True)
class _Scope:
    source: LogicalNode               # Scan or Join feeding this query
    tables: tuple[str, ...]           # one or two table names
    by_table: dict[str, tuple[str, ...]]  # table -> its column names

    def resolve(self, ref: ColumnRef) -> str:
        """Map a ColumnRef to its canonical key in a row: bare column for a
        single table, "table.col" under a JOIN. Raises on unknown/ambiguous."""
        if ref.table is not None:
            if ref.table not in self.by_table:
                raise LogicalError(f"unknown table {ref.table!r} in {ref.table}.{ref.name}")
            if ref.name not in self.by_table[ref.table]:
                raise LogicalError(f"column {ref.name!r} does not exist in {ref.table!r}")
            return f"{ref.table}.{ref.name}" if len(self.tables) > 1 else ref.name
        return self.resolve_name(ref.name)

    def resolve_name(self, name: str) -> str:
        owners = [t for t in self.tables if name in self.by_table[t]]
        if not owners:
            raise LogicalError(f"column {name!r} does not exist")
        if len(owners) > 1:
            raise LogicalError(f"column {name!r} is ambiguous (in {', '.join(owners)})")
        return f"{owners[0]}.{name}" if len(self.tables) > 1 else name

    def require(self, name: str) -> None:
        self.resolve_name(name)

    def all_columns(self) -> tuple[tuple[str, object], ...]:
        """(label, ColumnRef) for SELECT * — qualified labels under a JOIN."""
        out = []
        for t in self.tables:
            for c in self.by_table[t]:
                label = f"{t}.{c}" if len(self.tables) > 1 else c
                out.append((label, ColumnRef(c, table=t if len(self.tables) > 1 else None)))
        return tuple(out)


def _build_scope(stmt: SelectAST, db_dir: Path) -> _Scope:
    left_meta = _require_table(db_dir, stmt.table)
    left_cols = tuple(c.name for c in left_meta.columns)
    if stmt.join is None:
        scan = Scan(stmt.table, left_cols)
        return _Scope(scan, (stmt.table,), {stmt.table: left_cols})

    right_meta = _require_table(db_dir, stmt.join.table)
    right_cols = tuple(c.name for c in right_meta.columns)
    by_table = {stmt.table: left_cols, stmt.join.table: right_cols}
    scope = _Scope(None, (stmt.table, stmt.join.table), by_table)

    # Orient the ON keys by which table they belong to, not by their order in
    # the ON clause: `ON a.x = b.y` and `ON b.y = a.x` are the same join.
    k1 = scope.resolve(stmt.join.left)
    k2 = scope.resolve(stmt.join.right)
    keyed = {k.split(".", 1)[0]: k for k in (k1, k2)}
    if set(keyed) != {stmt.table, stmt.join.table}:
        raise LogicalError("JOIN ON must compare one column from each table")
    scope.source = Join(
        keyed[stmt.table], keyed[stmt.join.table],
        Scan(stmt.table, left_cols), Scan(stmt.join.table, right_cols),
    )
    return scope


def _projection_items(stmt: SelectAST, scope: _Scope) -> tuple[tuple[str, object], ...]:
    """(output_label, Expr) for each SELECT item, with columns resolved."""
    if stmt.projections is None:
        return scope.all_columns()
    items = []
    for expr in stmt.projections:
        if isinstance(expr, ColumnRef):
            scope.resolve(expr)  # validate
            label = expr.name if expr.table is None else f"{expr.table}.{expr.name}"
            items.append((label, expr))
        elif isinstance(expr, FuncCall):
            _check_func(expr, scope)
            items.append((_func_label(expr), expr))
        else:
            raise LogicalError(f"unsupported projection: {expr!r}")
    # Rows are dicts keyed by label; duplicate labels would silently collapse and
    # drop a column's value. Reject rather than mislead.
    labels = [label for label, _ in items]
    dup = next((c for c in labels if labels.count(c) > 1), None)
    if dup is not None:
        raise LogicalError(f"duplicate output column {dup!r}; use distinct names")
    return tuple(items)


def _check_func(fn: FuncCall, scope: _Scope) -> None:
    if fn.name in _AGG_FUNCS:
        if fn.name == "COUNT" and fn.arg is None:
            return  # COUNT(*)
    elif fn.name in _SCALAR_FUNCS:
        pass  # fall through to the shared arg check
    else:
        raise LogicalError(f"unknown function {fn.name}")
    if fn.arg is None:
        raise LogicalError(f"{fn.name} requires a column argument")
    scope.resolve(fn.arg)


def _func_label(fn) -> str:
    if fn.arg is None:
        return f"{fn.name}(*)"
    inner = fn.arg.name if fn.arg.table is None else f"{fn.arg.table}.{fn.arg.name}"
    return f"{fn.name}({inner})"


def _has_aggregate(projections) -> bool:
    if projections is None:
        return False
    return any(isinstance(e, FuncCall) and e.name in _AGG_FUNCS for e in projections)


def _check_scope_expr(expr, scope: _Scope) -> None:
    """Validate every column reference in a WHERE/ON expression against the scope."""
    stack = [expr]
    while stack:
        node = stack.pop()
        if isinstance(node, ColumnRef):
            scope.resolve(node)
        elif isinstance(node, (Comparison, BoolOp)):
            stack.append(node.left)
            stack.append(node.right)
        elif isinstance(node, FuncCall):
            _check_func(node, scope)


# --- binder helpers (Catalog access only) ---


def _require_table(db_dir: Path, name: str) -> TableMeta:
    try:
        return describe_table(db_dir, name)
    except ValueError as e:
        raise LogicalError(str(e)) from e


