"""StorageEngine: public facade composing Catalog + HeapFile.

Records cross this boundary as plain dict (column name -> Python value).
The CLI and later phases (scan, update, delete, B+Tree) call this class.

D-02: dict is the public boundary. encode_tuple/decode_tuple convert
between dict and bytes; HeapFile stays schema-agnostic.

Open-per-op (D-07): every method re-reads the catalog and re-opens the
heap, so a fresh StorageEngine(db_dir) is naturally restart-safe.

D-10: StorageEngine is the composition root for the B+Tree index.
The index is a sibling of HeapFile — heap_file.py never imports the index.
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
from super_db.catalog.catalog import scan as _scan
from super_db.catalog.schema import ColumnType, TableMeta
from super_db.common.constants import DEFAULT_PAGE_SIZE
from super_db.common.errors import StorageError
from super_db.index.bplustree import BPlusTree
from super_db.index.node_layout import KEY_TYPE_INT, KEY_TYPE_TEXT
from super_db.storage.heap_file import HeapFile
from super_db.storage.rid import RID
from super_db.storage.row import Row
from super_db.storage.tuple_codec import decode_tuple, encode_tuple


class StorageEngine:
    """Public facade composing Catalog + HeapFile.

    Records cross this boundary as plain dict (column name -> Python value).
    The CLI and later phases (scan, update, delete, B+Tree) call this class.
    Open-per-op (D-07): every method re-reads the catalog and re-opens the heap,
    so a fresh StorageEngine(db_dir) is naturally restart-safe.

    Index state (D-10): the engine holds an optional BPlusTree and the name of
    the indexed key column. build_index() populates the tree from the heap;
    insert() maintains it. update/delete do NOT maintain the index this phase
    (see comments in those methods).
    """

    def __init__(self, db_dir: Path) -> None:
        self._db_dir = db_dir
        self._index: BPlusTree | None = None
        self._index_keycol: str | None = None

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
        """Encode record and append to the table's heap. Returns RID.

        If an index is attached (via build_index), the new key is inserted into
        the index so subsequent searches find it. With no index attached, behavior
        is identical to before (index is optional).
        """
        handle = open_table(self._db_dir, table)
        cols = list(handle.meta.columns)
        missing = [c.name for c in cols if c.name not in record]
        if missing:
            raise StorageError(f"record missing columns: {', '.join(missing)}")
        values = [record[c.name] for c in cols]
        raw = encode_tuple(cols, values)
        rid = HeapFile(handle.heap_path, handle.meta.page_size).insert(raw)
        if self._index is not None and self._index_keycol is not None:
            self._index.insert(record[self._index_keycol], rid)
        return rid

    def get(self, table: str, rid: RID) -> dict:
        """Return the record at rid as a dict. Raises RecordNotFoundError if not found."""
        handle = open_table(self._db_dir, table)
        cols = list(handle.meta.columns)
        raw = HeapFile(handle.heap_path, handle.meta.page_size).get(rid)
        values = decode_tuple(raw, cols)
        return {c.name: v for c, v in zip(cols, values, strict=True)}

    def scan(self, table: str) -> list[Row]:
        """Return all live records in the table's heap as a list[Row]."""
        return _scan(open_table(self._db_dir, table))

    def update(self, table: str, rid: RID, record: dict) -> RID:
        """Encode record and update rid in-place or relocate. Returns RID (new if relocated).

        D-10: index not maintained on update/delete this milestone (Phase 8 TODO).
        """
        handle = open_table(self._db_dir, table)
        cols = list(handle.meta.columns)
        missing = [c.name for c in cols if c.name not in record]
        if missing:
            raise StorageError(f"record missing columns: {', '.join(missing)}")
        values = [record[c.name] for c in cols]
        raw = encode_tuple(cols, values)
        return HeapFile(handle.heap_path, handle.meta.page_size).update(rid, raw)

    def delete(self, table: str, rid: RID) -> None:
        """Tombstone the record at rid. Raises RecordNotFoundError if not live.

        D-10: index not maintained on update/delete this milestone (Phase 8 TODO).
        """
        handle = open_table(self._db_dir, table)
        HeapFile(handle.heap_path, handle.meta.page_size).delete(rid)

    # ---- index operations (D-10 composition root) ----

    def build_index(self, table: str, keycol: str) -> None:
        """Build a B+Tree over keycol for table (D-10). Scans the heap to populate;
        subsequent StorageEngine.insert calls maintain it. No update/delete maintenance
        this phase. Index lives at db_dir/{table}.idx, a sibling of the .tbl heap.

        Rebuilding replaces any existing index for the table. Raises StorageError if
        keycol is not a column of table.
        """
        handle = open_table(self._db_dir, table)
        col = next((c for c in handle.meta.columns if c.name == keycol), None)
        if col is None:
            raise StorageError(f"column '{keycol}' not found in table '{table}'")
        key_type = KEY_TYPE_INT if col.col_type == ColumnType.INT else KEY_TYPE_TEXT
        idx_path = self._db_dir / f"{handle.meta.name}.idx"
        # Replace any stale index — create() refuses to clobber an existing file.
        idx_path.unlink(missing_ok=True)
        tree = BPlusTree.create(idx_path, handle.meta.page_size, key_type, keycol)
        try:
            for row in self.scan(table):
                tree.insert(row.values[keycol], row.rid)
        except Exception:
            idx_path.unlink(missing_ok=True)
            raise
        self._index = tree
        self._index_keycol = keycol
