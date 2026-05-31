import argparse
import os
from pathlib import Path

from super_db.catalog.catalog import open_table
from super_db.storage.page import Page
from super_db.storage.page_layout import HEADER_SIZE, SLOT_FLAG_LIVE


def add_page_parser(verbs) -> None:
    show = verbs.add_parser("show", help="show slotted-page byte-layout map")
    show.add_argument("--db", metavar="PATH", default=argparse.SUPPRESS)
    show.add_argument("--table", metavar="NAME", required=True)
    show.add_argument("--page", metavar="N", type=int, required=True)


def _resolve_db(args) -> Path:
    db = getattr(args, "db", None)
    if db is None:
        raise ValueError("missing --db PATH (the database directory)")
    return Path(db).resolve()


def run_page(args, renderer) -> None:
    verb = getattr(args, "verb", None)
    if verb == "show":
        db_dir = _resolve_db(args)
        handle = open_table(db_dir, args.table)
        ps = handle.meta.page_size
        fd = os.open(str(handle.heap_path), os.O_RDONLY)
        try:
            page_count = os.fstat(fd).st_size // ps
            if args.page >= page_count:
                raise ValueError(
                    f"page {args.page} not found in table '{args.table}'"
                    f" (table has {page_count} pages)"
                )
            raw = os.pread(fd, ps, args.page * ps)
        finally:
            os.close(fd)
        page = Page.from_bytes(raw, ps)
        slots = []
        for sid in range(page.slot_count):
            off, ln, fl = page._slot(sid)
            slots.append((sid, off, ln, bool(fl & SLOT_FLAG_LIVE)))
        renderer.render_page(
            table_name=args.table,
            page_id=args.page,
            page_size=ps,
            header_bytes=HEADER_SIZE,
            slot_count=page.slot_count,
            slots=slots,
            free_space_start=page.free_start,
            free_space_end=page.free_end,
        )
    else:
        raise ValueError("usage: db-cli page show --table NAME --page N")
