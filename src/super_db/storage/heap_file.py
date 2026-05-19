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

    def get(self, rid: RID) -> bytes:
        """Return the record bytes at rid.

        Raises RecordNotFoundError for out-of-range page_id, out-of-range slot_id,
        and tombstoned slots.
        """
        ps = self._page_size
        fd = os.open(str(self._path), os.O_RDONLY)
        try:
            page_count = os.fstat(fd).st_size // ps
            if rid.page_id >= page_count:
                raise RecordNotFoundError(
                    f"page_id {rid.page_id} out of range (page_count={page_count})"
                )
            raw = os.pread(fd, ps, rid.page_id * ps)
            page = Page.from_bytes(raw, ps)
            if rid.slot_id >= page.slot_count:
                raise RecordNotFoundError(
                    f"slot_id {rid.slot_id} out of range (slot_count={page.slot_count})"
                )
            if not page.is_live(rid.slot_id):
                raise RecordNotFoundError(
                    f"RID ({rid.page_id}, {rid.slot_id}) is tombstoned"
                )
            return page.get_tuple(rid.slot_id)
        finally:
            os.close(fd)
