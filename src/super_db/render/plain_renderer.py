import sys
from typing import Sequence

from super_db.catalog.schema import TableMeta


SLOT_ENTRY_SIZE = 6  # mirrors page_layout.SLOT_ENTRY_SIZE; no import to keep this stdlib-only


class PlainRenderer:
    def render_message(self, msg: str) -> None:
        print(msg)

    def render_error(self, msg: str) -> None:
        print(f"Error: {msg}", file=sys.stderr)

    def render_rows(
        self,
        meta: TableMeta,
        rows: Sequence[tuple[str, dict]],
    ) -> None:
        if not rows:
            print("no rows")
            return
        header = "RID\t" + "\t".join(c.name for c in meta.columns)
        print(header)
        for rid_str, record in rows:
            values = "\t".join(
                "NULL" if record[c.name] is None else str(record[c.name])
                for c in meta.columns
            )
            print(f"{rid_str}\t{values}")

    def render_schema(self, meta: TableMeta) -> None:
        print(f"table: {meta.name}  track={meta.storage_track.value}  page_size={meta.page_size}")
        for col in meta.columns:
            null_str = "NULL" if col.nullable else "NOT NULL"
            print(f"  {col.name}: {col.col_type.value} {null_str}")

    def render_table_list(self, entries: Sequence[tuple[str, str]]) -> None:
        for name, table_id in entries:
            print(f"{name} (id {table_id})")

    def render_page(
        self,
        table_name: str,
        page_id: int,
        page_size: int,
        header_bytes: int,
        slot_count: int,
        slots: Sequence[tuple[int, int, int, bool]],
        free_space_start: int,
        free_space_end: int,
    ) -> None:
        print(f"Header\t[0, {header_bytes})\t{header_bytes}B\tpage_id, slot_count, free_start...")
        for slot_id, offset, length, is_live in slots:
            status = "live" if is_live else "dead"
            notes = f"offset={offset} length={length}" if is_live else "TOMBSTONE"
            slot_start = header_bytes + slot_id * SLOT_ENTRY_SIZE
            print(
                f"Slot {slot_id} ({status})\t"
                f"[{slot_start}, {slot_start + SLOT_ENTRY_SIZE})\t"
                f"6B\t"
                f"{notes}"
            )
        print(
            f"Free space\t"
            f"[{free_space_start}, {free_space_end})\t"
            f"{free_space_end - free_space_start}B\t"
        )

    def render_hexdump(
        self,
        rid_str: str,
        raw_bytes: bytes,
        field_spans: Sequence[tuple[str, int, int, str]],
        null_bitmap_span: tuple[int, int] | None,
    ) -> None:
        for row_start in range(0, len(raw_bytes), 16):
            chunk = raw_bytes[row_start:row_start + 16]
            hex_part = " ".join(f"{b:02x}" for b in chunk)
            ascii_part = "".join(chr(b) if 32 <= b < 127 else "·" for b in chunk)
            print(f"0x{row_start:04X}  {hex_part:<47}  {ascii_part}")
        for name, offset, length, type_label in field_spans:
            print(f"field  {name}  {type_label}  offset={offset}  length={length}")
        if null_bitmap_span:
            print(f"null_bitmap  —  offset={null_bitmap_span[0]}  length={null_bitmap_span[1]}")

    def render_btree(
        self,
        table_name: str,
        index_column: str,
        nodes: list,
    ) -> None:
        def _print_node(node: dict, indent: int) -> None:
            prefix = "  " * indent
            if node["type"] == "internal":
                print(f"{prefix}[internal] keys={node['keys']}")
            else:
                print(f"{prefix}[leaf] keys={node['keys']} rids={node['rids']}")
                if node.get("next_leaf") is not None:
                    print(f"{prefix}  → next_leaf: {node['next_leaf']}")
            for child in node.get("children", []):
                _print_node(child, indent + 1)

        for node in nodes:
            _print_node(node, 0)
