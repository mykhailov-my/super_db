"""Tests for storage/heap_file.py — HeapFile insert/get (WRITE-01, WRITE-02, WRITE-03)."""
import os

import pytest

from superdb.constants import DEFAULT_PAGE_SIZE
from superdb.errors import PageFullError, RecordNotFoundError
from superdb.heap_file import HeapFile
from superdb.page import Page
from superdb.rid import RID


def _new_heap(tmp_path, page_size=DEFAULT_PAGE_SIZE):
    path = tmp_path / "t.tbl"
    path.write_bytes(b"")  # 0-byte placeholder, as catalog.create_table does
    return HeapFile(path, page_size), path


def test_insert_returns_rid(tmp_path):
    # Arrange
    hf, _ = _new_heap(tmp_path)
    record = b"hello-record"
    # Act
    rid = hf.insert(record)
    # Assert
    assert rid == RID(0, 0)


def test_insert_into_empty_heap(tmp_path):
    # Arrange
    hf, path = _new_heap(tmp_path)
    record = b"first"
    # Act
    rid = hf.insert(record)
    # Assert
    assert rid.page_id == 0
    assert path.stat().st_size == DEFAULT_PAGE_SIZE


def test_insert_allocates_new_page(tmp_path):
    # Use a small page size to make it easy to fill one page.
    # A 128-byte page can hold at most 128 - HEADER_SIZE - SLOT_ENTRY_SIZE = 114 bytes per insert,
    # but each slot entry also costs SLOT_ENTRY_SIZE bytes of free space.
    # We fill the page with small records until it's full, then insert one more.
    page_size = 128
    hf, path = _new_heap(tmp_path, page_size=page_size)
    # Arrange: fill page 0 by inserting records until the page cannot fit more.
    # Each record uses 1 byte payload + 6 bytes slot entry = 7 bytes from free space.
    record = b"x" * 1
    pages_before = None
    while True:
        test_page = Page.from_bytes(path.read_bytes()[-page_size:] if path.stat().st_size > 0 else Page.new(page_size).to_bytes(), page_size)
        if not test_page.can_fit(len(record)):
            pages_before = path.stat().st_size // page_size
            break
        hf.insert(record)
    # Act: insert when last page is full — should allocate a new page
    rid = hf.insert(record)
    # Assert
    assert rid.page_id == pages_before
    assert path.stat().st_size == (pages_before + 1) * page_size


def test_oversized_record_raises(tmp_path):
    # Arrange
    page_size = DEFAULT_PAGE_SIZE
    hf, path = _new_heap(tmp_path, page_size=page_size)
    # A record that cannot fit on any page: page_size bytes is always too big
    oversized = b"x" * page_size
    initial_size = path.stat().st_size
    # Act + Assert
    with pytest.raises(PageFullError):
        hf.insert(oversized)
    # File must not grow — fd was never opened for the oversized check
    assert path.stat().st_size == initial_size


def test_page_written_to_disk(tmp_path):
    # Arrange
    hf, path = _new_heap(tmp_path)
    record = b"durable-data"
    # Act: insert with the first heap file instance
    rid = hf.insert(record)
    # Assert: construct a brand-new HeapFile to prove data is on disk
    hf2 = HeapFile(path, DEFAULT_PAGE_SIZE)
    assert hf2.get(rid) == record


def test_get_returns_record(tmp_path):
    # Arrange
    hf, _ = _new_heap(tmp_path)
    record = b"hello-record"
    # Act
    rid = hf.insert(record)
    result = hf.get(rid)
    # Assert
    assert result == record


def test_get_out_of_range_page(tmp_path):
    # Arrange: heap has one page (page_id=0); request page_id=99
    hf, _ = _new_heap(tmp_path)
    hf.insert(b"something")
    # Act + Assert
    with pytest.raises(RecordNotFoundError):
        hf.get(RID(99, 0))


def test_get_out_of_range_slot(tmp_path):
    # Arrange: insert one record at slot 0; request slot 5 (doesn't exist)
    hf, _ = _new_heap(tmp_path)
    hf.insert(b"one-record")
    # Act + Assert
    with pytest.raises(RecordNotFoundError):
        hf.get(RID(0, 5))


