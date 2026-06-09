"""Tests for Phase 6 mutations — update/delete on StorageEngine (MUT-01..MUT-04)."""
from pathlib import Path

import pytest

from superdb.database import init_db
from superdb.engine import StorageEngine
from superdb.errors import RecordNotFoundError
from superdb.rid import RID


def _as_set(rows):
    return {
        (r.rid.page_id, r.rid.slot_id, tuple(sorted(r.values.items())))
        for r in rows
    }


def test_update_inplace_same_rid(db_dir: Path) -> None:
    # Arrange — INT column guarantees fixed 4-byte encoding, so same-length is assured
    init_db(db_dir)
    engine = StorageEngine(db_dir)
    engine.create_table("t", [("id", "INT", False), ("score", "INT", False)])
    rid = engine.insert("t", {"id": 1, "score": 10})

    # Act
    returned_rid = engine.update("t", rid, {"id": 1, "score": 99})

    # Assert — same RID (in-place, same byte length)
    assert returned_rid == rid
    assert engine.get("t", rid) == {"id": 1, "score": 99}


def test_update_relocate_new_rid(db_dir: Path) -> None:
    # Arrange — TEXT column with different-length values forces relocation
    init_db(db_dir)
    engine = StorageEngine(db_dir)
    engine.create_table("t", [("id", "INT", False), ("name", "TEXT", False)])
    rid = engine.insert("t", {"id": 1, "name": "hi"})

    # Act — "hello world" encodes longer than "hi" → relocation
    new_rid = engine.update("t", rid, {"id": 1, "name": "hello world"})

    # Assert — new RID returned
    assert new_rid != rid
    # old RID must be dead
    with pytest.raises(RecordNotFoundError):
        engine.get("t", rid)
    # new RID resolves to updated values
    assert engine.get("t", new_rid) == {"id": 1, "name": "hello world"}


def test_delete_removes_from_scan(db_dir: Path) -> None:
    # Arrange
    init_db(db_dir)
    engine = StorageEngine(db_dir)
    engine.create_table("t", [("id", "INT", False)])
    rid_a = engine.insert("t", {"id": 1})
    rid_b = engine.insert("t", {"id": 2})
    rid_c = engine.insert("t", {"id": 3})

    # Act
    engine.delete("t", rid_b)

    # Assert — deleted RID raises
    with pytest.raises(RecordNotFoundError):
        engine.get("t", rid_b)

    # Scan omits the deleted record
    rows = engine.scan("t")
    row_ids = {(r.rid.page_id, r.rid.slot_id) for r in rows}
    assert (rid_b.page_id, rid_b.slot_id) not in row_ids

    # Other RIDs still resolve correctly
    assert engine.get("t", rid_a) == {"id": 1}
    assert engine.get("t", rid_c) == {"id": 3}


def test_delete_already_dead_raises(db_dir: Path) -> None:
    # Arrange
    init_db(db_dir)
    engine = StorageEngine(db_dir)
    engine.create_table("t", [("id", "INT", False)])
    rid = engine.insert("t", {"id": 42})

    # Act
    engine.delete("t", rid)

    # Assert — second delete raises (no idempotent no-op)
    with pytest.raises(RecordNotFoundError):
        engine.delete("t", rid)


def test_update_missing_rid_raises(db_dir: Path) -> None:
    # Arrange
    init_db(db_dir)
    engine = StorageEngine(db_dir)
    engine.create_table("t", [("id", "INT", False)])

    # Act / Assert
    with pytest.raises(RecordNotFoundError):
        engine.update("t", RID(99, 0), {"id": 1})


def test_delete_missing_rid_raises(db_dir: Path) -> None:
    # Arrange
    init_db(db_dir)
    engine = StorageEngine(db_dir)
    engine.create_table("t", [("id", "INT", False)])

    # Act / Assert
    with pytest.raises(RecordNotFoundError):
        engine.delete("t", RID(99, 0))


def test_mutations_survive_restart(db_dir: Path) -> None:
    # Arrange — build state with engine1
    init_db(db_dir)
    engine1 = StorageEngine(db_dir)
    engine1.create_table("t", [("id", "INT", False), ("label", "TEXT", False)])

    rid_inplace = engine1.insert("t", {"id": 1, "label": "aaa"})
    rid_relocate = engine1.insert("t", {"id": 2, "label": "bb"})
    rid_delete = engine1.insert("t", {"id": 3, "label": "ccc"})
    rid_untouched = engine1.insert("t", {"id": 4, "label": "dddd"})

    # Perform mutations with engine1
    new_inplace_rid = engine1.update("t", rid_inplace, {"id": 1, "label": "zzz"})
    new_relocate_rid = engine1.update("t", rid_relocate, {"id": 2, "label": "much longer text here"})
    engine1.delete("t", rid_delete)

    # Act — simulate process restart with a completely fresh engine (no shared state)
    engine2 = StorageEngine(db_dir)

    # Assert — in-place update: RID unchanged, value updated
    assert new_inplace_rid == rid_inplace
    assert engine2.get("t", rid_inplace) == {"id": 1, "label": "zzz"}

    # Assert — relocating update: new RID resolves, old RID raises
    assert new_relocate_rid != rid_relocate
    assert engine2.get("t", new_relocate_rid) == {"id": 2, "label": "much longer text here"}
    with pytest.raises(RecordNotFoundError):
        engine2.get("t", rid_relocate)

    # Assert — deleted record absent from scan and direct get raises
    scan_rows = engine2.scan("t")
    deleted_ids = {(r.rid.page_id, r.rid.slot_id) for r in scan_rows}
    assert (rid_delete.page_id, rid_delete.slot_id) not in deleted_ids
    with pytest.raises(RecordNotFoundError):
        engine2.get("t", rid_delete)

    # Assert — untouched RID identical after restart
    assert engine2.get("t", rid_untouched) == {"id": 4, "label": "dddd"}
