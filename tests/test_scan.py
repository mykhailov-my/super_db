"""Tests for catalog.scan() — sequential heap scan (SCAN-01, SCAN-02, SCAN-03)."""

import os
from pathlib import Path

import pytest

from superdb.catalog import create_table, insert, open_table, scan
from superdb.database import init_db
from superdb.errors import StorageError
from superdb.page import Page


def _as_set(rows):
    return {(r.rid.page_id, r.rid.slot_id, tuple(sorted(r.values.items()))) for r in rows}


def test_scan_empty_heap(db_dir: Path) -> None:
    # Arrange
    init_db(db_dir)
    create_table(db_dir, "t", [("id", "INT", False)])
    handle = open_table(db_dir, "t")

    # Act
    result = scan(handle)

    # Assert
    assert result == []


def test_scan_live_only(db_dir: Path) -> None:
    # Arrange
    init_db(db_dir)
    create_table(db_dir, "t", [("id", "INT", False), ("name", "TEXT", True)])
    handle = open_table(db_dir, "t")

    rid_a = insert(handle, {"id": 1, "name": "alice"})
    rid_b = insert(handle, {"id": 2, "name": "bob"})
    rid_c = insert(handle, {"id": 3, "name": "carol"})

    # Tombstone rid_b directly at the page level (no Phase 6 delete yet)
    ps = handle.meta.page_size
    path = handle.heap_path
    raw_page = path.read_bytes()[rid_b.page_id * ps : (rid_b.page_id + 1) * ps]
    page = Page.from_bytes(raw_page, ps)
    page.tombstone_slot(rid_b.slot_id)
    fd = os.open(str(path), os.O_RDWR)
    try:
        os.pwrite(fd, page.to_bytes(), rid_b.page_id * ps)
        os.fsync(fd)
    finally:
        os.close(fd)

    # Act
    rows = scan(handle)

    # Assert — tombstoned rid_b must be absent; rid_a and rid_c must be present
    rids = {r.rid for r in rows}
    assert rid_b not in rids
    assert rid_a in rids
    assert rid_c in rids


def test_scan_multi_page(db_dir: Path) -> None:
    # Arrange — use a small page size to force multiple pages with few inserts
    # page_size=64: holds 5 INT records each, so 20 inserts → 4 pages
    init_db(db_dir)
    page_size = 64
    create_table(db_dir, "t", [("id", "INT", False)], page_size=page_size)
    handle = open_table(db_dir, "t")

    inserted_rids = []
    for i in range(20):
        rid = insert(handle, {"id": i})
        inserted_rids.append((rid, {"id": i}))

    # Verify that the heap actually spans more than one page
    heap_size = handle.heap_path.stat().st_size
    assert heap_size // page_size >= 2, "expected at least 2 pages for multi-page test"

    # Act
    rows = scan(handle)

    # Assert — all inserted rows returned, unordered comparison
    expected = {
        (rid.page_id, rid.slot_id, tuple(sorted(vals.items()))) for rid, vals in inserted_rids
    }
    assert _as_set(rows) == expected


def test_scan_restart_returns_same_set(db_dir: Path) -> None:
    # Arrange
    init_db(db_dir)
    create_table(db_dir, "t", [("id", "INT", False), ("label", "TEXT", True)])
    handle1 = open_table(db_dir, "t")
    for i in range(5):
        insert(handle1, {"id": i, "label": f"item{i}"})

    rows_before = scan(handle1)

    # Act — fresh handle simulates process restart (no shared state)
    handle2 = open_table(db_dir, "t")
    rows_after = scan(handle2)

    # Assert — same live set, unordered
    assert _as_set(rows_after) == _as_set(rows_before)


def test_scan_corrupt_heap_raises(db_dir: Path) -> None:
    # Arrange — write a heap file whose size is not a multiple of page_size
    init_db(db_dir)
    create_table(db_dir, "t", [("id", "INT", False)])
    handle = open_table(db_dir, "t")
    handle.heap_path.write_bytes(b"x" * (handle.meta.page_size - 1))

    # Act / Assert
    with pytest.raises(StorageError):
        scan(handle)


def test_scan_missing_heap_raises(db_dir: Path) -> None:
    # Arrange — heap file deleted after the handle was opened
    init_db(db_dir)
    create_table(db_dir, "t", [("id", "INT", False)])
    handle = open_table(db_dir, "t")
    handle.heap_path.unlink()

    # Act / Assert — a missing heap surfaces StorageError, not a raw OSError
    with pytest.raises(StorageError):
        scan(handle)
