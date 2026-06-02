# super_db Design Notes

Internal design decisions, on-disk formats, and illustrated visualizations for the
storage layer.

---

## Storage Track

super_db uses a **row-store** with a **slotted-page heap** file layout.

Each table has one heap file (`<name>.tbl`) made up of fixed-size pages (default 4096 bytes).
Rows are stored contiguously as byte sequences (tuples) within pages. The position of a row
on disk is identified by a Record ID (RID) — a (page_id, slot_id) pair — that stays stable
for the lifetime of the row.

**Why row-store and not column-store?**

Column-store layouts pack a single column's values across many rows together on the same
page. This is efficient for analytical queries that read a few columns from many rows, but
it complicates point lookups, inserts, and updates because a single row's data is spread
across multiple pages. For a teaching DBMS focused on the storage layer — where the primary
workload is insert/get/scan/update/delete on whole rows — row-store keeps each operation
simple and self-contained.

**Why slotted pages and not PAX?**

PAX (Partition Attributes Across) is a hybrid layout that groups column values within each
page. It reduces I/O for column-scans, but adds a second level of indirection inside every
page and makes the page-layout code significantly more complex. Slotted pages are the
canonical introductory format (CMU 15-445 style), and the code can be read top to bottom
without needing to understand two interleaved layouts at once.

---

## Catalog Format

The catalog is stored as a JSON file at `<db_dir>/catalog.json`. Its top-level shape is:

```json
{
  "version": 1,
  "next_table_id": 2,
  "tables": [
    {
      "table_id": 1,
      "name": "users",
      "columns": [
        {"name": "id",   "type": "INT",  "nullable": false},
        {"name": "name", "type": "TEXT", "nullable": true}
      ],
      "storage_track": "row",
      "page_size": 4096,
      "format_version": 1
    }
  ]
}
```

**Atomic write pattern**

Every catalog write uses a temp-file rename pattern so that readers never see a partially
written file:

1. `tempfile.mkstemp(dir=parent)` — create a sibling temp file on the same filesystem
2. Write JSON to the temp file, then `os.fsync(fd)` — flush to stable storage
3. `os.replace(tmp, catalog_path)` — atomic rename (POSIX guarantees the rename is atomic)
4. `os.fsync(dir_fd)` — flush the directory entry so the rename survives a power failure

A reader opening `catalog.json` sees either the old complete file or the new complete
file, never a torn write.

---

## Table Metadata Format

Each entry in `catalog.json["tables"]` has these six fields:

| Field | Type | Description |
|-------|------|-------------|
| `table_id` | int | Monotonically increasing integer assigned at table creation |
| `name` | str | Table name; must match `^[A-Za-z_][A-Za-z0-9_]*$` |
| `columns` | list | Ordered list of `{name, type, nullable}` column descriptors |
| `storage_track` | str | Always `"row"` for this implementation |
| `page_size` | int | Page size in bytes (default 4096) |
| `format_version` | int | Heap/page format version; used for future compatibility checks |

Column type values are `"INT"` (signed 32-bit) and `"TEXT"` (variable-length UTF-8).

---

## RID Scheme

A Record ID (RID) is a `(page_id, slot_id)` pair encoded as a frozen dataclass:

```python
@dataclass(slots=True, frozen=True)
class RID:
    page_id: int
    slot_id: int
```

String form: `page_id:slot_id` (e.g. `0:3`).

**Stability under tombstone delete**

When a row is deleted, its slot directory entry is tombstoned (flags bit 0 cleared) but
the slot remains in the directory at the same index. The RID of the deleted row is not
reused. A subsequent scan skips tombstoned slots by checking the live flag before reading
the tuple bytes.

**Relocation on update**

If an updated tuple is larger than the original and does not fit in the same slot, the
old slot is tombstoned and the tuple is re-inserted into the next page with available
space. The new RID is returned to the caller. The `row update` command prints
`updated new_rid=P:S` in this case.

---

## Slotted-Page Byte Layout

Every page is exactly `page_size` bytes. The layout within a page is:

```
Byte 0                                              Byte page_size-1
┌────────────────┬──────────────────┬───────────────────────────────┐
│  Header (8 B)  │  Slot dir (↑)    │  Free space   │  Tuples (↓)  │
└────────────────┴──────────────────┴───────────────────────────────┘
```

**Header** — 8 bytes, four u16 little-endian fields (`struct "<HHHH"`):

| Offset | Field | Size | Description |
|--------|-------|------|-------------|
| 0 | `format_version` | 2 B | Page format version (currently 1) |
| 2 | `slot_count` | 2 B | Number of slot directory entries (live + tombstoned) |
| 4 | `free_start` | 2 B | First free byte after the last slot directory entry |
| 6 | `free_end` | 2 B | First byte of the highest tuple (free space ends here) |

**Slot directory** — grows upward from byte 8. Each entry is 6 bytes,
three u16 little-endian fields (`struct "<HHH"`):

| Offset within entry | Field | Size | Description |
|--------------------|-------|------|-------------|
| 0 | `offset` | 2 B | Byte offset of the tuple within the page |
| 2 | `length` | 2 B | Byte length of the tuple |
| 4 | `flags` | 2 B | Bit 0 = live (1) or tombstoned (0) |

**Tuple area** — grows downward from `page_size - 1`. Each new insert
appends the tuple bytes just before `free_end` and decrements `free_end`.

**Free space** occupies bytes `[free_start, free_end)`. A new insert that
requires `n` tuple bytes and one new slot directory entry fits if:
`free_end - free_start >= n + SLOT_ENTRY_SIZE` (6 bytes).

Constants from `storage/page_layout.py`:
- `HEADER_SIZE = 8`
- `SLOT_ENTRY_SIZE = 6`
- `DEFAULT_PAGE_SIZE = 4096`

