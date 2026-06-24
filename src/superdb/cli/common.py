import argparse
from pathlib import Path


def add_db_arg(parser: argparse.ArgumentParser, help: str | None = None) -> None:
    """Add the shared --db PATH argument to a subparser."""
    parser.add_argument("--db", metavar="PATH", default=argparse.SUPPRESS, help=help)


def resolve_db_dir(args) -> Path:
    """Resolve the database directory from --db, or raise if it was omitted."""
    db = getattr(args, "db", None)
    if db is None:
        raise ValueError("missing --db PATH (the database directory)")
    return Path(db).resolve()
