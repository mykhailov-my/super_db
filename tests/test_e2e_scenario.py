"""End-to-end 9-step integration test for super_db.

Proves that the full lifecycle (create → insert → get → scan → update → delete → restart)
works correctly, with a REAL restart simulated by constructing a fresh StorageEngine.
"""
from pathlib import Path

import pytest

from superdb.database import init_db
from superdb.engine import StorageEngine
from superdb.errors import RecordNotFoundError


def test_nine_step_scenario(db_dir: Path) -> None:
    # Step 1: create db
    init_db(db_dir)

    # Step 2: create table
    engine1 = StorageEngine(db_dir)
    engine1.create_table("users", [("id", "INT", False), ("name", "TEXT", True)])

    # Step 3: insert
    rid1 = engine1.insert("users", {"id": 1, "name": "Alice"})
    rid2 = engine1.insert("users", {"id": 2, "name": "Bob"})

    # Step 4: get
    assert engine1.get("users", rid1) == {"id": 1, "name": "Alice"}

    # Step 5: scan
    rows = engine1.scan("users")
    assert len(rows) == 2

    # Step 6: update Bob -> Bobby
    new_rid = engine1.update("users", rid2, {"id": 2, "name": "Bobby"})

    # Step 7: delete Alice
    engine1.delete("users", rid1)

    # Step 8: restart — FRESH engine, no shared state with engine1
    engine2 = StorageEngine(db_dir)

    # Step 9: verify identical data
    with pytest.raises(RecordNotFoundError):
        engine2.get("users", rid1)  # deleted record gone

    assert engine2.get("users", new_rid) == {"id": 2, "name": "Bobby"}

    rows_after = engine2.scan("users")
    assert len(rows_after) == 1
