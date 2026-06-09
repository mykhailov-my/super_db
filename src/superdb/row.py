from __future__ import annotations

from dataclasses import dataclass

from superdb.rid import RID


@dataclass(slots=True, frozen=True)
class Row:
    """A live record returned by scan(): its RID and decoded column values."""

    rid: RID
    values: dict[str, object]

    @property
    def page_id(self) -> int:
        return self.rid.page_id
