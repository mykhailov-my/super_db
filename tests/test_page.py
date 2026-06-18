"""Tests for storage/page.py — slotted-page byte layout (PHYS-01, PHYS-03, PHYS-04)."""

import pytest

from superdb.constants import FORMAT_VERSION
from superdb.errors import PageFullError, StorageError
from superdb.page import Page
from superdb.page_layout import HEADER_SIZE, PAGE_HDR, SLOT, SLOT_ENTRY_SIZE


def test_header_roundtrip():
    # Arrange
    p = Page.new(4096)
    # Act
    hdr = PAGE_HDR.unpack(p.to_bytes()[0:8])
    # Assert
    assert hdr == (FORMAT_VERSION, 0, 8, 4096)


def test_can_fit_accounts_for_slot_entry():
    # A fresh 4096 page has 4088 free bytes.
    # can_fit(T) := free >= T + SLOT_ENTRY_SIZE
    # max T = 4088 - 6 = 4082
    p = Page.new(4096)
    assert p.can_fit(4082)
    assert not p.can_fit(4083)


def test_slot_directory_grows():
    # Arrange
    p = Page.new(4096)
    r1 = b"hello"  # 5 bytes
    r2 = b"world!"  # 6 bytes
    # Act
    p.insert_tuple(r1)
    p.insert_tuple(r2)
    # Assert
    assert p.slot_count == 2
    assert p.free_start == HEADER_SIZE + 2 * SLOT_ENTRY_SIZE  # 8 + 12 = 20
    assert p.free_end == 4096 - 5 - 6  # 4085


def test_no_overlap_after_multiple_inserts():
    p = Page.new(4096)
    for i in range(10):
        p.insert_tuple(bytes([i] * (i + 1)))
    assert p.free_start < p.free_end
    # slot directory ends at free_start; tuples start at free_end — no overlap
    slot_dir_end = HEADER_SIZE + p.slot_count * SLOT_ENTRY_SIZE
    assert slot_dir_end == p.free_start
    assert p.free_end > slot_dir_end


def test_tombstone_slot():
    # Arrange
    p = Page.new(4096)
    data = b"keep-me"
    p.insert_tuple(data)
    # Act
    p.tombstone_slot(0)
    # Assert
    assert not p.is_live(0)
    assert p.get_tuple(0) == data  # tuple bytes untouched


def test_slot_flags():
    p = Page.new(4096)
    p.insert_tuple(b"live-record")
    assert p.is_live(0)


def test_nondefault_page_size():
    p = Page.new(8192)
    record = b"fits on big page"
    sid = p.insert_tuple(record)
    assert sid == 0
    assert p.get_tuple(0) == record
    assert len(p.to_bytes()) == 8192


def test_page_size_not_in_header():
    # page_size must not appear as a distinct header field.
    # A fresh 8192-byte page: free_end == 8192 (which is expected), but the
    # other three fields (format_version, slot_count, free_start) must not equal 8192.
    p = Page.new(8192)
    fv, sc, fs, fe = PAGE_HDR.unpack(p.to_bytes()[0:8])
    # free_end == 8192 is correct (that IS page_size, by design of a fresh page)
    assert fe == 8192
    # None of the other fields should be 8192
    assert fv != 8192
    assert sc != 8192
    assert fs != 8192


def test_full_page_byte_layout():
    # Golden-byte test (SC#2): inserting id=1, name='ab' record into fresh 4096 page
    record = bytes.fromhex("000100000002006162")
    p = Page.new(4096)
    p.insert_tuple(record)
    b = p.to_bytes()
    assert b[0:8].hex() == "010001000e00f70f"
    assert b[8:14].hex() == "f70f09000100"
    assert b[4087:4096].hex() == "000100000002006162"


def test_oversize_record_raises_pagefull():
    # A 64-byte page has 64-8-6=50 bytes max record size.
    # Inserting 100 bytes must raise PageFullError.
    with pytest.raises(PageFullError):
        Page.new(64).insert_tuple(b"x" * 100)


def test_malformed_slot_bounds_raises_storageerror():
    # Hand-craft a 256-byte page with slot_count=1 but bogus slot offset/length.
    # The slot points to offset=9000, length=10 which is out of bounds for page_size=256.
    buf = bytearray(256)
    mv = memoryview(buf)
    mv[0:HEADER_SIZE] = PAGE_HDR.pack(FORMAT_VERSION, 1, HEADER_SIZE + SLOT_ENTRY_SIZE, 200)
    mv[HEADER_SIZE : HEADER_SIZE + SLOT_ENTRY_SIZE] = SLOT.pack(9000, 10, 1)
    with pytest.raises(StorageError):
        Page.from_bytes(bytes(buf), 256).get_tuple(0)


def test_from_bytes_wrong_size_raises_storageerror():
    with pytest.raises(StorageError):
        Page.from_bytes(b"too short", 4096)


def test_roundtrip_via_bytes():
    p = Page.new(4096)
    records = [b"alpha", b"beta", b"gamma"]
    for r in records:
        p.insert_tuple(r)
    p2 = Page.from_bytes(p.to_bytes(), 4096)
    for i, r in enumerate(records):
        assert p2.get_tuple(i) == r


def test_live_slots_excludes_tombstoned():
    p = Page.new(4096)
    p.insert_tuple(b"a")
    p.insert_tuple(b"b")
    p.insert_tuple(b"c")
    p.tombstone_slot(1)
    assert p.live_slots() == [0, 2]


def test_page_size_over_u16_rejected():
    # free_end is a u16 header field; a page_size above 65535 must be rejected
    # cleanly rather than raising a raw struct.error.
    with pytest.raises(StorageError):
        Page.new(70000)


def test_overwrite_tuple_same_length():
    # Arrange
    p = Page.new(4096)
    original = b"hello"
    p.insert_tuple(original)
    slot_count_before = p.slot_count
    free_start_before = p.free_start
    free_end_before = p.free_end
    # Act
    p.overwrite_tuple(0, b"world")
    # Assert: tuple bytes updated, slot entry and header unchanged
    assert p.get_tuple(0) == b"world"
    assert p.slot_count == slot_count_before
    assert p.free_start == free_start_before
    assert p.free_end == free_end_before
    assert p.is_live(0)


def test_overwrite_tuple_length_mismatch_raises():
    # Arrange
    p = Page.new(4096)
    p.insert_tuple(b"hello")
    # Act + Assert: shorter record raises StorageError
    with pytest.raises(StorageError):
        p.overwrite_tuple(0, b"hi")
    # Assert: buffer not mutated
    assert p.get_tuple(0) == b"hello"
    # Also: longer record raises StorageError
    with pytest.raises(StorageError):
        p.overwrite_tuple(0, b"hello!!")
    assert p.get_tuple(0) == b"hello"


def test_overwrite_tuple_out_of_range_raises():
    # Arrange
    p = Page.new(4096)
    p.insert_tuple(b"data")
    # Act + Assert: slot_id beyond slot_count raises StorageError (via _slot)
    with pytest.raises(StorageError):
        p.overwrite_tuple(99, b"xxxx")
