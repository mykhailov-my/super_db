"""Tests for bplustree.py — BPlusTree create/insert/search + restart (IDX-02..IDX-04).

Covers 11 of the 12 named validation tests; test_build_over_heap is plan 07-03's
engine-integration test and is intentionally omitted here.
"""
import random

import pytest

from superdb.bplustree import BPlusTree
from superdb.errors import DuplicateKeyError, IndexKeyNotFoundError, IndexKeyTooLongError
from superdb.node_layout import (
    KEY_TYPE_INT,
    KEY_TYPE_TEXT,
    NULL_PAGE_ID,
    TEXT_KEY_CAP_DEFAULT,
    LeafNode,
    assert_node_fits,
    encode_leaf,
    int_leaf_max,
)
from superdb.rid import RID

# Small page size — forces splits with ~20-entry INT fanout (verified: int_leaf_max(256) = 20)
SMALL_PAGE = 256


def _new_int_tree(tmp_path, page_size=SMALL_PAGE):
    """Create a fresh INT-key index in tmp_path."""
    path = tmp_path / "test.idx"
    return BPlusTree.create(path, page_size, KEY_TYPE_INT, "id"), path


def _new_text_tree(tmp_path, page_size=SMALL_PAGE):
    """Create a fresh TEXT-key index in tmp_path."""
    path = tmp_path / "test.idx"
    return BPlusTree.create(path, page_size, KEY_TYPE_TEXT, "name"), path


# ---------------------------------------------------------------------------
# IDX-02a: insert with no split
# ---------------------------------------------------------------------------

def test_insert_no_split(tmp_path):
    """Insert a small number of INT keys into one leaf; root stays a leaf."""
    # Arrange
    tree, _ = _new_int_tree(tmp_path)
    keys = [10, 5, 20, 1, 15]

    # Act
    for k in keys:
        tree.insert(k, RID(k, 0))

    # Assert
    assert not tree.root_is_internal(), "root should still be a leaf (no split yet)"
    for k in keys:
        assert tree.search(k) == RID(k, 0), f"wrong RID for key {k}"


# ---------------------------------------------------------------------------
# IDX-02b: leaf split — root becomes internal
# ---------------------------------------------------------------------------

def test_leaf_split_root_becomes_internal(tmp_path):
    """Insert just past int_leaf_max(256) keys; root must become an InternalNode."""
    # Arrange
    fanout = int_leaf_max(SMALL_PAGE)  # = 20 for page_size=256
    tree, _ = _new_int_tree(tmp_path)

    # Act: insert fanout+1 keys to trigger the first leaf split
    keys = list(range(fanout + 1))
    for k in keys:
        tree.insert(k, RID(k, 0))

    # Assert
    assert tree.root_is_internal(), "root should be internal after leaf split"
    # The separator key (first key of right leaf) must be searchable
    for k in keys:
        assert tree.search(k) == RID(k, 0), f"key {k} not found after leaf split"


# ---------------------------------------------------------------------------
# IDX-02c: two-level split with INT keys
# ---------------------------------------------------------------------------

def test_two_level_split_int(tmp_path):
    """Insert 30 random INT keys; tree must grow to internal root, all keys resolve."""
    # Arrange
    random.seed(42)
    tree, _ = _new_int_tree(tmp_path)
    keys = list(range(30))
    random.shuffle(keys)

    # Act
    rids = {}
    for k in keys:
        rid = RID(k, 0)
        tree.insert(k, rid)
        rids[k] = rid

    # Assert
    assert tree.root_is_internal(), "root should be internal after >=2-level split"
    for k in keys:
        assert tree.search(k) == rids[k], f"wrong RID for key {k}"


# ---------------------------------------------------------------------------
# IDX-02d: two-level split with TEXT keys
# ---------------------------------------------------------------------------

def test_two_level_split_text(tmp_path):
    """Insert 30 distinct TEXT keys in random order; all keys resolve after splits."""
    # Arrange
    random.seed(99)
    tree, _ = _new_text_tree(tmp_path)
    keys = [f"k{n:04d}" for n in range(30)]
    random.shuffle(keys)

    # Act
    rids = {}
    for k in keys:
        rid = RID(len(rids), 0)
        tree.insert(k, rid)
        rids[k] = rid

    # Assert
    assert tree.root_is_internal(), "root should be internal after TEXT key splits"
    for k in keys:
        assert tree.search(k) == rids[k], f"wrong RID for TEXT key {k!r}"


# ---------------------------------------------------------------------------
# IDX-03: search found
# ---------------------------------------------------------------------------

def test_search_found(tmp_path):
    """Insert known keys; assert exact RIDs returned by search."""
    # Arrange
    tree, _ = _new_int_tree(tmp_path)
    pairs = [(1, RID(0, 0)), (42, RID(3, 7)), (100, RID(10, 2))]

    # Act
    for k, rid in pairs:
        tree.insert(k, rid)

    # Assert
    for k, expected_rid in pairs:
        assert tree.search(k) == expected_rid, f"wrong RID for key {k}"


