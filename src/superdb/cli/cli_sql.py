import argparse

from superdb.cli.cli_common import resolve_db_dir as _resolve_db
from superdb.sql import executor as ex
from superdb.sql import logical_plan as lp
from superdb.sql.sql_ast import (
    BoolOp,
    ColumnRef,
    Comparison,
    CreateTable,
    FuncCall,
    Insert,
    Literal,
    Select,
)
from superdb.sql.sql_parser import parse
from superdb.storage.engine import StorageEngine


def add_sql_parser(verbs) -> None:
    p = verbs.add_parser("parse", help="parse a SQL query and print its AST")
    p.add_argument("--query", metavar="SQL", required=True)

    pl = verbs.add_parser("plan", help="build and print a logical plan")
    pl.add_argument("--db", metavar="PATH", default=argparse.SUPPRESS)
    pl.add_argument("--query", metavar="SQL", required=True)

    q = verbs.add_parser("query", help="run a SQL query and print the result")
    q.add_argument("--db", metavar="PATH", default=argparse.SUPPRESS)
    q.add_argument("--query", metavar="SQL", required=True)

    ex_p = verbs.add_parser("explain", help="print the logical and physical plans")
    ex_p.add_argument("--db", metavar="PATH", default=argparse.SUPPRESS)
    ex_p.add_argument("--query", metavar="SQL", required=True)


def run_sql(args, renderer) -> None:
    verb = getattr(args, "verb", None)
    if verb == "parse":
        renderer.render_message(_render(parse(args.query)))
    elif verb == "plan":
        plan = lp.build_plan(parse(args.query), _resolve_db(args))
        renderer.render_message(_render_plan(plan))
    elif verb == "query":
        plan = lp.build_plan(parse(args.query), _resolve_db(args))
        result = ex.execute(ex.lower(plan), StorageEngine(_resolve_db(args)))
        renderer.render_result(result.columns, result.rows)
    elif verb == "explain":
        plan = lp.build_plan(parse(args.query), _resolve_db(args))
        physical = ex.lower(plan)
        renderer.render_message(
            "Logical Plan:\n" + _render_plan(plan)
            + "\n\nPhysical Plan:\n" + _render_physical(physical)
        )
    else:
        raise ValueError('usage: db-cli sql parse|plan|query|explain --query "..."')


def _render(node) -> str:
    return "\n".join(_lines(node, 0))


def _lines(node, depth: int) -> list[str]:
    pad = "  " * depth

    if isinstance(node, CreateTable):
        out = [f"{pad}CreateTable {node.table}"]
        for c in node.columns:
            out.append(f"{pad}  {c.name} {c.col_type}")
        return out

    if isinstance(node, Insert):
        vals = ", ".join(_literal(v) for v in node.values)
        return [f"{pad}Insert {node.table}", f"{pad}  values [{vals}]"]

    if isinstance(node, Select):
        cols = "*" if node.projections is None else ", ".join(c.name for c in node.projections)
        out = [f"{pad}Select [{cols}] from {node.table}"]
        if node.where is not None:
            out.append(f"{pad}  where {_expr(node.where)}")
        if node.order_by is not None:
            direction = "desc" if node.order_by.descending else "asc"
            out.append(f"{pad}  order by {node.order_by.column} {direction}")
        if node.limit is not None:
            out.append(f"{pad}  limit {node.limit}")
        return out

    return [f"{pad}{node!r}"]


def _expr(e) -> str:
    if isinstance(e, ColumnRef):
        return e.name if e.table is None else f"{e.table}.{e.name}"
    if isinstance(e, Literal):
        return _literal(e)
    if isinstance(e, Comparison):
        return f"({_expr(e.left)} {e.op} {_expr(e.right)})"
    if isinstance(e, BoolOp):
        return f"({_expr(e.left)} {e.op} {_expr(e.right)})"
    if isinstance(e, FuncCall):
        inner = "*" if e.arg is None else _expr(e.arg)
        return f"{e.name}({inner})"
    return repr(e)