def test_get_tombstoned_slot(tmp_path):
    # Arrange: insert a record, tombstone it at the page level, write page back
    page_size = DEFAULT_PAGE_SIZE
    hf, path = _new_heap(tmp_path, page_size=page_size)
    record = b"will-be-tombstoned"
    rid = hf.insert(record)
    # Load page, tombstone slot 0, write raw bytes back
    raw_page = path.read_bytes()[rid.page_id * page_size:(rid.page_id + 1) * page_size]
    page = Page.from_bytes(raw_page, page_size)
    page.tombstone_slot(rid.slot_id)
    # Rewrite the page to disk
    fd = os.open(str(path), os.O_RDWR)
    try:
        os.pwrite(fd, page.to_bytes(), rid.page_id * page_size)
        os.fsync(fd)
    finally:
        os.close(fd)
    # Act + Assert: get must raise for the tombstoned slot
    with pytest.raises(RecordNotFoundError):
        hf.get(rid)


# --- delete tests ---

def test_delete_tombstones_slot(tmp_path):
    # Arrange
    hf, _ = _new_heap(tmp_path)
    rid = hf.insert(b"to-be-deleted")
    # Act
    result = hf.delete(rid)
    # Assert: returns None and slot is now dead
    assert result is None
    with pytest.raises(RecordNotFoundError):
        hf.get(rid)


def test_delete_durable_across_reopen(tmp_path):
    # Arrange
    hf, path = _new_heap(tmp_path)
    rid = hf.insert(b"durable-delete")
    hf.delete(rid)
    # Act: reopen with a fresh HeapFile instance (simulates restart)
    hf2 = HeapFile(path, DEFAULT_PAGE_SIZE)
    # Assert: slot is still dead after reopen
    with pytest.raises(RecordNotFoundError):
        hf2.get(rid)


def test_delete_already_dead_raises(tmp_path):
    # Arrange
    hf, _ = _new_heap(tmp_path)
    rid = hf.insert(b"delete-me")
    hf.delete(rid)
    # Act + Assert: second delete must raise, not silently succeed
    with pytest.raises(RecordNotFoundError):
        hf.delete(rid)


def test_delete_out_of_range_page_raises(tmp_path):
    hf, _ = _new_heap(tmp_path)
    hf.insert(b"one")
    with pytest.raises(RecordNotFoundError):
        hf.delete(RID(99, 0))


def test_delete_out_of_range_slot_raises(tmp_path):
    hf, _ = _new_heap(tmp_path)
    hf.insert(b"one")
    with pytest.raises(RecordNotFoundError):
        hf.delete(RID(0, 99))


# --- update tests ---

def test_update_inplace_same_rid(tmp_path):
    # Arrange: same byte length -> in-place, RID unchanged
    hf, _ = _new_heap(tmp_path)
    original = b"hello"
    rid = hf.insert(original)
    # Act
    new_rid = hf.update(rid, b"world")
    # Assert: same RID, new bytes
    assert new_rid == rid
    assert hf.get(rid) == b"world"


def test_update_inplace_durable(tmp_path):
    # Arrange
    hf, path = _new_heap(tmp_path)
    rid = hf.insert(b"aaaaa")
    hf.update(rid, b"bbbbb")
    # Act: reopen to confirm durable
    hf2 = HeapFile(path, DEFAULT_PAGE_SIZE)
    assert hf2.get(rid) == b"bbbbb"


def test_update_relocate_new_rid(tmp_path):
    # Arrange: different byte length -> relocate, new RID
    hf, _ = _new_heap(tmp_path)
    rid = hf.insert(b"short")
    # Act: longer record forces relocation
    new_rid = hf.update(rid, b"a-longer-record")
    # Assert: different RID
    assert new_rid != rid
    # new RID returns new bytes
    assert hf.get(new_rid) == b"a-longer-record"
    # old RID is tombstoned
    with pytest.raises(RecordNotFoundError):
        hf.get(rid)


def test_update_relocate_durable(tmp_path):
    # Arrange
    hf, path = _new_heap(tmp_path)
    rid = hf.insert(b"x")
    new_rid = hf.update(rid, b"much-longer-bytes-here")
    # Act: reopen
    hf2 = HeapFile(path, DEFAULT_PAGE_SIZE)
    assert hf2.get(new_rid) == b"much-longer-bytes-here"
    with pytest.raises(RecordNotFoundError):
        hf2.get(rid)


def test_update_missing_rid_raises(tmp_path):
    # Non-existent page_id raises before any write
    hf, _ = _new_heap(tmp_path)
    hf.insert(b"something")
    with pytest.raises(RecordNotFoundError):
        hf.update(RID(99, 0), b"new-data")


def test_update_dead_rid_raises(tmp_path):
    # Tombstoned slot raises before any write
    hf, _ = _new_heap(tmp_path)
    rid = hf.insert(b"alive")
    hf.delete(rid)
    with pytest.raises(RecordNotFoundError):
        hf.update(rid, b"alive")
