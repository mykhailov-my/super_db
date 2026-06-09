import json
from pathlib import Path

import pytest

from superdb.catalog import (
    TableHandle,
    create_table,
    delete,
    describe_table,
    drop_table,
    get,
    insert,
    list_tables,
    open_table,
    update,
)
from superdb.database import init_db
from superdb.errors import RecordNotFoundError, StorageError
from superdb.rid import RID


def test_validate_schema(db_dir: Path) -> None:
    init_db(db_dir)

    # Empty column list
    with pytest.raises(ValueError, match="at least one column"):
        create_table(db_dir, "t", [])

    # Duplicate column names (case-insensitive)
    with pytest.raises(ValueError, match="duplicate column"):
        create_table(db_dir, "t", [("id", "INT", False), ("ID", "TEXT", True)])

    # Unsupported type
    with pytest.raises(ValueError, match="unsupported column type"):
        create_table(db_dir, "t", [("x", "FLOAT", False)])

    # Invalid table name
    with pytest.raises(ValueError, match="invalid table name"):
        create_table(db_dir, "../x", [("id", "INT", False)])

    with pytest.raises(ValueError, match="invalid table name"):
        create_table(db_dir, "9bad", [("id", "INT", False)])

    # Invalid column name
    with pytest.raises(ValueError, match="invalid column name"):
        create_table(db_dir, "t", [("a-b", "INT", False)])


def test_catalog_json_shape(db_dir: Path) -> None:
    init_db(db_dir)
    create_table(db_dir, "users", [("id", "INT", False), ("name", "TEXT", True)])

    raw = json.loads((db_dir / "catalog.json").read_text())
    assert "version" in raw
    assert "next_table_id" in raw
    assert "tables" in raw

    t = raw["tables"][0]
    assert "table_id" in t
    assert t["name"] == "users"
    assert t["storage_track"] == "row"
    assert t["page_size"] == 4096
    assert t["format_version"] == 1

    cols = t["columns"]
    assert len(cols) == 2
    assert cols[0] == {"name": "id", "type": "INT", "nullable": False}
    assert cols[1] == {"name": "name", "type": "TEXT", "nullable": True}


def test_create_table_creates_heap(db_dir: Path) -> None:
    init_db(db_dir)
    create_table(db_dir, "users", [("id", "INT", False)])

    heap = db_dir / "users.tbl"
    assert heap.exists()
    assert heap.stat().st_size == 0


def test_create_table_orphan_truncated(db_dir: Path) -> None:
    init_db(db_dir)

    # Write junk into an orphan .tbl — no catalog entry
    orphan = db_dir / "users.tbl"
    orphan.write_bytes(b"junk data")
    assert orphan.stat().st_size > 0

    create_table(db_dir, "users", [("id", "INT", False)])

    assert orphan.stat().st_size == 0
    tables = list_tables(db_dir)
    assert any(t.name == "users" for t in tables)


def test_create_table_dup_name(db_dir: Path) -> None:
    init_db(db_dir)
    create_table(db_dir, "users", [("id", "INT", False)])

    with pytest.raises(ValueError, match="already exists"):
        create_table(db_dir, "users", [("id", "INT", False)])


def test_open_table(db_dir: Path) -> None:
    init_db(db_dir)
    create_table(db_dir, "users", [("id", "INT", False)])

    handle = open_table(db_dir, "users")
    assert isinstance(handle, TableHandle)
    assert handle.meta.name == "users"
    assert handle.heap_path == db_dir / "users.tbl"


def test_open_table_missing(db_dir: Path) -> None:
    init_db(db_dir)

    with pytest.raises(ValueError):
        open_table(db_dir, "ghost")


def test_list_tables(db_dir: Path) -> None:
    init_db(db_dir)
    create_table(db_dir, "users", [("id", "INT", False)])
    create_table(db_dir, "orders", [("order_id", "INT", False)])

    names = {t.name for t in list_tables(db_dir)}
    assert "users" in names
    assert "orders" in names


def test_describe_table(db_dir: Path) -> None:
    init_db(db_dir)
    create_table(db_dir, "users", [("id", "INT", False), ("email", "TEXT", True)])

    meta = describe_table(db_dir, "users")
    assert meta.name == "users"
    assert len(meta.columns) == 2
    assert meta.columns[0].name == "id"
    assert meta.columns[0].col_type.value == "INT"
    assert meta.columns[0].nullable is False
    assert meta.columns[1].name == "email"
    assert meta.columns[1].col_type.value == "TEXT"
    assert meta.columns[1].nullable is True


def test_drop_table(db_dir: Path) -> None:
    init_db(db_dir)
    create_table(db_dir, "users", [("id", "INT", False)])
    heap = db_dir / "users.tbl"
    assert heap.exists()

    drop_table(db_dir, "users")

    assert not heap.exists()
    tables = list_tables(db_dir)
    assert not any(t.name == "users" for t in tables)


def test_drop_table_missing(db_dir: Path) -> None:
    init_db(db_dir)

    with pytest.raises(ValueError):
        drop_table(db_dir, "ghost")


