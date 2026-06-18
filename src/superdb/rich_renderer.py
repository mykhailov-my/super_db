from collections.abc import Sequence

from rich import box as rich_box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.tree import Tree

from superdb.page_layout import SLOT_ENTRY_SIZE
from superdb.schema import TableMeta

FIELD_COLORS = [
    "green",
    "yellow",
    "blue",
    "magenta",
    "red",
    "bright_green",
    "bright_yellow",
    "bright_blue",
    "bright_magenta",
]


class RichRenderer:
    def __init__(self) -> None:
        self._console = Console()
        self._err_console = Console(stderr=True, width=200)

    def render_message(self, msg: str) -> None:
        # markup=False: messages are plain text; brackets like "[id]" in a
        # printed AST must not be parsed as Rich style tags.
        self._console.print(msg, markup=False)

    def render_error(self, msg: str) -> None:
        # markup=False: the message may contain brackets (e.g. a bad column spec
        # "id[/]INT") that must not be parsed as Rich tags and crash the error path.
        self._err_console.print("Error:", msg, style="red", markup=False)

    def render_result(self, columns: Sequence[str], rows: Sequence[dict]) -> None:
        tbl = Table(show_header=True, border_style=None, box=rich_box.SIMPLE_HEAD)
        for col in columns:
            tbl.add_column(col)
        for row in rows:
            tbl.add_row(*(
                Text("NULL", style="italic dim") if row[c] is None else Text(str(row[c]))
                for c in columns
            ))
        self._console.print(tbl)
        self._console.print(f"({len(rows)} row{'s' if len(rows) != 1 else ''})")

    def render_rows(
        self,
        meta: TableMeta,
        rows: Sequence[tuple[str, dict]],
    ) -> None:
        if not rows:
            self._console.print("no rows")
            return
        tbl = Table(show_header=True, border_style=None, box=rich_box.SIMPLE_HEAD)
        tbl.add_column("RID", style="cyan")
        for col in meta.columns:
            tbl.add_column(col.name)
        for rid_str, record in rows:
            cells: list = [rid_str]
            for col in meta.columns:
                v = record[col.name]
                cells.append(Text("NULL", style="italic dim") if v is None else Text(str(v)))
            tbl.add_row(*cells)
        self._console.print(tbl)

    def render_schema(self, meta: TableMeta) -> None:
        self._console.print(
            f"Table: {meta.name}  track={meta.storage_track.value}  page_size={meta.page_size}"
        )
        tbl = Table(show_header=True, border_style=None, box=rich_box.SIMPLE_HEAD)
        tbl.add_column("Column", style="bold")
        tbl.add_column("Type", style="bold")
        tbl.add_column("Nullable", style="bold")
        for col in meta.columns:
            tbl.add_row(col.name, col.col_type.value, "Yes" if col.nullable else "No")
        self._console.print(tbl)

    def render_table_list(self, entries: Sequence[tuple[str, str]]) -> None:
        tbl = Table(show_header=True, border_style=None, box=rich_box.SIMPLE_HEAD)
        tbl.add_column("Name", style="bold")
        tbl.add_column("Table ID", style="bold")
        for name, table_id in entries:
            tbl.add_row(name, table_id)
        self._console.print(tbl)

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
        tbl = Table(show_header=True, box=rich_box.ROUNDED, border_style=None)
        tbl.add_column("Section")
        tbl.add_column("Byte Range")
        tbl.add_column("Size")
        tbl.add_column("Notes")

        # Header row
        tbl.add_row(
            "Header",
            f"[0, {header_bytes})",
            f"{header_bytes} B",
            "page_id, slot_count, free_start...",
        )

        # Slot rows
        for slot_id, offset, length, is_live in slots:
            style = "green" if is_live else "dim red"
            status = "live" if is_live else "dead"
            notes = "TOMBSTONE" if not is_live else f"offset={offset} length={length}"
            slot_start = header_bytes + slot_id * SLOT_ENTRY_SIZE
            tbl.add_row(
                Text(f"Slot {slot_id} ({status})", style=style),
                f"[{slot_start}, {slot_start + SLOT_ENTRY_SIZE})",
                f"{SLOT_ENTRY_SIZE} B",
                notes,
            )

        # Free space row
        free_size = free_space_end - free_space_start
        tbl.add_row(
            Text("Free space", style="dim"),
            f"[{free_space_start}, {free_space_end})",
            f"{free_size} B",
            "",
        )

        # Tuple rows (highest offset first)
        live_slots = [(sid, off, ln) for sid, off, ln, live in slots if live]
        for sid, off, ln in sorted(live_slots, key=lambda x: x[1], reverse=True):
            tbl.add_row(f"Tuple (slot {sid})", f"[{off}, {off + ln})", f"{ln} B", "")

        panel_title = f"Page {page_id} · table '{table_name}' · page_size={page_size}"
        self._console.print(Panel(tbl, title=panel_title, border_style="dim"))

    def render_hexdump(
        self,
        rid_str: str,
        raw_bytes: bytes,
        field_spans: Sequence[tuple[str, int, int, str]],
        null_bitmap_span: tuple[int, int] | None,
    ) -> None:
        # Build per-byte color map: null_bitmap first (lower priority), then fields override
        color_map: dict[int, str] = {}
        if null_bitmap_span:
            bm_start, bm_len = null_bitmap_span
            for i in range(bm_start, bm_start + bm_len):
                color_map[i] = "white on dark_orange3"
        for idx, (_name, offset, length, _type_label) in enumerate(field_spans):
            color = FIELD_COLORS[idx % len(FIELD_COLORS)]
            for i in range(offset, offset + length):
                color_map[i] = color

        lines: list[Text] = []

        # Header row label
        header_line = Text()
        header_line.append("Offset    ", style="dim")
        header_line.append("00 01 02 03 04 05 06 07  08 09 0a 0b 0c 0d 0e 0f")
        header_line.append("   ASCII")
        lines.append(header_line)

        # Hex rows
        for row_start in range(0, len(raw_bytes), 16):
            chunk = raw_bytes[row_start:row_start + 16]
            line = Text()
            line.append(f"0x{row_start:04X}   ", style="dim")

            for group_start in (0, 8):
                group = chunk[group_start:group_start + 8]
                for local_i, b in enumerate(group):
                    abs_i = row_start + group_start + local_i
                    color = color_map.get(abs_i, "")
                    line.append(f"{b:02x}", style=color)
                    line.append(" ")
                if group_start == 0:
                    line.append(" ")  # extra space between groups
            # Pad short last row
            full_count = len(chunk)
            if full_count < 16:
                missing = 16 - full_count
                padding = "   " * missing
                if full_count <= 8:
                    padding += " "  # account for group separator
                line.append(padding)

            line.append("  ")
            # ASCII gutter
            for local_i, b in enumerate(chunk):
                abs_i = row_start + local_i
                color = color_map.get(abs_i, "")
                ch = chr(b) if 32 <= b < 127 else "·"
                line.append(ch, style=color)

            lines.append(line)

        # Blank line before legend
        lines.append(Text(""))
        lines.append(Text("Legend", style="bold"))
        lines.append(Text("─────", style="dim"))

        # Legend: fields
        for idx, (name, offset, length, type_label) in enumerate(field_spans):
            color = FIELD_COLORS[idx % len(FIELD_COLORS)]
            leg = Text()
            leg.append("■ ", style=color)
            leg.append(f"{color:<20} {name:<12} {type_label:<8} offset={offset}   length={length}")
            lines.append(leg)

        # Legend: null bitmap
        if null_bitmap_span:
            bm_start, bm_len = null_bitmap_span
            leg = Text()
            leg.append("■ ", style="white on dark_orange3")
            leg.append(
                f"{'dark_orange3':<20} {'null_bitmap':<12} {'—':<8} "
                f"offset={bm_start}   length={bm_len}"
            )
            lines.append(leg)

        # Combine into a single Text block
        content = Text()
        for i, line in enumerate(lines):
            content.append_text(line)
            if i < len(lines) - 1:
                content.append("\n")

        panel_title = f"Hex dump · RID {rid_str}"
        self._console.print(Panel(content, title=panel_title, border_style="dim"))

    def render_btree(
        self,
        table_name: str,
        index_column: str,
        nodes: list,
    ) -> None:
        root_label = f"B+Tree · table '{table_name}' · index on '{index_column}'"
        tree = Tree(root_label)

        def _add_node(parent: Tree, node: dict) -> None:
            if node["type"] == "internal":
                label = Text(f"[internal] keys={node['keys']}", style="bold cyan")
            else:
                label = Text(
                    f"[leaf] keys={node['keys']} rids={node['rids']}",
                    style="bold green",
                )
            branch = parent.add(label)
            for child in node.get("children", []):
                _add_node(branch, child)
            if node.get("next_leaf") is not None:
                branch.add(Text(f"→ next_leaf: {node['next_leaf']}", style="dim"))

        for node in nodes:
            _add_node(tree, node)

        self._console.print(tree)
