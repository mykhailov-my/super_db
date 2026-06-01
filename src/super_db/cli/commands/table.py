import argparse
from pathlib import Path

from super_db.catalog.catalog import (
    create_table,
    describe_table,
    drop_table,
    list_tables,
)
from super_db.common.constants import DEFAULT_PAGE_SIZE


def add_table_parser(verbs) -> None:
    create = verbs.add_parser("create", help="create a new table")
    create.add_argument("--db", metavar="PATH", default=argparse.SUPPRESS)
    create.add_argument("--table", metavar="NAME", required=True, help="table name")
    create.add_argument("--columns", metavar="SPEC", required=True, help="column spec: name:TYPE,...")
    create.add_argument("--page-size", metavar="N", type=int, default=DEFAULT_PAGE_SIZE)

    ls = verbs.add_parser("list", help="list all tables")
    ls.add_argument("--db", metavar="PATH", default=argparse.SUPPRESS)

    desc = verbs.add_parser("describe", help="describe a table's schema")
    desc.add_argument("--db", metavar="PATH", default=argparse.SUPPRESS)
    desc.add_argument("--table", metavar="NAME", required=True, help="table name")

    drop = verbs.add_parser("drop", help="drop a table")
    drop.add_argument("--db", metavar="PATH", default=argparse.SUPPRESS)
    drop.add_argument("--table", metavar="NAME", required=True, help="table name")


def _resolve_db(args) -> Path:
    db = getattr(args, "db", None)
    if db is None:
        raise ValueError("missing --db PATH (the database directory)")
    return Path(db).resolve()


def _parse_columns_spec(spec: str) -> list[tuple[str, str, bool]]:
    result = []
    for item in spec.split(","):
        item = item.strip()
        if ":" not in item:
            raise ValueError(f"invalid column spec {item!r}: expected name:TYPE")
        name, _, col_type = item.partition(":")
        result.append((name.strip(), col_type.strip().upper(), True))
    return result


def run_table(args, renderer) -> None:
    verb = getattr(args, "verb", None)
    if verb == "create":
        db_dir = _resolve_db(args)
        cols = _parse_columns_spec(args.columns)
        meta = create_table(db_dir, args.table, cols, page_size=args.page_size)
        renderer.render_message(
            f"Created table {meta.name!r} (id={meta.table_id}) with {len(meta.columns)} columns"
        )
    elif verb == "list":
        metas = list_tables(_resolve_db(args))
        if not metas:
            renderer.render_message("no tables")
        else:
            renderer.render_table_list([(m.name, str(m.table_id)) for m in metas])
    elif verb == "describe":
        meta = describe_table(_resolve_db(args), args.table)
        renderer.render_schema(meta)
    elif verb == "drop":
        drop_table(_resolve_db(args), args.table)
        renderer.render_message(f"Dropped table {args.table!r}")
    else:
        raise ValueError("usage: db-cli table <create|list|describe|drop>")
