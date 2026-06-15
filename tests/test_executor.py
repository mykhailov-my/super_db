"""End-to-end execution: SQL text → result, through the real storage engine."""
from pathlib import Path

import pytest

from superdb import executor as ex
from superdb import logical_plan as lp
from superdb.database import init_db
from superdb.engine import StorageEngine
from superdb.errors import LogicalError
from superdb.sql_parser import parse


def run(sql: str, db_dir: Path) -> ex.Result:
    plan = lp.build_plan(parse(sql), db_dir)
    return ex.execute(ex.lower(plan), StorageEngine(db_dir))


@pytest.fixture
def users(db_dir: Path) -> Path:
    init_db(db_dir)
    run("CREATE TABLE users (id INT, name TEXT, age INT)", db_dir)
    for vals in ["(1, 'Alice', 30)", "(2, 'Bob', 17)", "(3, 'Carol', 25)", "(4, 'Dave', NULL)"]:
        run(f"INSERT INTO users VALUES {vals}", db_dir)
    return db_dir


def test_create_and_insert_then_select(users: Path):
    result = run("SELECT * FROM users", users)

    assert result.columns == ("id", "name", "age")
    assert len(result.rows) == 4


def test_projection_subset_and_order(users: Path):
    result = run("SELECT name, id FROM users", users)

    assert result.columns == ("name", "id")
    assert set(result.rows[0].keys()) == {"name", "id"}


def test_where_filters(users: Path):
    result = run("SELECT name FROM users WHERE age >= 18", users)

    names = {r["name"] for r in result.rows}
    assert names == {"Alice", "Carol"}  # Bob is 17, Dave is NULL → excluded


def test_where_null_comparison_excludes(users: Path):
    # age = NULL is never TRUE, so Dave (NULL age) is not returned.
    result = run("SELECT name FROM users WHERE age = 30", users)

    assert [r["name"] for r in result.rows] == ["Alice"]


def test_and_or_predicate(users: Path):
    result = run("SELECT id FROM users WHERE age > 25 OR name = 'Bob'", users)

    assert {r["id"] for r in result.rows} == {1, 2}  # Alice(30), Bob(name)


def test_order_by_asc_and_desc(users: Path):
    asc = run("SELECT name FROM users ORDER BY name", users)
    desc = run("SELECT name FROM users ORDER BY name DESC", users)

    assert [r["name"] for r in asc.rows] == ["Alice", "Bob", "Carol", "Dave"]
    assert [r["name"] for r in desc.rows] == ["Dave", "Carol", "Bob", "Alice"]


def test_order_by_nulls_sort_first(users: Path):
    result = run("SELECT name, age FROM users ORDER BY age", users)

    assert result.rows[0]["name"] == "Dave"  # NULL age sorts first ascending


def test_limit(users: Path):
    result = run("SELECT id FROM users ORDER BY id LIMIT 2", users)

    assert [r["id"] for r in result.rows] == [1, 2]


def test_limit_zero_returns_empty(users: Path):
    result = run("SELECT * FROM users LIMIT 0", users)

    assert result.rows == []
    assert result.columns == ("id", "name", "age")  # columns still known


def test_full_select_pipeline(users: Path):
    result = run(
        "SELECT id, name FROM users WHERE age >= 18 ORDER BY name DESC LIMIT 1", users
    )

    assert result.columns == ("id", "name")
    assert result.rows == [{"id": 3, "name": "Carol"}]


def test_insert_null_roundtrips(users: Path):
    result = run("SELECT age FROM users WHERE id = 4", users)

    assert result.rows == [{"age": None}]


def test_restart_safety(db_dir: Path):
    # Build, then re-open with a fresh engine/process-equivalent and read back.
    init_db(db_dir)
    run("CREATE TABLE t (id INT, name TEXT)", db_dir)
    run("INSERT INTO t VALUES (42, 'persisted')", db_dir)

    # A brand-new StorageEngine instance = process restart for our purposes.
    result = run("SELECT name FROM t WHERE id = 42", db_dir)

    assert result.rows == [{"name": "persisted"}]


def test_query_unknown_table_is_logical_error(db_dir: Path):
    init_db(db_dir)
    with pytest.raises(LogicalError, match="does not exist"):
        run("SELECT * FROM ghosts", db_dir)


def test_order_by_a_non_projected_column(users: Path):
    # ORDER BY name while only projecting id — must sort by name, not crash.
    result = run("SELECT id FROM users ORDER BY name", users)

    assert result.columns == ("id",)
    assert [r["id"] for r in result.rows] == [1, 2, 3, 4]  # Alice,Bob,Carol,Dave


def test_duplicate_create_table_is_logical_error(db_dir: Path):
    init_db(db_dir)
    run("CREATE TABLE t (id INT)", db_dir)
    with pytest.raises(LogicalError, match="already exists"):
        run("CREATE TABLE t (id INT)", db_dir)


def test_nested_null_comparison_excludes_null_row(users: Path):
    # (age = 1) = (id = 99): inner NULL comparison must stay unknown, not collapse
    # to a real False that the outer = would match. Dave (NULL age) must not appear.
    result = run("SELECT id FROM users WHERE (age = 1) = (id = 99)", users)

    assert 4 not in {r["id"] for r in result.rows}