def _literal(lit: Literal) -> str:
    if lit.kind == "STRING":
        return f"'{lit.value}'"
    if lit.kind == "NULL":
        return "NULL"
    return str(lit.value)


def _render_plan(node) -> str:
    return "\n".join(_plan_lines(node, 0))


def _plan_lines(node, depth: int) -> list[str]:
    pad = "  " * depth

    if isinstance(node, lp.CreateTable):
        cols = ", ".join(f"{n} {t}" for n, t in node.columns)
        return [f"{pad}CreateTable {node.table} [{cols}]"]
    if isinstance(node, lp.Insert):
        vals = ", ".join(_value(v) for v in node.values)
        return [f"{pad}Insert {node.table} [{vals}]"]

    # SELECT nodes: header line, then the child subtree indented below.
    if isinstance(node, lp.Limit):
        head = f"{pad}Limit {node.count}"
        child = node.child
    elif isinstance(node, lp.Sort):
        direction = "desc" if node.descending else "asc"
        head = f"{pad}Sort [{node.column} {direction}]"
        child = node.child
    elif isinstance(node, lp.Projection):
        head = f"{pad}Projection [{', '.join(label for label, _ in node.columns)}]"
        child = node.child
    elif isinstance(node, lp.Filter):
        head = f"{pad}Filter [{_expr(node.predicate)}]"
        child = node.child
    elif isinstance(node, lp.Aggregate):
        gb = f" group_by={node.group_by}" if node.group_by else ""
        aggs = ", ".join(label for _, _, label in node.aggregates)
        head = f"{pad}Aggregate [{aggs}]{gb}"
        child = node.child
    elif isinstance(node, lp.Join):
        out = [f"{pad}Join [{node.left_key} = {node.right_key}]"]
        out += _plan_lines(node.left, depth + 1)
        out += _plan_lines(node.right, depth + 1)
        return out
    elif isinstance(node, lp.Scan):
        return [f"{pad}Scan {node.table}"]
    else:
        return [f"{pad}{node!r}"]

    return [head, *_plan_lines(child, depth + 1)]


def _value(v) -> str:
    if isinstance(v, str):
        return f"'{v}'"
    if v is None:
        return "NULL"
    return str(v)


def _render_physical(node) -> str:
    return "\n".join(_physical_lines(node, 0))


def _physical_lines(node, depth: int) -> list[str]:
    pad = "  " * depth

    if isinstance(node, ex.CreateTableExec):
        cols = ", ".join(f"{n} {t}" for n, t in node.columns)
        return [f"{pad}CreateTableExec {node.table} [{cols}]"]
    if isinstance(node, ex.InsertExec):
        vals = ", ".join(_value(v) for v in node.values)
        return [f"{pad}InsertExec {node.table} [{vals}]"]

    if isinstance(node, ex.LimitExec):
        head, child = f"{pad}LimitExec {node.count}", node.child
    elif isinstance(node, ex.SortExec):
        direction = "desc" if node.descending else "asc"
        head, child = f"{pad}SortExec [{node.column} {direction}]", node.child
    elif isinstance(node, ex.ProjectionExec):
        labels = ", ".join(label for label, _ in node.items)
        head, child = f"{pad}ProjectionExec [{labels}]", node.child
    elif isinstance(node, ex.FilterExec):
        head, child = f"{pad}FilterExec [{_expr(node.predicate)}]", node.child
    elif isinstance(node, ex.AggregateExec):
        gb = f" group_by={node.group_by}" if node.group_by else ""
        aggs = ", ".join(label for _, _, label in node.aggregates)
        head, child = f"{pad}AggregateExec [{aggs}]{gb}", node.child
    elif isinstance(node, ex.JoinExec):
        out = [f"{pad}NestedLoopJoinExec [{node.left_key} = {node.right_key}]"]
        out += _physical_lines(node.left, depth + 1)
        out += _physical_lines(node.right, depth + 1)
        return out
    elif isinstance(node, ex.TableScanExec):
        return [f"{pad}TableScanExec {node.table}"]
    else:
        return [f"{pad}{node!r}"]

    return [head, *_physical_lines(child, depth + 1)]
