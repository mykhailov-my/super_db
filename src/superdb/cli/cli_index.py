import argparse
import os

from superdb.catalog.catalog import open_table
from superdb.cli.cli_common import resolve_db_dir as _resolve_db
from superdb.errors import StorageError
from superdb.index.node_layout import (
    INT_IKEY,
    KEY_TYPE_INT,
    NULL_PAGE_ID,
    U16,
    InternalNode,
    LeafNode,
    decode_header,
    decode_node,
)
from superdb.storage.engine import StorageEngine


def add_index_parser(verbs) -> None:
    build = verbs.add_parser("build", help="build a B+Tree index over a key column")
    build.add_argument("--db", metavar="PATH", default=argparse.SUPPRESS)
    build.add_argument("--table", metavar="NAME", required=True)
    build.add_argument("--keycol", metavar="COLUMN", required=True)

    show = verbs.add_parser("show", help="visualize B+Tree index as a tree")
    show.add_argument("--db", metavar="PATH", default=argparse.SUPPRESS)
    show.add_argument("--table", metavar="NAME", required=True)


def _decode_key(key_bytes: bytes, key_type: int) -> object:
    if key_type == KEY_TYPE_INT:
        return INT_IKEY.unpack(key_bytes)[0]
    length = U16.unpack(key_bytes[:2])[0]
    return key_bytes[2 : 2 + length].decode("utf-8")


def _dump_tree(fd: int, page_id: int, key_type: int, cap: int, page_size: int) -> list:
    raw = os.pread(fd, page_size, page_id * page_size)
    node = decode_node(raw, key_type, cap)
    if isinstance(node, InternalNode):
        display_keys = [_decode_key(k, key_type) for k in node.keys]
        children: list = []
        for child_page_id in node.children:
            children.extend(_dump_tree(fd, child_page_id, key_type, cap, page_size))
        return [{"type": "internal", "keys": display_keys, "children": children}]
    # LeafNode
    if not isinstance(node, LeafNode):
        raise StorageError(f"unexpected node type at page {page_id}")
    display_keys = [_decode_key(k, key_type) for k, _rid in node.entries]
    rids = [str(r) for _k, r in node.entries]
    next_leaf = None if node.next_leaf == NULL_PAGE_ID else node.next_leaf
    return [{"type": "leaf", "keys": display_keys, "rids": rids, "next_leaf": next_leaf}]


def run_index(args, renderer) -> None:
    verb = getattr(args, "verb", None)
    if verb == "build":
        db_dir = _resolve_db(args)
        StorageEngine(db_dir).build_index(args.table, args.keycol)
        renderer.render_message(
            f"built index on '{args.table}.{args.keycol}'"
        )
    elif verb == "show":
        db_dir = _resolve_db(args)
        handle = open_table(db_dir, args.table)
        idx_path = db_dir / f"{handle.meta.name}.idx"
        if not idx_path.exists():
            renderer.render_message(f"no index found for table '{args.table}'")
            return
        page_size = handle.meta.page_size
        fd = os.open(str(idx_path), os.O_RDONLY)
        try:
            hdr_raw = os.pread(fd, page_size, 0)
            hdr = decode_header(hdr_raw)
            nodes = _dump_tree(fd, hdr.root_page_id, hdr.key_type, hdr.text_key_cap, page_size)
        finally:
            os.close(fd)
        renderer.render_btree(args.table, hdr.col_name, nodes)
    else:
        raise ValueError("usage: db-cli index build|show --table NAME ...")
