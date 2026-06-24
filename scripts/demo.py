"""9-step end-to-end showcase for super_db.

Walks the north-star scenario: create db → create table → insert → get → scan
→ build index + hexdump → update → delete → restart → verify.

Usage:
    python scripts/demo.py --db PATH
"""
import argparse
import math
import os
import sys
from pathlib import Path

from superdb.catalog.catalog import open_table
from superdb.cli.index import _dump_tree
from superdb.errors import RecordNotFoundError, SuperDBError
from superdb.catalog.database import init_db
from superdb.index.node_layout import decode_header
from superdb.render.rich import RichRenderer
from superdb.storage.engine import StorageEngine
from superdb.storage.heap_file import HeapFile
from superdb.storage.page import Page
from superdb.storage.page_layout import HEADER_SIZE, SLOT_FLAG_LIVE
from superdb.storage.tuple_codec import describe_tuple


def _section(n: int, title: str) -> None:
    print(f"\n=== Step {n}: {title} ===")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="super_db 9-step end-to-end showcase"
    )
    parser.add_argument("--db", required=True, metavar="PATH", help="database directory")
    args = parser.parse_args()
    db_dir = Path(args.db)
    renderer = RichRenderer()

    # ------------------------------------------------------------------
    # Step 1: init db
    # ------------------------------------------------------------------
    _section(1, "init db")
    init_db(db_dir)
    renderer.render_message(f"Initialized database at {db_dir}")

    # ------------------------------------------------------------------
    # Step 2: create table
    # ------------------------------------------------------------------
    _section(2, "create table")
    engine = StorageEngine(db_dir)
    engine.create_table("users", [("id", "INT", False), ("name", "TEXT", True)])
    meta = engine.describe_table("users")
    renderer.render_schema(meta)

    # ------------------------------------------------------------------
    # Step 3: insert rows + show page byte layout
    # ------------------------------------------------------------------
    _section(3, "insert")
    rid1 = engine.insert("users", {"id": 1, "name": "Alice"})
    rid2 = engine.insert("users", {"id": 2, "name": "Bob"})
    renderer.render_message(f"Inserted rid1={rid1.page_id}:{rid1.slot_id}")
    renderer.render_message(f"Inserted rid2={rid2.page_id}:{rid2.slot_id}")

    # Show page 0 byte layout
    handle = open_table(db_dir, "users")
    ps = handle.meta.page_size
    fd = os.open(str(handle.heap_path), os.O_RDONLY)
    try:
        raw = os.pread(fd, ps, 0)
    finally:
        os.close(fd)
    page = Page.from_bytes(raw, ps)
    slots = []
    for sid in range(page.slot_count):
        off, ln, fl = page._slot(sid)
        slots.append((sid, off, ln, bool(fl & SLOT_FLAG_LIVE)))
    renderer.render_page(
        table_name="users",
        page_id=0,
        page_size=ps,
        header_bytes=HEADER_SIZE,
        slot_count=page.slot_count,
        slots=slots,
        free_space_start=page.free_start,
        free_space_end=page.free_end,
    )

    # ------------------------------------------------------------------
    # Step 4: get rid1
    # ------------------------------------------------------------------
    _section(4, "get")
    record = engine.get("users", rid1)
    renderer.render_rows(meta, [(f"{rid1.page_id}:{rid1.slot_id}", record)])

    # ------------------------------------------------------------------
    # Step 5: scan all rows
    # ------------------------------------------------------------------
    _section(5, "scan")
    rows = engine.scan("users")
    renderer.render_rows(
        meta,
        [(f"{r.rid.page_id}:{r.rid.slot_id}", r.values) for r in rows],
    )

    # ------------------------------------------------------------------
    # Step 6: build index + hexdump of rid1
    # ------------------------------------------------------------------
    _section(6, "build index + hexdump")
    engine.build_index("users", "id")

    # Render B+Tree using _dump_tree from index.py (reuse, no duplicate walker)
    handle = open_table(db_dir, "users")
    idx_path = db_dir / "users.idx"
    idx_fd = os.open(str(idx_path), os.O_RDONLY)
    try:
        hdr_raw = os.pread(idx_fd, handle.meta.page_size, 0)
        hdr = decode_header(hdr_raw)
        nodes = _dump_tree(
            idx_fd, hdr.root_page_id, hdr.key_type, hdr.text_key_cap, handle.meta.page_size
        )
    finally:
        os.close(idx_fd)
    renderer.render_btree("users", "id", nodes)

    # Hexdump of rid1 raw bytes
    raw_tuple = HeapFile(handle.heap_path, handle.meta.page_size).get(rid1)
    spans = describe_tuple(raw_tuple, list(handle.meta.columns))
    bm_w = math.ceil(len(handle.meta.columns) / 8)
    null_bitmap_span = (0, bm_w) if bm_w > 0 else None
    field_spans = [
        (s.col_name, s.byte_offset, s.byte_length, s.col_type.value)
        for s in spans
    ]
    renderer.render_hexdump(
        f"{rid1.page_id}:{rid1.slot_id}",
        raw_tuple,
        field_spans,
        null_bitmap_span,
    )

    # ------------------------------------------------------------------
    # Step 7: update rid2 Bob -> Bobby
    # ------------------------------------------------------------------
    _section(7, "update")
    new_rid = engine.update("users", rid2, {"id": 2, "name": "Bobby"})
    renderer.render_message(f"Updated rid2 -> new_rid={new_rid.page_id}:{new_rid.slot_id}")

    # ------------------------------------------------------------------
    # Step 8: delete rid1 (Alice)
    # ------------------------------------------------------------------
    _section(8, "delete")
    engine.delete("users", rid1)
    renderer.render_message(f"Deleted rid1={rid1.page_id}:{rid1.slot_id} (Alice)")

    # ------------------------------------------------------------------
    # Step 9: restart (fresh StorageEngine) + verify
    # ------------------------------------------------------------------
    _section(9, "restart + verify")
    engine2 = StorageEngine(db_dir)
    rows_after = engine2.scan("users")
    meta2 = engine2.describe_table("users")
    renderer.render_rows(
        meta2,
        [(f"{r.rid.page_id}:{r.rid.slot_id}", r.values) for r in rows_after],
    )

    # Confirm Alice is gone
    try:
        engine2.get("users", rid1)
        renderer.render_error("UNEXPECTED: Alice (rid1) still present after delete!")
    except RecordNotFoundError:
        renderer.render_message("Confirmed: Alice (rid1) is gone after restart.")

    # Confirm Bobby persists
    bobby = engine2.get("users", new_rid)
    renderer.render_message(
        f"Confirmed: Bobby persists at {new_rid.page_id}:{new_rid.slot_id} -> {bobby}"
    )


if __name__ == "__main__":
    try:
        main()
    except SuperDBError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
