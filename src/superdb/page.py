from __future__ import annotations

from superdb.constants import FORMAT_VERSION
from superdb.errors import PageFullError, StorageError
from superdb.page_layout import HEADER_SIZE, PAGE_HDR, SLOT, SLOT_ENTRY_SIZE, SLOT_FLAG_LIVE


class Page:
    """Fixed-size in-memory slotted-page buffer.

    Layout: 8-byte header | slot directory (grows up) | free space | tuples (grows down)
    page_size is NOT stored in the header; the caller always supplies it.
    """

    def __init__(self, buf: bytearray, page_size: int) -> None:
        self._buf = buf
        self.page_size = page_size

    @classmethod
    def new(cls, page_size: int, format_version: int = FORMAT_VERSION) -> Page:
        if not HEADER_SIZE < page_size <= 0xFFFF:
            raise StorageError(f"page_size {page_size} must be in ({HEADER_SIZE}, 65535]")
        buf = bytearray(page_size)
        mv = memoryview(buf)
        mv[0:HEADER_SIZE] = PAGE_HDR.pack(format_version, 0, HEADER_SIZE, page_size)
        return cls(buf, page_size)

    @classmethod
    def from_bytes(cls, data: bytes, page_size: int) -> Page:
        if len(data) != page_size:
            raise StorageError(
                f"expected {page_size} bytes, got {len(data)}"
            )
        return cls(bytearray(data), page_size)

    def to_bytes(self) -> bytes:
        return bytes(self._buf)

    def _header(self) -> tuple[int, int, int, int]:
        return PAGE_HDR.unpack(self._buf[0:HEADER_SIZE])

    @property
    def format_version(self) -> int:
        return self._header()[0]

    @property
    def slot_count(self) -> int:
        return self._header()[1]

    @property
    def free_start(self) -> int:
        return self._header()[2]

    @property
    def free_end(self) -> int:
        return self._header()[3]

    @property
    def free_space(self) -> int:
        return self.free_end - self.free_start

    def can_fit(self, tuple_len: int) -> bool:
        return (self.free_end - self.free_start) >= tuple_len + SLOT_ENTRY_SIZE

    def insert_tuple(self, record: bytes) -> int:
        tuple_len = len(record)
        if not self.can_fit(tuple_len):
            raise PageFullError(f"record {tuple_len}B exceeds max for page_size {self.page_size}")
        mv = memoryview(self._buf)
        format_version, slot_count, free_start, free_end = self._header()
        free_end -= tuple_len
        mv[free_end:free_end + tuple_len] = record
        mv[free_start:free_start + SLOT_ENTRY_SIZE] = SLOT.pack(free_end, tuple_len, SLOT_FLAG_LIVE)
        mv[0:HEADER_SIZE] = PAGE_HDR.pack(
            format_version, slot_count + 1, free_start + SLOT_ENTRY_SIZE, free_end
        )
        return slot_count

    def slot(self, slot_id: int) -> tuple[int, int, int]:
        """Return the slot directory entry as (offset, length, flags)."""
        if not (0 <= slot_id < self.slot_count):
            raise StorageError(f"slot_id {slot_id} out of range (slot_count={self.slot_count})")
        base = HEADER_SIZE + slot_id * SLOT_ENTRY_SIZE
        return SLOT.unpack(self._buf[base:base + SLOT_ENTRY_SIZE])

    _slot = slot

    def get_tuple(self, slot_id: int) -> bytes:
        off, ln, _fl = self._slot(slot_id)
        if off < HEADER_SIZE or off + ln > self.page_size:
            raise StorageError(f"slot {slot_id} offset/length out of bounds")
        return bytes(self._buf[off:off + ln])

    def is_live(self, slot_id: int) -> bool:
        return bool(self._slot(slot_id)[2] & SLOT_FLAG_LIVE)

    def tombstone_slot(self, slot_id: int) -> None:
        off, ln, fl = self._slot(slot_id)
        base = HEADER_SIZE + slot_id * SLOT_ENTRY_SIZE
        mv = memoryview(self._buf)
        mv[base:base + SLOT_ENTRY_SIZE] = SLOT.pack(off, ln, fl & ~SLOT_FLAG_LIVE)

    def overwrite_tuple(self, slot_id: int, record: bytes) -> None:
        """Overwrite a slot's tuple bytes in place. record must be the exact same length.

        Caller must ensure the slot is live (HeapFile gates on is_live); this only
        touches tuple bytes, never the slot's live flag.
        """
        off, ln, _fl = self._slot(slot_id)
        if len(record) != ln:
            raise StorageError(
                f"overwrite_tuple: record length {len(record)} != slot length {ln}"
            )
        if off < HEADER_SIZE or off + ln > self.page_size:
            raise StorageError(f"overwrite_tuple: slot {slot_id} offset out of bounds")
        mv = memoryview(self._buf)
        mv[off:off + ln] = record

    def live_slots(self) -> list[int]:
        result = []
        for sid in range(self.slot_count):
            base = HEADER_SIZE + sid * SLOT_ENTRY_SIZE
            off, ln, fl = SLOT.unpack(self._buf[base:base + SLOT_ENTRY_SIZE])
            if not (fl & SLOT_FLAG_LIVE):
                continue
            if off < HEADER_SIZE or off + ln > self.page_size:
                continue
            result.append(sid)
        return result