# ---------------------------------------------------------------------------
# IDX-03: search not found
# ---------------------------------------------------------------------------

def test_search_not_found(tmp_path):
    """Searching for an absent key raises IndexKeyNotFoundError."""
    # Arrange
    tree, _ = _new_int_tree(tmp_path)
    tree.insert(1, RID(0, 0))
    tree.insert(2, RID(0, 1))

    # Act + Assert
    with pytest.raises(IndexKeyNotFoundError):
        tree.search(999)


# ---------------------------------------------------------------------------
# duplicate key
# ---------------------------------------------------------------------------

def test_duplicate_key(tmp_path):
    """Inserting an existing key raises DuplicateKeyError; tree is unchanged."""
    # Arrange
    tree, _ = _new_int_tree(tmp_path)
    tree.insert(42, RID(0, 0))

    # Act + Assert
    with pytest.raises(DuplicateKeyError):
        tree.insert(42, RID(1, 0))

    # Tree is unchanged — the original RID is still there
    assert tree.search(42) == RID(0, 0)


# ---------------------------------------------------------------------------
# fanout assertion
# ---------------------------------------------------------------------------

def test_fanout_assertion(tmp_path):
    """Fanout math produces a positive value; a full leaf encodes within page_size."""
    # Arrange
    ps = SMALL_PAGE
    max_entries = int_leaf_max(ps)

    # Assert: fanout is positive
    assert max_entries > 0, "int_leaf_max must be positive"

    # Build a leaf at exactly max capacity and verify it encodes to <= page_size
    entries = tuple((b"\x00" * 4, RID(i, 0)) for i in range(max_entries))
    full_leaf = LeafNode(entries=entries, next_leaf=NULL_PAGE_ID)
    encoded = encode_leaf(full_leaf, KEY_TYPE_INT, 0, ps)
    assert len(encoded) == ps, f"encoded leaf should be exactly {ps} bytes, got {len(encoded)}"

    # assert_node_fits should NOT raise for page_size bytes
    assert_node_fits(len(encoded), ps)

    # assert_node_fits SHOULD raise when given a length > page_size
    from superdb.errors import StorageError
    with pytest.raises(StorageError):
        assert_node_fits(ps + 1, ps)


# ---------------------------------------------------------------------------
# TEXT key too long
# ---------------------------------------------------------------------------

def test_text_key_too_long(tmp_path):
    """Inserting a TEXT key longer than TEXT_KEY_CAP_DEFAULT raises IndexKeyTooLongError."""
    # Arrange
    tree, _ = _new_text_tree(tmp_path)
    oversized = "x" * (TEXT_KEY_CAP_DEFAULT + 1)

    # Act + Assert
    with pytest.raises(IndexKeyTooLongError):
        tree.insert(oversized, RID(0, 0))


# ---------------------------------------------------------------------------
# IDX-04 MUST-HAVE: restart with INT keys
# ---------------------------------------------------------------------------

def test_restart_int(tmp_path):
    """Insert enough INT keys to force >=2-level split; reopen with fresh objects; every key resolves."""
    # Arrange
    random.seed(7)
    page_size = SMALL_PAGE
    idx_path = tmp_path / "test.idx"
    keys = list(range(30))
    random.shuffle(keys)

    tree = BPlusTree.create(idx_path, page_size, KEY_TYPE_INT, "id")
    rids = {}
    for k in keys:
        rid = RID(page_id=k, slot_id=0)
        tree.insert(k, rid)
        rids[k] = rid

    assert tree.root_is_internal(), "root should be internal after >=2-level split"

    # Act — simulate process restart: construct a fresh BPlusTree (no shared state with tree)
    tree2 = BPlusTree(idx_path, page_size)

    # Assert — every key resolves to the correct RID via disk
    for k in keys:
        assert tree2.search(k) == rids[k], f"wrong RID for key {k} after restart"


# ---------------------------------------------------------------------------
# IDX-04 MUST-HAVE: restart with TEXT keys
# ---------------------------------------------------------------------------

def test_restart_text(tmp_path):
    """Insert 30 TEXT keys to force >=2-level split; reopen with fresh objects; every key resolves."""
    # Arrange
    random.seed(13)
    page_size = SMALL_PAGE
    idx_path = tmp_path / "test.idx"
    keys = [f"key{n:04d}" for n in range(30)]
    random.shuffle(keys)

    tree = BPlusTree.create(idx_path, page_size, KEY_TYPE_TEXT, "name")
    rids = {}
    for i, k in enumerate(keys):
        rid = RID(page_id=i, slot_id=0)
        tree.insert(k, rid)
        rids[k] = rid

    assert tree.root_is_internal(), "root should be internal after TEXT key splits"

    # Act — simulate process restart: construct a fresh BPlusTree (no shared state with tree)
    tree2 = BPlusTree(idx_path, page_size)

    # Assert — every key resolves to the correct RID via disk
    for k in keys:
        assert tree2.search(k) == rids[k], f"wrong RID for TEXT key {k!r} after restart"
