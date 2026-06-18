import contextlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from superdb.constants import CATALOG_FILE, DEFAULT_PAGE_SIZE, FORMAT_VERSION
from superdb.durability import write_file_atomic, write_json_atomic
from superdb.errors import StorageError
from superdb.heap_file import HeapFile
from superdb.page import Page
from superdb.page_layout import HEADER_SIZE, SLOT_ENTRY_SIZE
from superdb.rid import RID
from superdb.row import Row
from superdb.schema import Column, ColumnType, StorageTrack, TableMeta
from superdb.tuple_codec import decode_tuple, encode_tuple

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_VALID_TYPES = frozenset(t.value for t in ColumnType)


@dataclass(slots=True, frozen=True)
class TableHandle:
    db_dir: Path
    meta: TableMeta

    @property
    def heap_path(self) -> Path:
        return self.db_dir / f"{self.meta.name}.tbl"


def _load_catalog(db_dir: Path) -> dict:
    p = db_dir / CATALOG_FILE
    if not p.exists():
        return {"version": 1, "next_table_id": 1, "tables": []}
    try:
        cat = json.loads(p.read_text("utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as e:
        raise ValueError(f"{p}: corrupt catalog ({e})") from e
    if not isinstance(cat, dict) or "tables" not in cat or "next_table_id" not in cat:
        raise ValueError(f"{p}: corrupt catalog (missing required keys)")
    return cat


def _save_catalog(db_dir: Path, cat: dict) -> None:
    write_json_atomic(db_dir / CATALOG_FILE, cat)


def _validate_schema(table_name: str, columns: list[tuple[str, str, bool]]) -> None:
    if not _IDENT.match(table_name):
        raise ValueError(f"invalid table name {table_name!r}: must match ^[A-Za-z_][A-Za-z0-9_]*$")
    if not columns:
        raise ValueError("table must have at least one column")
    seen: set[str] = set()
    for col_name, col_type, _ in columns:
        if not _IDENT.match(col_name):
            raise ValueError(f"invalid column name {col_name!r}")
        if col_type.upper() not in _VALID_TYPES:
            raise ValueError(f"unsupported column type {col_type!r}; supported: INT, TEXT")
        key = col_name.lower()
        if key in seen:
            raise ValueError(f"duplicate column name {col_name!r} (case-insensitive)")
        seen.add(key)


def _col_to_dict(col: Column) -> dict:
    return {"name": col.name, "type": col.col_type.value, "nullable": col.nullable}


def _table_to_dict(meta: TableMeta) -> dict:
    return {
        "table_id": meta.table_id,
        "name": meta.name,
        "columns": [_col_to_dict(c) for c in meta.columns],
        "storage_track": meta.storage_track.value,
        "page_size": meta.page_size,
        "format_version": meta.format_version,
    }


def _table_from_dict(d: dict) -> TableMeta:
    try:
        name = d["name"]
        if not _IDENT.match(name):
            raise ValueError(f"invalid table name {name!r}")
        cols = tuple(
            Column(c["name"], ColumnType(c["type"]), c["nullable"])
            for c in d["columns"]
        )
        return TableMeta(
            table_id=d["table_id"],
            name=name,
            columns=cols,
            storage_track=StorageTrack(d["storage_track"]),
            page_size=d["page_size"],
            format_version=d["format_version"],
        )
    except (KeyError, TypeError, ValueError) as e:
        raise ValueError(f"corrupt catalog: malformed table entry ({e})") from e


def create_table(
    db_dir: Path,
    name: str,
    columns: list[tuple[str, str, bool]],
    page_size: int = DEFAULT_PAGE_SIZE,
) -> TableMeta:
    _validate_schema(name, columns)
    if not isinstance(page_size, int) or isinstance(page_size, bool):
        raise ValueError(f"page_size must be an int, got {type(page_size).__name__}")
    if page_size <= HEADER_SIZE + SLOT_ENTRY_SIZE or page_size > 0xFFFF:
        raise ValueError(
            f"page_size {page_size} out of range "
            f"(must be > {HEADER_SIZE + SLOT_ENTRY_SIZE} and <= {0xFFFF})"
        )
    cat = _load_catalog(db_dir)
    if any(t["name"] == name for t in cat["tables"]):
        raise ValueError(f"table {name!r} already exists")

    table_id = cat["next_table_id"]
    cols = tuple(
        Column(col_name, ColumnType(col_type.upper()), nullable)
        for col_name, col_type, nullable in columns
    )
    meta = TableMeta(
        table_id=table_id,
        name=name,
        columns=cols,
        storage_track=StorageTrack.ROW,
        page_size=page_size,
        format_version=FORMAT_VERSION,
    )
    cat["tables"].append(_table_to_dict(meta))
    cat["next_table_id"] = table_id + 1
    # Heap first, durably: a catalog entry pointing at a missing heap is
    # unrecoverable, but an orphan .tbl with no catalog entry is harmlessly
    # truncated on the next create. So commit the heap before the catalog.
    write_file_atomic(db_dir / f"{name}.tbl", b"")
    _save_catalog(db_dir, cat)
    return meta


def open_table(db_dir: Path, name: str) -> TableHandle:
    cat = _load_catalog(db_dir)
    entry = next((t for t in cat["tables"] if t["name"] == name), None)
    if entry is None:
        raise ValueError(f"table {name!r} does not exist")
    return TableHandle(db_dir, _table_from_dict(entry))


def list_tables(db_dir: Path) -> list[TableMeta]:
    return [_table_from_dict(t) for t in _load_catalog(db_dir)["tables"]]


def describe_table(db_dir: Path, name: str) -> TableMeta:
    cat = _load_catalog(db_dir)
    entry = next((t for t in cat["tables"] if t["name"] == name), None)
    if entry is None:
        raise ValueError(f"table {name!r} does not exist")
    return _table_from_dict(entry)


def drop_table(db_dir: Path, name: str) -> None:
    cat = _load_catalog(db_dir)
    if not any(t["name"] == name for t in cat["tables"]):
        raise ValueError(f"table {name!r} does not exist")
    cat["tables"] = [t for t in cat["tables"] if t["name"] != name]
    _save_catalog(db_dir, cat)
    # Remove the heap and any sibling B+Tree index for this table.
    for suffix in (".tbl", ".idx"):
        with contextlib.suppress(OSError):
            (db_dir / f"{name}{suffix}").unlink(missing_ok=True)


def insert(handle: TableHandle, record: dict) -> RID:
    """Insert a record into the table's heap and return its RID."""
    cols = list(handle.meta.columns)
    missing = [c.name for c in cols if c.name not in record]
    if missing:
        raise StorageError(f"record missing columns: {', '.join(missing)}")
    raw = encode_tuple(cols, [record[c.name] for c in cols])
    return HeapFile(handle.heap_path, handle.meta.page_size).insert(raw)


def get(handle: TableHandle, rid: RID) -> dict:
    """Return the live record at rid as a dict."""
    cols = list(handle.meta.columns)
    raw = HeapFile(handle.heap_path, handle.meta.page_size).get(rid)
    values = decode_tuple(raw, cols)
    return {c.name: v for c, v in zip(cols, values, strict=True)}


def scan(handle: TableHandle) -> list[Row]:
    """Return all live records in the heap as a list[Row]. Order is unspecified."""
    cols = list(handle.meta.columns)
    page_size = handle.meta.page_size
    try:
        fd = os.open(str(handle.heap_path), os.O_RDONLY)
    except FileNotFoundError as e:
        raise StorageError(f"heap file not found: {handle.heap_path}") from e
    try:
        size = os.fstat(fd).st_size
        if size % page_size != 0:
            raise StorageError(
                f"heap file size {size} is not a multiple of page_size {page_size}"
            )
        page_count = size // page_size
        rows: list = []
        for page_id in range(page_count):
            raw = os.pread(fd, page_size, page_id * page_size)
            page = Page.from_bytes(raw, page_size)
            for slot_id in page.live_slots():
                record = page.get_tuple(slot_id)
                values_list = decode_tuple(record, cols)
                values = {c.name: v for c, v in zip(cols, values_list, strict=True)}
                rows.append(Row(rid=RID(page_id, slot_id), values=values))
    finally:
        os.close(fd)
    return rows


def update(handle: TableHandle, rid: RID, record: dict) -> RID:
    """Update record at rid in-place or relocate. Returns RID (new if relocated)."""
    cols = list(handle.meta.columns)
    missing = [c.name for c in cols if c.name not in record]
    if missing:
        raise StorageError(f"record missing columns: {', '.join(missing)}")
    raw = encode_tuple(cols, [record[c.name] for c in cols])
    return HeapFile(handle.heap_path, handle.meta.page_size).update(rid, raw)


def delete(handle: TableHandle, rid: RID) -> None:
    """Tombstone the record at rid. Raises RecordNotFoundError if not live."""
    HeapFile(handle.heap_path, handle.meta.page_size).delete(rid)


class Catalog:
    """Table catalog bound to a database directory.

    A thin object over the module-level functions above: it holds the one piece
    of shared state (db_dir) so callers don't thread it through every call. Nothing
    else is cached — every method re-reads catalog.json from disk, so a fresh
    Catalog(db_dir) always reflects the current on-disk state.
    """

    __slots__ = ("_db_dir",)

    def __init__(self, db_dir: Path) -> None:
        self._db_dir = db_dir

    def create_table(
        self,
        name: str,
        columns: list[tuple[str, str, bool]],
        page_size: int = DEFAULT_PAGE_SIZE,
    ) -> TableMeta:
        return create_table(self._db_dir, name, columns, page_size)

    def open_table(self, name: str) -> TableHandle:
        return open_table(self._db_dir, name)

    def list_tables(self) -> list[TableMeta]:
        return list_tables(self._db_dir)

    def describe_table(self, name: str) -> TableMeta:
        return describe_table(self._db_dir, name)

    def drop_table(self, name: str) -> None:
        drop_table(self._db_dir, name)
