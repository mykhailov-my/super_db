from collections.abc import Sequence
from typing import Protocol

from superdb.schema import TableMeta


class Renderer(Protocol):
    # --- Phase 1 (unchanged) ---
    def render_message(self, msg: str) -> None: ...
    def render_error(self, msg: str) -> None: ...

    # --- v4.0: query results, decoupled from any single table's schema ---
    def render_result(
        self,
        columns: Sequence[str],
        rows: Sequence[dict],
    ) -> None: ...

    # --- Phase 8 additions ---
    def render_rows(
        self,
        meta: TableMeta,
        rows: Sequence[tuple[str, dict]],   # (rid_str, {col_name: value})
    ) -> None: ...

    def render_schema(
        self,
        meta: TableMeta,
    ) -> None: ...

    def render_page(
        self,
        table_name: str,
        page_id: int,
        page_size: int,
        header_bytes: int,
        slot_count: int,
        slots: Sequence[tuple[int, int, int, bool]],  # (slot_id, offset, length, is_live)
        free_space_start: int,
        free_space_end: int,
    ) -> None: ...

    def render_hexdump(
        self,
        rid_str: str,
        raw_bytes: bytes,
        field_spans: Sequence[tuple[str, int, int, str]],  # (name, offset, length, type_label)
        null_bitmap_span: tuple[int, int] | None,          # (offset, length) or None
    ) -> None: ...

    def render_btree(
        self,
        table_name: str,
        index_column: str,
        nodes: list,   # opaque recursive structure emitted by BPlusTree; renderer walks it
    ) -> None: ...

    def render_table_list(
        self,
        entries: Sequence[tuple[str, str]],   # (name, table_id)
    ) -> None: ...
