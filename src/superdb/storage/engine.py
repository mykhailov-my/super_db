"""StorageEngine: public facade composing Catalog + HeapFile.

Records cross this boundary as plain dict (column name -> Python value).
The CLI and later phases (scan, update, delete, B+Tree) call this class.

dict is the public boundary. encode_tuple/decode_tuple convert
between dict and bytes; HeapFile stays schema-agnostic.

Open-per-op: every method re-reads the catalog and re-opens the
heap, so a fresh StorageEngine(db_dir) is naturally restart-safe.

StorageEngine is the composition root for the B+Tree index.
The index is a sibling of HeapFile — heap_file.py never imports the index.
"""
from pathlib import Path

from superdb.catalog.catalog import Catalog, TableHandle
from superdb.catalog.catalog import scan as _scan
from superdb.catalog.schema import Column, ColumnType, TableMeta
from superdb.constants import DEFAULT_PAGE_SIZE
from superdb.errors import StorageError
from superdb.index.bplustree import BPlusTree
from superdb.index.node_layout import KEY_TYPE_INT, KEY_TYPE_TEXT
from superdb.storage.heap_file import HeapFile
from superdb.storage.rid import RID
from superdb.storage.row import Row
from superdb.storage.tuple_codec import decode_tuple, encode_tuple


class StorageEngine:
    """Public facade composing Catalog + HeapFile.

    Records cross this boundary as plain dict (column name -> Python value).
    The CLI and later phases (scan, update, delete, B+Tree) call this class.
    Open-per-op: every method re-reads the catalog and re-opens the heap,
    so a fresh StorageEngine(db_dir) is naturally restart-safe.

    Index state: the engine holds an optional BPlusTree and the name of
    the indexed key column. build_index() populates the tree from the heap;
    insert() maintains it. update/delete do NOT maintain the index this phase
    (see comments in those methods).
    """

    def __init__(self, db_dir: Path) -> None:
        self._db_dir = db_dir
        self._catalog = Catalog(db_dir)
        self._index: BPlusTree | None = None
        self._index_keycol: str | None = None

    # ---- internal helpers ----

    @staticmethod
    def _heap(handle: TableHandle) -> HeapFile:
        return HeapFile(handle.heap_path, handle.meta.page_size)

    @staticmethod
    def _encode_record(columns: list[Column], record: dict) -> bytes:
        """Validate that every column is present, then encode the record to bytes."""
        missing = [c.name for c in columns if c.name not in record]
        if missing:
            raise StorageError(f"record missing columns: {', '.join(missing)}")
        return encode_tuple(columns, [record[c.name] for c in columns])

    # ---- catalog delegation (thin wrappers) ----

    def create_table(
        self,
        name: str,
        columns: list[tuple[str, str, bool]],
        page_size: int = DEFAULT_PAGE_SIZE,
    ) -> TableMeta:
        return self._catalog.create_table(name, columns, page_size)

    def open_table(self, name: str) -> TableHandle:
        return self._catalog.open_table(name)

    def list_tables(self) -> list[TableMeta]:
        return self._catalog.list_tables()

    def describe_table(self, name: str) -> TableMeta:
        return self._catalog.describe_table(name)

    def drop_table(self, name: str) -> None:
        self._catalog.drop_table(name)

    # ---- data operations (dict <-> bytes bridge) ----

    def insert(self, table: str, record: dict) -> RID:
        """Encode record and append to the table's heap. Returns RID.

        If an index is attached (via build_index), the new key is inserted into
        the index so subsequent searches find it. With no index attached, behavior
        is identical to before (index is optional).
        """
        handle = self.open_table(table)
        cols = list(handle.meta.columns)
        raw = self._encode_record(cols, record)
        rid = self._heap(handle).insert(raw)
        if self._index is not None and self._index_keycol is not None:
            self._index.insert(record[self._index_keycol], rid)
        return rid

    def get(self, table: str, rid: RID) -> dict:
        """Return the record at rid as a dict. Raises RecordNotFoundError if not found."""
        handle = self.open_table(table)
        cols = list(handle.meta.columns)
        raw = self._heap(handle).get(rid)
        values = decode_tuple(raw, cols)
        return {c.name: v for c, v in zip(cols, values, strict=True)}

    def scan(self, table: str) -> list[Row]:
        """Return all live records in the table's heap as a list[Row]."""
        return _scan(self.open_table(table))

    def update(self, table: str, rid: RID, record: dict) -> RID:
        """Encode record and update rid in-place or relocate. Returns RID (new if relocated).

        Index not maintained on update/delete this milestone (Phase 8 TODO).
        """
        handle = self.open_table(table)
        cols = list(handle.meta.columns)
        raw = self._encode_record(cols, record)
        return self._heap(handle).update(rid, raw)

    def delete(self, table: str, rid: RID) -> None:
        """Tombstone the record at rid. Raises RecordNotFoundError if not live.

        Index not maintained on update/delete this milestone (Phase 8 TODO).
        """
        handle = self.open_table(table)
        self._heap(handle).delete(rid)

    # ---- index operations (composition root) ----

    def build_index(self, table: str, keycol: str) -> None:
        """Build a B+Tree over keycol for table. Scans the heap to populate;
        subsequent StorageEngine.insert calls maintain it. No update/delete maintenance
        this phase. Index lives at db_dir/{table}.idx, a sibling of the .tbl heap.

        Rebuilding replaces any existing index for the table. Raises StorageError if
        keycol is not a column of table.
        """
        handle = self.open_table(table)
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
