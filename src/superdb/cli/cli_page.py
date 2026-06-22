import argparse
import os

from superdb.catalog.catalog import open_table
from superdb.cli.cli_common import resolve_db_dir as _resolve_db
from superdb.storage.page import Page
from superdb.storage.page_layout import HEADER_SIZE, SLOT_FLAG_LIVE


def add_page_parser(verbs) -> None:
    show = verbs.add_parser("show", help="show slotted-page byte-layout map")
    show.add_argument("--db", metavar="PATH", default=argparse.SUPPRESS)
    show.add_argument("--table", metavar="NAME", required=True)
    show.add_argument("--page", metavar="N", type=int, required=True)


def run_page(args, renderer) -> None:
    verb = getattr(args, "verb", None)
    if verb == "show":
        db_dir = _resolve_db(args)
        handle = open_table(db_dir, args.table)
        page_size = handle.meta.page_size
        fd = os.open(str(handle.heap_path), os.O_RDONLY)
        try:
            page_count = os.fstat(fd).st_size // page_size
            if args.page < 0 or args.page >= page_count:
                raise ValueError(
                    f"page {args.page} not found in table '{args.table}'"
                    f" (table has {page_count} pages)"
                )
            raw = os.pread(fd, page_size, args.page * page_size)
        finally:
            os.close(fd)
        page = Page.from_bytes(raw, page_size)
        slots = []
        for sid in range(page.slot_count):
            off, ln, fl = page.slot(sid)
            slots.append((sid, off, ln, bool(fl & SLOT_FLAG_LIVE)))
        renderer.render_page(
            table_name=args.table,
            page_id=args.page,
            page_size=page_size,
            header_bytes=HEADER_SIZE,
            slot_count=page.slot_count,
            slots=slots,
            free_space_start=page.free_start,
            free_space_end=page.free_end,
        )
    else:
        raise ValueError("usage: db-cli page show --table NAME --page N")
