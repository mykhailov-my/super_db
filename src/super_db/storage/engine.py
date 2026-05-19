"""StorageEngine: public facade composing Catalog + HeapFile.

Records cross this boundary as plain dict (column name -> Python value).
The CLI and later phases (scan, update, delete, B+Tree) call this class.

D-02: dict is the public boundary. encode_tuple/decode_tuple convert
between dict and bytes; HeapFile stays schema-agnostic.

Open-per-op (D-07): every method re-reads the catalog and re-opens the
heap, so a fresh StorageEngine(db_dir) is naturally restart-safe.
"""
from pathlib import Path

from super_db.catalog.catalog import (
    TableHandle,
    create_table,
    describe_table,
    drop_table,
    list_tables,
    open_table,
)
from super_db.catalog.schema import TableMeta
from super_db.common.constants import DEFAULT_PAGE_SIZE
from super_db.common.errors import StorageError
from super_db.storage.heap_file import HeapFile
from super_db.storage.rid import RID
from super_db.storage.tuple_codec import decode_tuple, encode_tuple


class StorageEngine:
    """Public facade composing Catalog + HeapFile.

    Records cross this boundary as plain dict (column name -> Python value).
    The CLI and later phases (scan, update, delete, B+Tree) call this class.
    Open-per-op (D-07): every method re-reads the catalog and re-opens the heap,
    so a fresh StorageEngine(db_dir) is naturally restart-safe.
    """

    def __init__(self, db_dir: Path) -> None:
        self._db_dir = db_dir

    # ---- catalog delegation (thin wrappers) ----

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

    # ---- data operations (dict <-> bytes bridge) ----

    def insert(self, table: str, record: dict) -> RID:
        """Encode record and append to the table's heap. Returns RID."""
        handle = open_table(self._db_dir, table)
        cols = list(handle.meta.columns)
        missing = [c.name for c in cols if c.name not in record]
        if missing:
            raise StorageError(f"record missing columns: {', '.join(missing)}")
        values = [record[c.name] for c in cols]
        raw = encode_tuple(cols, values)
        return HeapFile(handle.heap_path, handle.meta.page_size).insert(raw)

    def get(self, table: str, rid: RID) -> dict:
        """Return the record at rid as a dict. Raises RecordNotFoundError if not found."""
        handle = open_table(self._db_dir, table)
        cols = list(handle.meta.columns)
        raw = HeapFile(handle.heap_path, handle.meta.page_size).get(rid)
        values = decode_tuple(raw, cols)
        return {c.name: v for c, v in zip(cols, values)}
