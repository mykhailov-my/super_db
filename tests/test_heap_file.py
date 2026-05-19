"""Tests for storage/heap_file.py — HeapFile insert/get (WRITE-01, WRITE-02, WRITE-03)."""
import os

import pytest

from super_db.common.constants import DEFAULT_PAGE_SIZE
from super_db.common.errors import PageFullError, RecordNotFoundError
from super_db.storage.heap_file import HeapFile
from super_db.storage.page import Page
from super_db.storage.page_layout import HEADER_SIZE, SLOT_ENTRY_SIZE
from super_db.storage.rid import RID


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
