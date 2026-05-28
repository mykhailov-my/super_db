"""Integration tests for StorageEngine index composition (IDX-01..IDX-04, plan 07-03).

Tests verify:
  - build_index populates the B+Tree from existing heap rows (IDX-01)
  - insert after build_index maintains the index (IDX-02)
  - search RID resolves via get to the original record (IDX-03)
  - index persists across fresh objects (IDX-04)
  - unknown keycol raises StorageError
"""
from pathlib import Path

import pytest

from super_db.common.errors import StorageError
from super_db.db import init_db
from super_db.index.bplustree import BPlusTree
from super_db.storage.engine import StorageEngine
from super_db.storage.rid import RID


# ---------------------------------------------------------------------------
# IDX-01: build_index populates B+Tree from heap rows
# ---------------------------------------------------------------------------

def test_build_over_heap(db_dir: Path) -> None:
    """build_index scans an existing heap and populates the B+Tree (IDX-01).

    Each key inserted before build_index must be searchable via the index,
    and the returned RID must resolve via engine.get to the original record.
    """
    # Arrange
    init_db(db_dir)
    engine = StorageEngine(db_dir)
    engine.create_table("t", [("id", "INT", False), ("name", "TEXT", True)])

    records = [{"id": i, "name": f"user{i}"} for i in range(8)]
    rids: dict[int, RID] = {}
    for rec in records:
        rid = engine.insert("t", rec)
        rids[rec["id"]] = rid

    # Act
    engine.build_index("t", "id")

    # Assert — every pre-existing key resolves to the recorded RID and the correct record
    for rec in records:
        found_rid = engine._index.search(rec["id"])
        assert found_rid == rids[rec["id"]], f"index RID mismatch for id={rec['id']}"
        result = engine.get("t", found_rid)
        assert result == rec, f"record mismatch for id={rec['id']}"


# ---------------------------------------------------------------------------
# IDX-02: insert after build_index maintains the index
# ---------------------------------------------------------------------------

def test_insert_maintains_index(db_dir: Path) -> None:
    """A new insert after build_index updates the index (IDX-02).

    The newly inserted record's key must be searchable via the index,
    and the returned RID must resolve to the new record.
    """
    # Arrange
    init_db(db_dir)
    engine = StorageEngine(db_dir)
    engine.create_table("t", [("id", "INT", False), ("name", "TEXT", True)])
    for i in range(4):
        engine.insert("t", {"id": i, "name": f"early{i}"})
    engine.build_index("t", "id")

    # Act
    new_rid = engine.insert("t", {"id": 99, "name": "late"})

    # Assert — the newly inserted key is searchable and resolves to the correct RID
    assert engine._index.search(99) == new_rid
    assert engine.get("t", new_rid) == {"id": 99, "name": "late"}


# ---------------------------------------------------------------------------
# IDX-03: search RID resolves via get to the original record
# ---------------------------------------------------------------------------

def test_index_search_resolves_via_get(db_dir: Path) -> None:
    """search returns a RID that engine.get resolves to the original record (IDX-03)."""
    # Arrange
    init_db(db_dir)
    engine = StorageEngine(db_dir)
    engine.create_table("t", [("id", "INT", False), ("name", "TEXT", True)])
    records = [{"id": 10, "name": "alpha"}, {"id": 20, "name": "beta"}, {"id": 30, "name": "gamma"}]
    for rec in records:
        engine.insert("t", rec)
    engine.build_index("t", "id")

    # Act + Assert — pick a key, search, resolve via get
    rid = engine._index.search(20)
    result = engine.get("t", rid)
    assert result == {"id": 20, "name": "beta"}


# ---------------------------------------------------------------------------
# IDX-04: index persists across fresh BPlusTree object (and fresh engine)
# ---------------------------------------------------------------------------

def test_index_persists_after_restart(db_dir: Path) -> None:
    """Index file is durable: a fresh BPlusTree opened over the .idx file
    resolves all keys; a fresh StorageEngine resolves RIDs to records (IDX-04).
    """
    # Arrange
    init_db(db_dir)
    engine = StorageEngine(db_dir)
    engine.create_table("t", [("id", "INT", False), ("name", "TEXT", True)])
    records = [{"id": i, "name": f"row{i}"} for i in range(6)]
    rids: dict[int, RID] = {}
    for rec in records:
        rid = engine.insert("t", rec)
        rids[rec["id"]] = rid
    engine.build_index("t", "id")

    # Gather the .idx path and page_size (from the catalog, not from engine state)
    meta = engine.describe_table("t")
    idx_path = db_dir / f"{meta.name}.idx"
    page_size = meta.page_size

    # Act — construct a completely fresh BPlusTree (no shared state with engine._index)
    fresh_tree = BPlusTree(idx_path, page_size)

    # Assert — every key resolves to the correct RID from disk
    for rec in records:
        found_rid = fresh_tree.search(rec["id"])
        assert found_rid == rids[rec["id"]], f"fresh tree RID mismatch for id={rec['id']}"

    # Also verify the RID resolves via a fresh StorageEngine
    fresh_engine = StorageEngine(db_dir)
    for rec in records:
        found_rid = fresh_tree.search(rec["id"])
        result = fresh_engine.get("t", found_rid)
        assert result == rec, f"fresh engine get mismatch for id={rec['id']}"


# ---------------------------------------------------------------------------
# Unknown keycol raises StorageError
# ---------------------------------------------------------------------------

def test_build_unknown_keycol_raises(db_dir: Path) -> None:
    """build_index raises StorageError when keycol is not a column of the table."""
    # Arrange
    init_db(db_dir)
    engine = StorageEngine(db_dir)
    engine.create_table("t", [("id", "INT", False), ("name", "TEXT", True)])
    engine.insert("t", {"id": 1, "name": "a"})

    # Act + Assert
    with pytest.raises(StorageError):
        engine.build_index("t", "missing")


def test_build_index_rebuild_replaces_stale(db_dir: Path) -> None:
    """A second build_index over the same table replaces the old .idx cleanly
    (the index file is unlinked first; create() refuses to clobber otherwise)."""
    init_db(db_dir)
    engine = StorageEngine(db_dir)
    engine.create_table("t", [("id", "INT", False)])
    for i in range(5):
        engine.insert("t", {"id": i})
    engine.build_index("t", "id")

    # Rebuilding must not raise and must still resolve every key.
    engine.build_index("t", "id")
    for i in range(5):
        rid = engine._index.search(i)
        assert engine.get("t", rid) == {"id": i}


def test_index_negative_int_keys(db_dir: Path) -> None:
    """INT keys sort by signed numeric order — negative keys resolve correctly."""
    init_db(db_dir)
    engine = StorageEngine(db_dir)
    engine.create_table("t", [("id", "INT", False)])
    keys = [-100, -1, 0, 1, 100, -50, 42]
    rids = {k: engine.insert("t", {"id": k}) for k in keys}
    engine.build_index("t", "id")

    for k in keys:
        assert engine._index.search(k) == rids[k]