def test_catalog_survives_restart(db_dir: Path) -> None:
    init_db(db_dir)
    create_table(db_dir, "users", [("id", "INT", False), ("name", "TEXT", True)])

    # Simulate restart by re-reading purely from disk (stateless calls)
    tables = list_tables(db_dir)
    assert len(tables) == 1
    assert tables[0].name == "users"

    meta = describe_table(db_dir, "users")
    assert meta.storage_track.value == "row"
    assert meta.page_size == 4096
    assert meta.columns[0].name == "id"
    assert meta.columns[0].col_type.value == "INT"
    assert meta.columns[1].col_type.value == "TEXT"
    assert meta.columns[1].nullable is True


def test_next_table_id_monotonic(db_dir: Path) -> None:
    init_db(db_dir)
    a = create_table(db_dir, "a", [("x", "INT", False)])
    drop_table(db_dir, "a")
    b = create_table(db_dir, "b", [("y", "INT", False)])

    assert b.table_id > a.table_id

    raw = json.loads((db_dir / "catalog.json").read_text())
    assert raw["next_table_id"] > b.table_id


def test_create_table_custom_page_size(db_dir: Path) -> None:
    init_db(db_dir)
    create_table(db_dir, "wide", [("data", "TEXT", True)], page_size=8192)

    meta = describe_table(db_dir, "wide")
    assert meta.page_size == 8192


def test_insert_get_via_handle_roundtrip(db_dir: Path) -> None:
    # catalog.insert/get delegate to the heap using the handle directly
    # (no second catalog read, no StorageEngine round-trip).
    init_db(db_dir)
    create_table(db_dir, "t", [("id", "INT", False), ("name", "TEXT", True)])
    handle = open_table(db_dir, "t")

    rid = insert(handle, {"id": 3, "name": "carol"})
    assert get(handle, rid) == {"id": 3, "name": "carol"}


def test_update_via_handle_inplace(db_dir: Path) -> None:
    # Same-length update keeps the RID and changes the value at the catalog layer.
    init_db(db_dir)
    create_table(db_dir, "t", [("id", "INT", False)])
    handle = open_table(db_dir, "t")
    rid = insert(handle, {"id": 1})

    same_rid = update(handle, rid, {"id": 2})

    assert same_rid == rid
    assert get(handle, rid) == {"id": 2}


def test_update_via_handle_missing_column_raises(db_dir: Path) -> None:
    init_db(db_dir)
    create_table(db_dir, "t", [("id", "INT", False), ("name", "TEXT", True)])
    handle = open_table(db_dir, "t")
    rid = insert(handle, {"id": 1, "name": "alice"})

    with pytest.raises(StorageError, match="name"):
        update(handle, rid, {"id": 2})


def test_delete_via_handle(db_dir: Path) -> None:
    # catalog.delete tombstones the record; a second delete raises (no idempotent no-op).
    init_db(db_dir)
    create_table(db_dir, "t", [("id", "INT", False)])
    handle = open_table(db_dir, "t")
    rid = insert(handle, {"id": 1})

    delete(handle, rid)

    with pytest.raises(RecordNotFoundError):
        get(handle, rid)
    with pytest.raises(RecordNotFoundError):
        delete(handle, rid)


def test_rid_definition() -> None:
    r = RID(1, 2)
    assert r.page_id == 1
    assert r.slot_id == 2


def test_corrupt_catalog_missing_keys_raises_value_error(tmp_path: Path) -> None:
    # A structurally-broken catalog.json must surface a clean ValueError,
    # not a KeyError traceback.
    (tmp_path / "catalog.json").write_text(json.dumps({"version": 1}))
    with pytest.raises(ValueError, match="corrupt catalog"):
        list_tables(tmp_path)


def test_corrupt_catalog_non_utf8_raises_value_error(tmp_path: Path) -> None:
    (tmp_path / "catalog.json").write_bytes(b"\xff\xfe not json")
    with pytest.raises(ValueError, match="corrupt catalog"):
        list_tables(tmp_path)


def test_corrupt_catalog_bad_table_name_rejected(tmp_path: Path) -> None:
    # A hostile name in a hand-edited catalog must not reach the filesystem.
    cat = {
        "version": 1,
        "next_table_id": 2,
        "tables": [{
            "table_id": 1,
            "name": "../evil",
            "columns": [{"name": "id", "type": "INT", "nullable": True}],
            "storage_track": "row",
            "page_size": 4096,
            "format_version": 1,
        }],
    }
    (tmp_path / "catalog.json").write_text(json.dumps(cat))
    with pytest.raises(ValueError, match="invalid table name"):
        describe_table(tmp_path, "../evil")


def test_create_table_failed_save_leaves_no_heap(db_dir: Path, monkeypatch) -> None:
    """If catalog persistence fails, no orphan .tbl heap is left behind — the
    catalog save is the commit point (heap is created only afterward)."""
    from superdb import catalog as catalog_mod

    init_db(db_dir)

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(catalog_mod, "_save_catalog", boom)
    with pytest.raises(OSError):
        create_table(db_dir, "t", [("id", "INT", False)])
    assert not (db_dir / "t.tbl").exists()
