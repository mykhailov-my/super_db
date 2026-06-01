import argparse
import sys

from loguru import logger

from super_db import __version__
from super_db.cli.commands.index import add_index_parser, run_index
from super_db.cli.commands.init import add_init_parser, run_init
from super_db.cli.commands.page import add_page_parser, run_page
from super_db.cli.commands.row import add_row_parser, run_row
from super_db.cli.commands.table import add_table_parser, run_table
from super_db.common.errors import SuperDBError
from super_db.common.log import setup_logging
from super_db.render.rich_renderer import RichRenderer


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="db-cli", description="super_db database CLI")
    parser.add_argument("--version", action="version", version=f"db-cli {__version__}")
    parser.add_argument("--db", metavar="PATH", help="database directory")
    parser.add_argument("--debug", action="store_true", help="enable debug logging")
    parser.add_argument("--verbose", action="store_true", help="enable info logging")

    nouns = parser.add_subparsers(dest="noun", title="commands", metavar="<command>")
    db_parser = nouns.add_parser("db", help="database-level commands")
    db_verbs = db_parser.add_subparsers(dest="verb", title="db commands", metavar="<verb>")
    add_init_parser(db_verbs)

    table_parser = nouns.add_parser("table", help="table-level commands")
    table_verbs = table_parser.add_subparsers(dest="verb", title="table commands", metavar="<verb>")
    add_table_parser(table_verbs)

    row_parser = nouns.add_parser("row", help="row-level data operations")
    row_verbs = row_parser.add_subparsers(dest="verb", title="row commands", metavar="<verb>")
    add_row_parser(row_verbs)

    page_parser = nouns.add_parser("page", help="page visualization commands")
    page_verbs = page_parser.add_subparsers(dest="verb", title="page commands", metavar="<verb>")
    add_page_parser(page_verbs)

    index_parser = nouns.add_parser("index", help="index visualization commands")
    index_verbs = index_parser.add_subparsers(dest="verb", title="index commands", metavar="<verb>")
    add_index_parser(index_verbs)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    setup_logging(debug=args.debug, verbose=args.verbose)

    if args.noun is None:
        parser.print_help(sys.stderr)
        sys.exit(1)

    renderer = RichRenderer()
    logger.debug("cli: command noun={noun} verb={verb}", noun=args.noun, verb=getattr(args, "verb", None))

    try:
        if args.noun == "db" and args.verb == "init":
            run_init(args, renderer)
        elif args.noun == "table":
            run_table(args, renderer)
        elif args.noun == "row":
            run_row(args, renderer)
        elif args.noun == "page":
            run_page(args, renderer)
        elif args.noun == "index":
            run_index(args, renderer)
        else:
            parser.print_help(sys.stderr)
            sys.exit(1)
    except SuperDBError as exc:
        logger.debug("cli: failed reason={exc!r}", exc=exc)
        renderer.render_error(str(exc))
        sys.exit(1)
    except ValueError as exc:
        logger.debug("cli: validation error reason={exc!r}", exc=exc)
        renderer.render_error(str(exc))
        sys.exit(1)
