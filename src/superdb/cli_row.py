import argparse
import math

from superdb.catalog import open_table
from superdb.cli_common import resolve_db_dir as _resolve_db
from superdb.engine import StorageEngine
from superdb.heap_file import HeapFile
from superdb.rid import RID
from superdb.schema import ColumnType, TableMeta
from superdb.tuple_codec import describe_tuple


def add_row_parser(verbs) -> None:
    insert = verbs.add_parser("insert", help="insert a record")
    insert.add_argument("--db", metavar="PATH", default=argparse.SUPPRESS)
    insert.add_argument("--table", metavar="NAME", required=True)
    insert.add_argument("--values", metavar="CSV", required=True)

    get = verbs.add_parser("get", help="get a record by RID")
    get.add_argument("--db", metavar="PATH", default=argparse.SUPPRESS)
    get.add_argument("--table", metavar="NAME", required=True)
    get.add_argument("--rid", metavar="PAGE:SLOT", required=True)

    scan = verbs.add_parser("scan", help="scan all live records")
    scan.add_argument("--db", metavar="PATH", default=argparse.SUPPRESS)
    scan.add_argument("--table", metavar="NAME", required=True)

    update = verbs.add_parser("update", help="update a record by RID")
    update.add_argument("--db", metavar="PATH", default=argparse.SUPPRESS)
    update.add_argument("--table", metavar="NAME", required=True)
    update.add_argument("--rid", metavar="PAGE:SLOT", required=True)
    update.add_argument("--values", metavar="CSV", required=True)

    delete = verbs.add_parser("delete", help="delete a record by RID")
    delete.add_argument("--db", metavar="PATH", default=argparse.SUPPRESS)
    delete.add_argument("--table", metavar="NAME", required=True)
    delete.add_argument("--rid", metavar="PAGE:SLOT", required=True)

    hexdump = verbs.add_parser("hexdump", help="hex dump a record's raw bytes")
    hexdump.add_argument("--db", metavar="PATH", default=argparse.SUPPRESS)
    hexdump.add_argument("--table", metavar="NAME", required=True)
    hexdump.add_argument("--rid", metavar="PAGE:SLOT", required=True)


def _parse_rid(raw: str) -> RID:
    try:
        page_str, slot_str = raw.split(":")
        return RID(int(page_str), int(slot_str))
    except (ValueError, AttributeError):
        raise ValueError(
            f"invalid rid format '{raw}': expected page:slot (e.g. 0:3)"
        ) from None


def _parse_values_spec(spec: str, meta: TableMeta) -> list:
    fields = spec.split(",")
    n_cols = len(meta.columns)
    if len(fields) != n_cols:
        raise ValueError(
            f"invalid values for table '{meta.name}': expected {n_cols} fields, got {len(fields)}"
        )
    result = []
    for col, raw in zip(meta.columns, fields, strict=True):
        raw = raw.strip()
        if raw == "" or raw == r"\N":
            result.append(None)
        elif col.col_type == ColumnType.INT:
            try:
                result.append(int(raw))
            except ValueError:
                raise ValueError(
                    f"invalid value '{raw}' for column '{col.name}' (INT): expected an integer"
                ) from None
        else:
            result.append(raw)
    return result


def run_row(args, renderer) -> None:
    verb = getattr(args, "verb", None)
    if verb == "insert":
        db_dir = _resolve_db(args)
        engine = StorageEngine(db_dir)
        meta = engine.describe_table(args.table)
        values = _parse_values_spec(args.values, meta)
        record = {c.name: v for c, v in zip(meta.columns, values, strict=True)}
        rid = engine.insert(args.table, record)
        renderer.render_message(f"inserted rid={rid}")
    elif verb == "get":
        db_dir = _resolve_db(args)
        rid = _parse_rid(args.rid)
        engine = StorageEngine(db_dir)
        meta = engine.describe_table(args.table)
        record = engine.get(args.table, rid)
        renderer.render_rows(meta, [(str(rid), record)])
    elif verb == "scan":
        db_dir = _resolve_db(args)
        engine = StorageEngine(db_dir)
        meta = engine.describe_table(args.table)
        rows = engine.scan(args.table)
        if not rows:
            renderer.render_message("no rows")
        else:
            renderer.render_rows(
                meta,
                [(str(r.rid), r.values) for r in rows],
            )
    elif verb == "update":
        db_dir = _resolve_db(args)
        rid = _parse_rid(args.rid)
        engine = StorageEngine(db_dir)
        meta = engine.describe_table(args.table)
        values = _parse_values_spec(args.values, meta)
        record = {c.name: v for c, v in zip(meta.columns, values, strict=True)}
        new_rid = engine.update(args.table, rid, record)
        if new_rid == rid:
            renderer.render_message(f"updated rid={new_rid}")
        else:
            renderer.render_message(f"updated new_rid={new_rid}")
    elif verb == "delete":
        db_dir = _resolve_db(args)
        rid = _parse_rid(args.rid)
        engine = StorageEngine(db_dir)
        engine.delete(args.table, rid)
        renderer.render_message(f"deleted rid={rid}")
    elif verb == "hexdump":
        db_dir = _resolve_db(args)
        rid = _parse_rid(args.rid)
        handle = open_table(db_dir, args.table)
        raw = HeapFile(handle.heap_path, handle.meta.page_size).get(rid)
        spans = describe_tuple(raw, list(handle.meta.columns))
        bm_w = math.ceil(len(handle.meta.columns) / 8)
        null_bitmap_span = (0, bm_w) if bm_w > 0 else None
        field_spans = [
            (s.col_name, s.byte_offset, s.byte_length, s.col_type.value)
            for s in spans
        ]
        renderer.render_hexdump(
            str(rid),
            raw,
            field_spans,
            null_bitmap_span,
        )
    else:
        raise ValueError("usage: db-cli row <insert|get|scan|update|delete|hexdump>")
