"""Heap file: RID-addressed page I/O for a single table's .tbl file.

Design decisions in effect:
  D-01: HeapFile never reads catalog.json — page_size is passed in from TableMeta.
  D-06: Full padded page written + fsynced per mutation (via write_page).
  D-07: Open-per-op — each method opens, operates, closes its own fd. No fd held in state.
"""
import os
from pathlib import Path

from ..common.durability import write_page
from ..common.errors import PageFullError, RecordNotFoundError, StorageError
from ..storage.page import Page
from ..storage.rid import RID


class HeapFile:
    """RID-addressed page I/O over a single table's .tbl heap file.

    Full padded page written + fsynced per mutation (D-06). page_size is passed
    in from TableMeta; HeapFile never reads catalog.json (D-01). Open-per-op:
    each method opens, operates, closes its own fd (D-07) — no fd held in state.
    """

    __slots__ = ("_path", "_page_size")

    def __init__(self, heap_path: Path, page_size: int) -> None:
        self._path = heap_path
        self._page_size = page_size

    def insert(self, record: bytes) -> RID:
        """Append record to the heap, allocating a new page if needed. Returns RID.

        Raises PageFullError if the record is too large to fit on any empty page.
        """
        ps = self._page_size
        # D-05: oversized check before opening any fd to avoid fd leaks on error
        if not Page.new(ps).can_fit(len(record)):
            raise PageFullError(f"record {len(record)}B cannot fit in any {ps}B page")

        try:
            fd = os.open(str(self._path), os.O_RDWR)
        except FileNotFoundError as e:
            raise StorageError(f"heap file not found: {self._path}") from e
        try:
            page_count = os.fstat(fd).st_size // ps
            if page_count == 0:
                page_id = 0
                page = Page.new(ps)
            else:
                page_id = page_count - 1
                raw = os.pread(fd, ps, page_id * ps)
                page = Page.from_bytes(raw, ps)
                if not page.can_fit(len(record)):
                    page_id = page_count  # allocate a fresh page
                    page = Page.new(ps)
            slot_id = page.insert_tuple(record)
            write_page(fd, page_id, page.to_bytes(), ps)
            return RID(page_id, slot_id)
        finally:
            os.close(fd)

    def _read_live_page(self, fd: int, rid: RID) -> Page:
        """Read the page holding rid, asserting rid points at a live record.

        Raises RecordNotFoundError for an out-of-range page_id, out-of-range slot_id,
        or a tombstoned slot — the single not-found contract shared by get/update/delete.
        """
        ps = self._page_size
        page_count = os.fstat(fd).st_size // ps
        if rid.page_id >= page_count:
            raise RecordNotFoundError(
                f"page_id {rid.page_id} out of range (page_count={page_count})"
            )
        page = Page.from_bytes(os.pread(fd, ps, rid.page_id * ps), ps)
        if rid.slot_id >= page.slot_count:
            raise RecordNotFoundError(
                f"slot_id {rid.slot_id} out of range (slot_count={page.slot_count})"
            )
        if not page.is_live(rid.slot_id):
            raise RecordNotFoundError(
                f"RID ({rid.page_id}, {rid.slot_id}) is tombstoned"
            )
        return page

    def get(self, rid: RID) -> bytes:
        """Return the record bytes at rid.

        Raises RecordNotFoundError for out-of-range page_id, out-of-range slot_id,
        and tombstoned slots.
        """
        fd = os.open(str(self._path), os.O_RDONLY)
        try:
            return self._read_live_page(fd, rid).get_tuple(rid.slot_id)
        finally:
            os.close(fd)

    def delete(self, rid: RID) -> None:
        """Tombstone the slot at rid durably. Raises RecordNotFoundError if not live."""
        ps = self._page_size
        try:
            fd = os.open(str(self._path), os.O_RDWR)
        except FileNotFoundError as e:
            raise StorageError(f"heap file not found: {self._path}") from e
        try:
            page = self._read_live_page(fd, rid)
            page.tombstone_slot(rid.slot_id)
            write_page(fd, rid.page_id, page.to_bytes(), ps)
        finally:
            os.close(fd)

    def update(self, rid: RID, record_bytes: bytes) -> RID:
        """Update the record at rid in place or relocate if the length changes.

        Same byte length: overwrites in place, returns the same RID.
        Different byte length: inserts the new record first, then tombstones the old
        slot (D-03 — insert before tombstone so a crash leaves a recoverable duplicate,
        never data loss). Returns the new RID.

        Raises RecordNotFoundError if rid does not address a live record.
        """
        ps = self._page_size
        try:
            fd = os.open(str(self._path), os.O_RDWR)
        except FileNotFoundError as e:
            raise StorageError(f"heap file not found: {self._path}") from e
        try:
            page = self._read_live_page(fd, rid)
            old_len = len(page.get_tuple(rid.slot_id))
            if len(record_bytes) == old_len:
                # In-place: overwrite tuple bytes, one durable write, same RID.
                page.overwrite_tuple(rid.slot_id, record_bytes)
                write_page(fd, rid.page_id, page.to_bytes(), ps)
                return rid
            else:
                # Relocate (D-03): insert new record FIRST so a crash between the two
                # fsyncs leaves both copies live (duplicate), not a lost record.
                new_rid = self.insert(record_bytes)
                # Re-read the old page after insert: insert may have written to it
                # (if the old page still had free space), so we need the current state
                # before tombstoning to avoid clobbering newly inserted data.
                raw2 = os.pread(fd, ps, rid.page_id * ps)
                page2 = Page.from_bytes(raw2, ps)
                page2.tombstone_slot(rid.slot_id)
                write_page(fd, rid.page_id, page2.to_bytes(), ps)
                return new_rid
        finally:
            os.close(fd)