---

## B+Tree

Each index is stored in a separate file: `<db_dir>/<table_name>.idx`.

**Node types**

- **Leaf nodes** hold `(key, RID)` pairs sorted by key. Each leaf carries a `next_leaf`
  page ID pointing to the right sibling, forming a linked list for range scans.
- **Internal nodes** hold separator keys and child page IDs interleaved:
  `child_0, key_0, child_1, key_1, ..., child_N`.

**Split mechanics**

When a leaf overflows during insert, the entries are split at the midpoint. The right
half becomes a new leaf page. The copy-up key (the first key of the right leaf) is
inserted into the parent internal node. If the parent also overflows, its separator keys
are split and the middle key is pushed up to the grandparent. This propagates until
either a non-full parent is reached or the root itself splits, increasing the tree height.

**File layout**

Page 0 of the `.idx` file is the header page. Its fixed-size section holds:

| Field | Size | Description |
|-------|------|-------------|
| magic (`SUPERIDX`) | 8 B | Identifies the file as a super_db index |
| `format_version` | 2 B | Index format version (currently 1) |
| node type tag | 1 B | `0xFF` for the header page |
| `key_type` | 1 B | `0` = INT, `1` = TEXT |
| `text_key_cap` | 2 B | Maximum UTF-8 bytes for a TEXT key (default 128) |
| `root_page_id` | 4 B | Page ID of the current root node |

After the fixed section, a length-prefixed UTF-8 byte sequence holds the indexed column name.

Node pages start at page 1. The header page is updated (rewritten + fsynced) whenever a
split changes the root page ID, making the root pointer durable before returning.

**Persistence**

Every node write uses `write_page` (pwrite + fsync). The header rewrite is the commit
point for a split: if the process crashes before the header is updated, the old root
remains valid and the orphaned node pages are simply unreachable.

---

## Visualizations

The blocks below were captured by running:

```
python scripts/demo.py --db /tmp/superdb_demo_capture
```

When stdout is not a TTY, Rich emits plain text with no ANSI color codes, making the
output paste-friendly.

### Regenerating the visualizations

If the on-disk format or renderer changes, re-run the capture command above and replace
the blocks below with the new output. Keep the temp directory fresh (delete it first) so
the demo always starts from a clean state.

```
rm -rf /tmp/superdb_demo_capture
python scripts/demo.py --db /tmp/superdb_demo_capture
```

---

### Page byte-layout map (Step 3: after two inserts)

```
=== Step 3: insert ===
Inserted rid1=0:0
Inserted rid2=0:1
╭────────────────── Page 0 · table 'users' · page_size=4096 ───────────────────╮
│ ╭────────────────┬──────────────┬────────┬─────────────────────────────────╮ │
│ │ Section        │ Byte Range   │ Size   │ Notes                           │ │
│ ├────────────────┼──────────────┼────────┼─────────────────────────────────┤ │
│ │ Header         │ [0, 8)       │ 8 B    │ page_id, slot_count,            │ │
│ │                │              │        │ free_start...                   │ │
│ │ Slot 0 (live)  │ [8, 14)      │ 6 B    │ offset=4084 length=12           │ │
│ │ Slot 1 (live)  │ [14, 20)     │ 6 B    │ offset=4074 length=10           │ │
│ │ Free space     │ [20, 4074)   │ 4054 B │                                 │ │
│ │ Tuple (slot 0) │ [4084, 4096) │ 12 B   │                                 │ │
│ │ Tuple (slot 1) │ [4074, 4084) │ 10 B   │                                 │ │
│ ╰────────────────┴──────────────┴────────┴─────────────────────────────────╯ │
╰──────────────────────────────────────────────────────────────────────────────╯
```

The header occupies `[0, 8)`, two slot entries follow at `[8, 14)` and `[14, 20)`,
and the two tuples sit at the top of the page growing downward. The large free-space
region `[20, 4074)` is available for more inserts.

---

### Hex/ASCII dump (Step 6: RID 0:0 — Alice's tuple)

```
=== Step 6: build index + hexdump ===
╭───────────────────────────── Hex dump · RID 0:0 ─────────────────────────────╮
│ Offset    00 01 02 03 04 05 06 07  08 09 0a 0b 0c 0d 0e 0f   ASCII           │
│ 0x0000   00 01 00 00 00 05 00 41  6c 69 63 65               ·······Alice     │
│                                                                              │
│ Legend                                                                       │
│ ─────                                                                        │
│ ■ green                id           INT      offset=1   length=4             │
│ ■ yellow               name         TEXT     offset=5   length=7             │
│ ■ dark_orange3         null_bitmap  —        offset=0   length=1             │
╰──────────────────────────────────────────────────────────────────────────────╯
```

The raw 12-byte tuple for `(id=1, name="Alice")`:
- Byte 0: null bitmap (`0x00` — both columns non-null)
- Bytes 1–4: `id` as signed int32 little-endian (`01 00 00 00` = 1)
- Bytes 5–6: `name` length prefix u16 little-endian (`05 00` = 5)
- Bytes 7–11: `name` UTF-8 payload (`41 6c 69 63 65` = "Alice")

---

### B+Tree visualization (Step 6: after building index on `id`)

```
=== Step 6: build index + hexdump ===
B+Tree · table 'users' · index on 'id'
└── [leaf] keys=[1, 2] rids=['0:0', '0:1']
```

With only two rows the tree is a single leaf node. Both (key, RID) pairs fit within one
page — no split occurs. After Step 9 (delete Alice, update Bob to Bobby at new RID 0:2):

```
=== Step 9: restart + verify ===
 RID  id  name
 0:2  2   Bobby
```

The index still points to the original RIDs inserted at build time; point lookups via
the index are checked against the live slot flag on read.
