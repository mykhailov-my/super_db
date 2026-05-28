"""B+Tree index node layouts, value types, key codec, and fanout helpers.

Mirrors the page_layout.py / page.py split:
  node_layout.py — struct constants + encode/decode (this file)
  bplustree.py   — BPlusTree class (algorithm + I/O)

All struct formats use explicit '<' (little-endian, no alignment padding).
Zero rich/loguru imports — renderer firewall applies to the entire index/ package.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

from ..catalog.schema import ColumnType  # noqa: F401 — exported for bplustree convenience
from ..common.errors import IndexKeyTooLongError, StorageError
from ..storage.rid import RID

# ---------------------------------------------------------------------------
# Node type and key type sentinels
# ---------------------------------------------------------------------------

NODE_TYPE_LEAF = 0
NODE_TYPE_INTERNAL = 1
NODE_TYPE_HEADER = 0xFF

KEY_TYPE_INT = 0
KEY_TYPE_TEXT = 1

# ---------------------------------------------------------------------------
# File header constants
# ---------------------------------------------------------------------------

IDX_MAGIC = b"SUPERIDX"  # exactly 8 bytes
INDEX_FORMAT_VERSION = 1
TEXT_KEY_CAP_DEFAULT = 128  # max UTF-8 bytes for a TEXT key in one index entry
NULL_PAGE_ID = 0  # next_leaf == 0 means "no right sibling"

# ---------------------------------------------------------------------------
# Struct constants (all '<' little-endian — never '>'/'@'/'=')
# ---------------------------------------------------------------------------

# Index file header fixed part: magic(8) + fv(2) + tag(1) + ktype(1) + tcap(2) + root_pid(4)
IDX_HDR_FIXED = struct.Struct("<8sHBBHI")  # 18 bytes

# Leaf node header: node_type(1) + entry_count(2) + next_leaf(4)
LEAF_HDR = struct.Struct("<BHI")  # 7 bytes

# Internal node header: node_type(1) + key_count(2)
INT_NODE_HDR = struct.Struct("<BH")  # 3 bytes

# Leaf INT entry: i32 key(4) + u32 page_id(4) + u32 slot_id(4)
INT_LENTRY = struct.Struct("<iII")  # 12 bytes

# Internal node navigation pieces
INT_ICHILD = struct.Struct("<I")  # u32 child_page_id — 4 bytes
INT_IKEY = struct.Struct("<i")  # i32 separator key  — 4 bytes

# RID part used in TEXT leaf entries: u32 page_id + u32 slot_id
RID_PART = struct.Struct("<II")  # 8 bytes

# TEXT key length prefix
U16 = struct.Struct("<H")  # 2 bytes

# ---------------------------------------------------------------------------
# Value types (immutable frozen dataclasses — house immutability rule)
# ---------------------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class LeafNode:
    """Leaf B+Tree node: stores (encoded_key, RID) pairs sorted by key.

    Keys are stored as encoded bytes (never decoded str — Pitfall 6).
    next_leaf is the page_id of the right sibling leaf; 0 means no sibling.
    """

    entries: tuple[tuple[bytes, RID], ...]
    next_leaf: int


@dataclass(slots=True, frozen=True)
class InternalNode:
    """Internal B+Tree node: N sorted separator keys and N+1 child page_ids.

    Layout mirrors the on-disk interleaved format: child_0, key_0, child_1, ..., child_N.
    Keys are stored as encoded bytes.
    """

    keys: tuple[bytes, ...]
    children: tuple[int, ...]


@dataclass(slots=True, frozen=True)
class Header:
    """Decoded index file header (page 0)."""

    magic: bytes
    format_version: int
    key_type: int
    text_key_cap: int
    root_page_id: int
    col_name: str


# ---------------------------------------------------------------------------
# Key codec
# ---------------------------------------------------------------------------


def encode_int_key(key: int) -> bytes:
    """Encode a signed integer key as 4-byte little-endian int32.

    Raises StorageError if key is outside the int32 range.
    """
    try:
        return INT_IKEY.pack(key)
    except struct.error as exc:
        raise StorageError(f"INT key {key} out of int32 range") from exc


def encode_text_key(key: str, cap: int) -> bytes:
    """Encode a text key as a fixed-size slot: u16 LE length + UTF-8 payload zero-padded to cap.

    Total slot size: 2 + cap bytes.
    Raises IndexKeyTooLongError if the UTF-8 encoding exceeds cap bytes.
    """
    raw = key.encode("utf-8")
    if len(raw) > cap:
        raise IndexKeyTooLongError(
            f"TEXT key {len(raw)}B exceeds index cap {cap}B"
        )
    return U16.pack(len(raw)) + raw.ljust(cap, b"\x00")


def compare_int_keys(a: bytes, b: bytes) -> int:
    """Compare two encoded INT keys. Returns negative, zero, or positive (signed order)."""
    va = INT_IKEY.unpack(a)[0]
    vb = INT_IKEY.unpack(b)[0]
    return (va > vb) - (va < vb)


def compare_text_keys(a: bytes, b: bytes) -> int:
    """Compare two encoded TEXT keys by raw UTF-8 byte order (D-02).

    Extracts the actual payload bytes from each fixed-size slot and compares
    as Python bytes — never as str (Pitfall 6).
    """
    la = U16.unpack(a[:2])[0]
    lb = U16.unpack(b[:2])[0]
    ra = a[2 : 2 + la]
    rb = b[2 : 2 + lb]
    return (ra > rb) - (ra < rb)


# ---------------------------------------------------------------------------
# Fanout helpers (D-05)
# ---------------------------------------------------------------------------


def int_leaf_max(page_size: int) -> int:
    """Max INT entries in a leaf node for the given page_size."""
    return (page_size - LEAF_HDR.size) // INT_LENTRY.size


def int_internal_max_keys(page_size: int) -> int:
    """Max separator keys in an INT internal node for the given page_size."""
    return (page_size - INT_NODE_HDR.size - INT_ICHILD.size) // (INT_ICHILD.size + INT_IKEY.size)


def text_entry_size(cap: int) -> int:
    """Byte size of one TEXT leaf entry: 2-byte len prefix + cap payload + 8-byte RID."""
    return 2 + cap + RID_PART.size  # = 2 + cap + 8


def text_leaf_max(page_size: int, cap: int) -> int:
    """Max TEXT entries in a leaf node."""
    return (page_size - LEAF_HDR.size) // text_entry_size(cap)


def text_internal_max_keys(page_size: int, cap: int) -> int:
    """Max separator keys in a TEXT internal node."""
    return (page_size - INT_NODE_HDR.size - INT_ICHILD.size) // (INT_ICHILD.size + (2 + cap))


def assert_node_fits(node_bytes_len: int, page_size: int) -> None:
    """Assert that the serialized node does not exceed the page size (D-05).

    Raises StorageError if node_bytes_len > page_size.
    Called by every encode function before returning.
    """
    if node_bytes_len > page_size:
        raise StorageError(
            f"node size {node_bytes_len} exceeds page_size {page_size}"
        )


# ---------------------------------------------------------------------------
# Header encode / decode
# ---------------------------------------------------------------------------


def encode_header(
    key_type: int,
    text_key_cap: int,
    root_page_id: int,
    col_name: str,
    page_size: int,
) -> bytes:
    """Encode the index file header page (page 0) to exactly page_size bytes."""
    buf = bytearray(page_size)
    mv = memoryview(buf)
    fixed = IDX_HDR_FIXED.pack(
        IDX_MAGIC,
        INDEX_FORMAT_VERSION,
        NODE_TYPE_HEADER,
        key_type,
        text_key_cap,
        root_page_id,
    )
    end = IDX_HDR_FIXED.size
    mv[0:end] = fixed
    col_bytes = col_name.encode("utf-8")
    col_len = len(col_bytes)
    if col_len > 255:
        raise StorageError(f"key column name too long: {col_len} bytes (max 255)")
    if end + 1 + col_len > page_size:
        raise StorageError(f"key column name does not fit in a {page_size}B header page")
    mv[end] = col_len
    end += 1
    mv[end : end + col_len] = col_bytes
    # remainder stays as zero padding
    return bytes(buf)


def decode_header(data: bytes) -> Header:
    """Decode the index file header page.

    Validates magic and format_version before trusting any other field (Pitfall 8).
    Raises StorageError on invalid magic or unsupported version.
    """
    magic, fv, _tag, key_type, text_key_cap, root_page_id = IDX_HDR_FIXED.unpack(
        data[: IDX_HDR_FIXED.size]
    )
    if magic != IDX_MAGIC:
        raise StorageError("not a super_db index file")
    if fv != INDEX_FORMAT_VERSION:
        raise StorageError(f"unsupported index format version {fv}")
    col_len = data[IDX_HDR_FIXED.size]
    col_name = data[IDX_HDR_FIXED.size + 1 : IDX_HDR_FIXED.size + 1 + col_len].decode("utf-8")
    return Header(
        magic=magic,
        format_version=fv,
        key_type=key_type,
        text_key_cap=text_key_cap,
        root_page_id=root_page_id,
        col_name=col_name,
    )


# ---------------------------------------------------------------------------
# Leaf encode / decode — separate INT and TEXT paths (D-07, Pitfall 9)
# ---------------------------------------------------------------------------


def _encode_leaf_int(node: LeafNode, page_size: int) -> bytes:
    """Encode a leaf node with INT keys to a full page_size byte buffer."""
    buf = bytearray(page_size)
    mv = memoryview(buf)
    mv[0 : LEAF_HDR.size] = LEAF_HDR.pack(NODE_TYPE_LEAF, len(node.entries), node.next_leaf)
    pos = LEAF_HDR.size
    for key_bytes, rid in node.entries:
        mv[pos : pos + INT_LENTRY.size] = INT_LENTRY.pack(
            INT_IKEY.unpack(key_bytes)[0], rid.page_id, rid.slot_id
        )
        pos += INT_LENTRY.size
    assert_node_fits(pos, page_size)
    return bytes(buf)


def _decode_leaf_int(data: bytes) -> LeafNode:
    """Decode a leaf node with INT keys from raw page bytes."""
    node_type, entry_count, next_leaf = LEAF_HDR.unpack(data[: LEAF_HDR.size])
    if node_type != NODE_TYPE_LEAF:
        raise StorageError(f"expected leaf node, got node_type {node_type}")
    entries = []
    pos = LEAF_HDR.size
    for _ in range(entry_count):
        key_i, pid, sid = INT_LENTRY.unpack(data[pos : pos + INT_LENTRY.size])
        entries.append((INT_IKEY.pack(key_i), RID(pid, sid)))
        pos += INT_LENTRY.size
    return LeafNode(entries=tuple(entries), next_leaf=next_leaf)


def _encode_leaf_text(node: LeafNode, cap: int, page_size: int) -> bytes:
    """Encode a leaf node with TEXT keys to a full page_size byte buffer.

    Each TEXT entry occupies exactly (2 + cap + 8) bytes: the fixed key slot
    followed by the RID.
    """
    buf = bytearray(page_size)
    mv = memoryview(buf)
    mv[0 : LEAF_HDR.size] = LEAF_HDR.pack(NODE_TYPE_LEAF, len(node.entries), node.next_leaf)
    pos = LEAF_HDR.size
    key_slot = 2 + cap
    entry_sz = key_slot + RID_PART.size
    for key_bytes, rid in node.entries:
        mv[pos : pos + key_slot] = key_bytes
        mv[pos + key_slot : pos + entry_sz] = RID_PART.pack(rid.page_id, rid.slot_id)
        pos += entry_sz
    assert_node_fits(pos, page_size)
    return bytes(buf)


def _decode_leaf_text(data: bytes, cap: int) -> LeafNode:
    """Decode a leaf node with TEXT keys from raw page bytes."""
    node_type, entry_count, next_leaf = LEAF_HDR.unpack(data[: LEAF_HDR.size])
    if node_type != NODE_TYPE_LEAF:
        raise StorageError(f"expected leaf node, got node_type {node_type}")
    entries = []
    pos = LEAF_HDR.size
    key_slot = 2 + cap
    entry_sz = key_slot + RID_PART.size
    for _ in range(entry_count):
        key_bytes = bytes(data[pos : pos + key_slot])
        pid, sid = RID_PART.unpack(data[pos + key_slot : pos + entry_sz])
        entries.append((key_bytes, RID(pid, sid)))
        pos += entry_sz
    return LeafNode(entries=tuple(entries), next_leaf=next_leaf)


def encode_leaf(node: LeafNode, key_type: int, text_key_cap: int, page_size: int) -> bytes:
    """Encode a leaf node to a full page_size byte buffer.

    Dispatches to the INT or TEXT implementation (D-07 — no is_leaf soup).
    """
    if key_type == KEY_TYPE_INT:
        return _encode_leaf_int(node, page_size)
    return _encode_leaf_text(node, text_key_cap, page_size)


# ---------------------------------------------------------------------------
# Internal node encode / decode — separate INT and TEXT paths (D-07, Pitfall 9)
# ---------------------------------------------------------------------------


def _encode_internal_int(node: InternalNode, page_size: int) -> bytes:
    """Encode an internal node with INT separator keys.

    On-disk layout: [child_0][key_0][child_1]...[key_{N-1}][child_N]
    """
    buf = bytearray(page_size)
    mv = memoryview(buf)
    mv[0 : INT_NODE_HDR.size] = INT_NODE_HDR.pack(NODE_TYPE_INTERNAL, len(node.keys))
    pos = INT_NODE_HDR.size
    for i, child_pid in enumerate(node.children):
        mv[pos : pos + INT_ICHILD.size] = INT_ICHILD.pack(child_pid)
        pos += INT_ICHILD.size
        if i < len(node.keys):
            mv[pos : pos + INT_IKEY.size] = node.keys[i]
            pos += INT_IKEY.size
    assert_node_fits(pos, page_size)
    return bytes(buf)


def _decode_internal_int(data: bytes) -> InternalNode:
    """Decode an internal node with INT separator keys from raw page bytes."""
    node_type, key_count = INT_NODE_HDR.unpack(data[: INT_NODE_HDR.size])
    if node_type != NODE_TYPE_INTERNAL:
        raise StorageError(f"expected internal node, got node_type {node_type}")
    pos = INT_NODE_HDR.size
    children = []
    keys = []
    for i in range(key_count + 1):
        pid = INT_ICHILD.unpack(data[pos : pos + INT_ICHILD.size])[0]
        children.append(pid)
        pos += INT_ICHILD.size
        if i < key_count:
            key_bytes = bytes(data[pos : pos + INT_IKEY.size])
            keys.append(key_bytes)
            pos += INT_IKEY.size
    return InternalNode(keys=tuple(keys), children=tuple(children))


def _encode_internal_text(node: InternalNode, cap: int, page_size: int) -> bytes:
    """Encode an internal node with TEXT separator keys.

    Each separator key occupies exactly (2 + cap) bytes (same fixed slot as leaf).
    On-disk layout: [child_0][key_0][child_1]...[key_{N-1}][child_N]
    """
    buf = bytearray(page_size)
    mv = memoryview(buf)
    mv[0 : INT_NODE_HDR.size] = INT_NODE_HDR.pack(NODE_TYPE_INTERNAL, len(node.keys))
    pos = INT_NODE_HDR.size
    key_slot = 2 + cap
    for i, child_pid in enumerate(node.children):
        mv[pos : pos + INT_ICHILD.size] = INT_ICHILD.pack(child_pid)
        pos += INT_ICHILD.size
        if i < len(node.keys):
            mv[pos : pos + key_slot] = node.keys[i]
            pos += key_slot
    assert_node_fits(pos, page_size)
    return bytes(buf)


def _decode_internal_text(data: bytes, cap: int) -> InternalNode:
    """Decode an internal node with TEXT separator keys from raw page bytes."""
    node_type, key_count = INT_NODE_HDR.unpack(data[: INT_NODE_HDR.size])
    if node_type != NODE_TYPE_INTERNAL:
        raise StorageError(f"expected internal node, got node_type {node_type}")
    pos = INT_NODE_HDR.size
    key_slot = 2 + cap
    children = []
    keys = []
    for i in range(key_count + 1):
        pid = INT_ICHILD.unpack(data[pos : pos + INT_ICHILD.size])[0]
        children.append(pid)
        pos += INT_ICHILD.size
        if i < key_count:
            key_bytes = bytes(data[pos : pos + key_slot])
            keys.append(key_bytes)
            pos += key_slot
    return InternalNode(keys=tuple(keys), children=tuple(children))


def encode_internal(
    node: InternalNode, key_type: int, text_key_cap: int, page_size: int
) -> bytes:
    """Encode an internal node to a full page_size byte buffer.

    Dispatches to the INT or TEXT implementation (D-07 — separate code paths).
    """
    if key_type == KEY_TYPE_INT:
        return _encode_internal_int(node, page_size)
    return _encode_internal_text(node, text_key_cap, page_size)


# ---------------------------------------------------------------------------
# Top-level decoder dispatcher
# ---------------------------------------------------------------------------


def decode_node(data: bytes, key_type: int, text_key_cap: int) -> LeafNode | InternalNode:
    """Decode a node page by reading the first byte (node type tag).

    Returns LeafNode or InternalNode. Raises StorageError on unknown tag.
    This is what bplustree._read_node calls — the return type drives isinstance
    branching in the algorithm (separate leaf/internal code paths, D-07).
    """
    tag = data[0]
    if tag == NODE_TYPE_LEAF:
        if key_type == KEY_TYPE_INT:
            return _decode_leaf_int(data)
        return _decode_leaf_text(data, text_key_cap)
    if tag == NODE_TYPE_INTERNAL:
        if key_type == KEY_TYPE_INT:
            return _decode_internal_int(data)
        return _decode_internal_text(data, text_key_cap)
    raise StorageError(f"unknown node type tag {tag}")
