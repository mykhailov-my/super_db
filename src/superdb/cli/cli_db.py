import argparse
from pathlib import Path

from superdb.catalog.database import init_db
from superdb.errors import InitError


def add_init_parser(verbs) -> None:
    p = verbs.add_parser("init", help="initialize a new super_db database")
    # Accept --db after the verb too, but SUPPRESS so it doesn't clobber the
    # global --db when omitted here.
    p.add_argument("--db", metavar="PATH", default=argparse.SUPPRESS, help="database directory")
    p.add_argument("--force", action="store_true", help="re-initialize an existing database")


def run_init(args, renderer) -> None:
    if args.db is None:
        raise InitError("missing --db PATH (the database directory to initialize)")
    db_dir = Path(args.db).resolve()
    init_db(db_dir, force=args.force)
    renderer.render_message(f"Initialized super_db database at {db_dir}")
