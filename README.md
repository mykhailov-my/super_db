# super_db

An educational single-node DBMS written in Python from scratch, covering the complete
storage layer: a persistent catalog, a slotted-page row-store heap engine with durable
writes, sequential scan, mutations, B+Tree indexing, and a graphical CLI that visualizes
on-disk structures.

---

## Build

Requires Python 3.12+ and Poetry 2.x.

```
poetry install
```

The console script `db-cli` is installed automatically.

---

## Quickstart

```
# 1. Create a new database
db-cli --db ./mydb db init

# 2. Create a table
db-cli --db ./mydb table create --table users --columns "id:INT,name:TEXT"

# 3. Insert rows
db-cli --db ./mydb row insert --table users --values "1,Alice"
db-cli --db ./mydb row insert --table users --values "2,Bob"

# 4. Scan all rows
db-cli --db ./mydb row scan --table users
```

Each command exits 0 on success, 1 on error. Error messages go to stderr.

---

## CLI Usage

Global flags:

```
db-cli [--db PATH] [--debug] [--verbose] <noun> <verb> [options]
```

`--db PATH` is required by every command except `--version` and `--help`.

### db

| Verb   | Flags  | Description |
|--------|--------|-------------|
| `init` | (none) | Create the database directory and write an empty catalog |

```
db-cli --db PATH db init
```

### table

| Verb       | Flags | Description |
|------------|-------|-------------|
| `create`   | `--table NAME --columns SPEC [--page-size N]` | Create a new table. SPEC is `col:TYPE,...` (types: INT, TEXT). Columns are nullable by default. |
| `list`     | (none) | List all tables in the database |
| `describe` | `--table NAME` | Show a table's schema |
| `drop`     | `--table NAME` | Drop a table and its heap file and index file |

```
db-cli --db PATH table create --table users --columns "id:INT,name:TEXT"
db-cli --db PATH table list
db-cli --db PATH table describe --table users
db-cli --db PATH table drop --table users
```

### row

| Verb      | Flags | Description |
|-----------|-------|-------------|
| `insert`  | `--table NAME --values "f1,f2,..."` | Insert a row; prints `inserted rid=P:S` |
| `get`     | `--table NAME --rid P:S` | Fetch one live row by its record ID |
| `scan`    | `--table NAME` | Scan and print all live rows |
| `update`  | `--table NAME --rid P:S --values "f1,f2,..."` | Update a row in-place or relocate it |
| `delete`  | `--table NAME --rid P:S` | Tombstone a row; prints `deleted rid=P:S` |
| `hexdump` | `--table NAME --rid P:S` | Print a hex/ASCII dump of a raw tuple with field annotations |

`--values` format: positional CSV in schema column order. Empty field or `\N` inserts NULL.
`--rid` format: `PAGE:SLOT` (e.g. `0:3`).

```
db-cli --db PATH row insert  --table users --values "1,Alice"
db-cli --db PATH row get     --table users --rid 0:0
db-cli --db PATH row scan    --table users
db-cli --db PATH row update  --table users --rid 0:1 --values "2,Bobby"
db-cli --db PATH row delete  --table users --rid 0:0
db-cli --db PATH row hexdump --table users --rid 0:0
```

### page

| Verb   | Flags | Description |
|--------|-------|-------------|
| `show` | `--table NAME --page N` | Render the slotted-page byte-layout map for page N |

```
db-cli --db PATH page show --table users --page 0
```

### index

| Verb   | Flags | Description |
|--------|-------|-------------|
| `show` | `--table NAME` | Render the B+Tree index as an indented tree |

```
db-cli --db PATH index show --table users
```

---

## Command Mapping

The table below maps course-spec command names to the db-cli noun-verb form.

| Spec command   | db-cli command |
|----------------|----------------|
| `table-create` | `db-cli table create --table NAME --columns SPEC` |
| `insert`       | `db-cli row insert --table NAME --values "f1,f2,..."` |
| `get`          | `db-cli row get --table NAME --rid P:S` |
| `scan`         | `db-cli row scan --table NAME` |
| `update`       | `db-cli row update --table NAME --rid P:S --values "f1,f2,..."` |
| `delete`       | `db-cli row delete --table NAME --rid P:S` |
| `page-show`    | `db-cli page show --table NAME --page N` |
| `hexdump`      | `db-cli row hexdump --table NAME --rid P:S` |
| `index-show`   | `db-cli index show --table NAME` |

---

## Tests

```
poetry run pytest
```

All tests run against isolated temporary directories (pytest `tmp_path`). No external
services or network access required.

---

## Project Layout

```
super_db/
├── src/super_db/
│   ├── db.py                    # init_db: create db directory + empty catalog
│   ├── catalog/
│   │   ├── catalog.py           # catalog read/write, table CRUD, row-level ops
│   │   └── schema.py            # TableMeta, Column, ColumnType, StorageTrack dataclasses
│   ├── cli/
│   │   ├── main.py              # argparse root; noun/verb dispatch
│   │   └── commands/
│   │       ├── init.py          # db init
│   │       ├── table.py         # table create/list/describe/drop
│   │       ├── row.py           # row insert/get/scan/update/delete/hexdump
│   │       ├── page.py          # page show
│   │       └── index.py         # index show + B+Tree walker
│   ├── storage/
│   │   ├── page_layout.py       # struct constants: PAGE_HDR, SLOT, HEADER_SIZE, SLOT_ENTRY_SIZE
│   │   ├── page.py              # Page: in-memory mutable bytearray + slot operations
│   │   ├── heap_file.py         # HeapFile: paged file reader/writer using os.pread/pwrite
│   │   ├── engine.py            # StorageEngine: higher-level table operations
│   │   ├── tuple_codec.py       # encode_tuple/decode_tuple + FieldSpan describe_tuple
│   │   ├── rid.py               # RID(page_id, slot_id) frozen dataclass
│   │   └── row.py               # Row(rid, values) result type
│   ├── index/
│   │   ├── node_layout.py       # B+Tree struct constants, key codecs, node encode/decode
│   │   └── bplustree.py         # BPlusTree: insert, search, split, persistence (.idx file)
│   ├── render/
│   │   ├── protocol.py          # Renderer Protocol (stdlib only, no rich import)
│   │   ├── plain_renderer.py    # PlainRenderer: deterministic text output, used in tests
│   │   └── rich_renderer.py     # RichRenderer: styled terminal output (only file importing rich)
│   └── common/
│       ├── constants.py         # CATALOG_FILE, DEFAULT_PAGE_SIZE, FORMAT_VERSION
│       ├── durability.py        # write_page (pwrite+fsync), write_json_atomic (temp+replace)
│       ├── errors.py            # SuperDBError hierarchy
│       └── log.py               # loguru setup
├── tests/                       # pytest suite (one file per module or feature)
├── scripts/
│   └── demo.py                  # 9-step end-to-end showcase; run to see live visualizations
└── pyproject.toml
```
