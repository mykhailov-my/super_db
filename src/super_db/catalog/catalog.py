import json
import re
from dataclasses import dataclass
from pathlib import Path

from super_db.catalog.schema import Column, ColumnType, StorageTrack, TableMeta
from super_db.common.constants import CATALOG_FILE, DEFAULT_PAGE_SIZE, FORMAT_VERSION
from super_db.common.durability import write_json_atomic
from super_db.storage.rid import RID

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
    cat = _load_catalog(db_dir)
    if any(t["name"] == name for t in cat["tables"]):
        raise ValueError(f"table {name!r} already exists")

    table_id = cat["next_table_id"]
    heap = db_dir / f"{name}.tbl"
    heap.write_bytes(b"")  # create or truncate orphan

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
    try:
        (db_dir / f"{name}.tbl").unlink(missing_ok=True)
    except OSError:
        pass


def insert(handle: TableHandle, record: dict) -> RID:
    """Insert a record into the table's heap and return its RID.

    Not implemented in Phase 2. Phase 4 implements this via the slotted-page
    heap file. record is a dict mapping column names to Python values.
    """
    raise NotImplementedError("insert is implemented in Phase 4")


def get(handle: TableHandle, rid: RID) -> dict:
    """Return the record at rid. Phase 4 implementation."""
    raise NotImplementedError("get is implemented in Phase 4")


def scan(handle: TableHandle):
    """Yield all live records. Phase 5 implementation."""
    raise NotImplementedError("scan is implemented in Phase 5")


def update(handle: TableHandle, rid: RID, record: dict) -> RID:
    """Update record at rid. Returns new RID if relocation occurred. Phase 6."""
    raise NotImplementedError("update is implemented in Phase 6")


def delete(handle: TableHandle, rid: RID) -> None:
    """Tombstone the record at rid. Phase 6 implementation."""
    raise NotImplementedError("delete is implemented in Phase 6")
