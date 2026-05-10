from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True, frozen=True)
class RID:
    """Row identifier: stable reference to a record in a heap file.

    page_id  – zero-based index of the page within the table's heap file.
    slot_id  – index of the slot directory entry within that page.

    RIDs are stable under tombstone delete: deleting a record marks its slot
    as tombstoned in-place; no slots are shifted or removed. The RID of any
    live record never changes unless an update relocates the record (Phase 6),
    in which case a new RID is returned.
    """

    page_id: int
    slot_id: int
