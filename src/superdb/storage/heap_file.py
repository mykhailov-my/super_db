"""Heap file: RID-addressed page I/O for a single table's .tbl file.

Design decisions in effect:
  HeapFile never reads catalog.json — page_size is passed in from TableMeta.
  Full padded page written + fsynced per mutation (via write_page).
  Open-per-op — each method opens, operates, closes its own fd. No fd held in state.
"""
import os
from pathlib import Path

from superdb.durability import write_page
from superdb.errors import PageFullError, RecordNotFoundError, StorageError
from superdb.storage.page import Page
from superdb.storage.rid import RID


class HeapFile:
    """RID-addressed page I/O over a single table's .tbl heap file.

    Full padded page written + fsynced per mutation. page_size is passed
    in from TableMeta; HeapFile never reads catalog.json. Open-per-op:
    each method opens, operates, closes its own fd — no fd held in state.
    """

    __slots__ = ("_path", "_page_size")

    def __init__(self, heap_path: Path, page_size: int) -> None:
        self._path = heap_path
        self._page_size = page_size

    def _page_count(self, fd: int) -> int:
        """Number of whole pages in the heap, rejecting a torn (partial) trailing page."""
        page_size = self._page_size
        size = os.fstat(fd).st_size
        if size % page_size != 0:
            raise StorageError(
                f"heap file size {size} is not a multiple of page_size {page_size}"
            )
        return size // page_size

    def insert(self, record: bytes) -> RID:
        """Append record to the heap, allocating a new page if needed. Returns RID.

        Raises PageFullError if the record is too large to fit on any empty page.
        """
        page_size = self._page_size
        # oversized check before opening any fd to avoid fd leaks on error
        if not Page.new(page_size).can_fit(len(record)):
            raise PageFullError(f"record {len(record)}B cannot fit in any {page_size}B page")

        try:
            fd = os.open(str(self._path), os.O_RDWR)
        except FileNotFoundError as e:
            raise StorageError(f"heap file not found: {self._path}") from e
        try:
            page_count = self._page_count(fd)
            if page_count == 0:
                page_id = 0
                page = Page.new(page_size)
            else:
                page_id = page_count - 1
                raw = os.pread(fd, page_size, page_id * page_size)
                page = Page.from_bytes(raw, page_size)
                if not page.can_fit(len(record)):
                    page_id = page_count  # allocate a fresh page
                    page = Page.new(page_size)
            slot_id = page.insert_tuple(record)
            write_page(fd, page_id, page.to_bytes(), page_size)
            return RID(page_id, slot_id)
        finally:
            os.close(fd)

    def _read_live_page(self, fd: int, rid: RID) -> Page:
        """Read the page holding rid, asserting rid points at a live record.

        Raises RecordNotFoundError for an out-of-range page_id, out-of-range slot_id,
        or a tombstoned slot — the single not-found contract shared by get/update/delete.
        """
        page_size = self._page_size
        page_count = self._page_count(fd)
        if rid.page_id >= page_count:
            raise RecordNotFoundError(
                f"page_id {rid.page_id} out of range (page_count={page_count})"
            )
        page = Page.from_bytes(os.pread(fd, page_size, rid.page_id * page_size), page_size)
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
        try:
            fd = os.open(str(self._path), os.O_RDONLY)
        except FileNotFoundError as e:
            raise StorageError(f"heap file not found: {self._path}") from e
        try:
            return self._read_live_page(fd, rid).get_tuple(rid.slot_id)
        finally:
            os.close(fd)

    def delete(self, rid: RID) -> None:
        """Tombstone the slot at rid durably. Raises RecordNotFoundError if not live."""
        page_size = self._page_size
        try:
            fd = os.open(str(self._path), os.O_RDWR)
        except FileNotFoundError as e:
            raise StorageError(f"heap file not found: {self._path}") from e
        try:
            page = self._read_live_page(fd, rid)
            page.tombstone_slot(rid.slot_id)
            write_page(fd, rid.page_id, page.to_bytes(), page_size)
        finally:
            os.close(fd)

    def update(self, rid: RID, record_bytes: bytes) -> RID:
        """Update the record at rid in place or relocate if the length changes.

        Same byte length: overwrites in place, returns the same RID.
        Different byte length: inserts the new record first, then tombstones the old
        slot (insert before tombstone so a crash leaves a recoverable duplicate,
        never data loss). Returns the new RID.

        Raises RecordNotFoundError if rid does not address a live record.
        """
        page_size = self._page_size
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
                write_page(fd, rid.page_id, page.to_bytes(), page_size)
                return rid
            else:
                # Relocate: insert new record FIRST so a crash between the two
                # fsyncs leaves both copies live (duplicate), not a lost record.
                new_rid = self.insert(record_bytes)
                # Re-read the old page after insert: insert may have written to it
                # (if the old page still had free space), so we need the current state
                # before tombstoning to avoid clobbering newly inserted data.
                raw2 = os.pread(fd, page_size, rid.page_id * page_size)
                page2 = Page.from_bytes(raw2, page_size)
                page2.tombstone_slot(rid.slot_id)
                write_page(fd, rid.page_id, page2.to_bytes(), page_size)
                return new_rid
        finally:
            os.close(fd)
