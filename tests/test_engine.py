"""Tests for storage/engine.py — StorageEngine dict roundtrip + restart durability (WRITE-03, WRITE-04)."""
from pathlib import Path

import pytest

from super_db.common.errors import RecordNotFoundError
from super_db.db import init_db
from super_db.storage.engine import StorageEngine
from super_db.storage.rid import RID


def test_engine_insert_get_roundtrip(db_dir: Path) -> None:
    # Arrange
    init_db(db_dir)
    engine = StorageEngine(db_dir)
    engine.create_table("t", [("id", "INT", False), ("name", "TEXT", True)])

    # Act
    rid = engine.insert("t", {"id": 7, "name": "alice"})
    result = engine.get("t", rid)

    # Assert
    assert result == {"id": 7, "name": "alice"}


def test_restart_durability(db_dir: Path) -> None:
    # Arrange
    init_db(db_dir)
    engine1 = StorageEngine(db_dir)
    engine1.create_table("t", [("id", "INT", False)])
    rid = engine1.insert("t", {"id": 42})

    # Act — simulate process restart with a fresh engine (no shared state with engine1)
    engine2 = StorageEngine(db_dir)
    got = engine2.get("t", rid)

    # Assert
    assert got == {"id": 42}


def test_restart_multiple_records(db_dir: Path) -> None:
    # Arrange
    init_db(db_dir)
    engine1 = StorageEngine(db_dir)
    engine1.create_table("t", [("id", "INT", False), ("label", "TEXT", True)])
    rows = [
        {"id": 1, "label": "alpha"},
        {"id": 2, "label": "beta"},
        {"id": 3, "label": "gamma"},
        {"id": 4, "label": "delta"},
        {"id": 5, "label": "epsilon"},
    ]
    written = []
    for row in rows:
        rid = engine1.insert("t", row)
        written.append((rid, row))

    # Act — simulate process restart with a fresh engine (no shared state with engine1)
    engine2 = StorageEngine(db_dir)

    # Assert — all RIDs resolve correctly after restart
    for rid, expected in written:
        assert engine2.get("t", rid) == expected


def test_get_missing_rid_raises(db_dir: Path) -> None:
    # Arrange
    init_db(db_dir)
    engine = StorageEngine(db_dir)
    engine.create_table("t", [("id", "INT", False)])

    # Act / Assert
    with pytest.raises(RecordNotFoundError):
        engine.get("t", RID(99, 0))
