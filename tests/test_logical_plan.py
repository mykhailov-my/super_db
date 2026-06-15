from pathlib import Path

import pytest

from superdb import logical_plan as lp
from superdb.catalog import create_table
from superdb.database import init_db
from superdb.errors import LogicalError
from superdb.sql_parser import parse


def _users(db_dir: Path) -> None:
    init_db(db_dir)
    create_table(db_dir, "users", [("id", "INT", True), ("name", "TEXT", True), ("age", "INT", True)])


def plan(sql: str, db_dir: Path):
    return lp.build_plan(parse(sql), db_dir)


def _labels(projection) -> tuple:
    # Projection.columns is now (label, Expr) pairs.
    return tuple(label for label, _ in projection.columns)


# --- CREATE TABLE (no catalog needed) ---


def test_create_table_plan(tmp_path: Path):
    node = plan("CREATE TABLE t (id INT, name TEXT)", tmp_path)

    assert node == lp.CreateTable("t", (("id", "INT"), ("name", "TEXT")))


def test_create_table_rejects_duplicate_columns(tmp_path: Path):
    with pytest.raises(LogicalError, match="duplicate column"):
        plan("CREATE TABLE t (id INT, id TEXT)", tmp_path)


def test_create_table_duplicate_columns_case_insensitive(tmp_path: Path):
    # Catalog dedups case-insensitively; the binder must agree.
    with pytest.raises(LogicalError, match="duplicate column"):
        plan("CREATE TABLE t (a INT, A TEXT)", tmp_path)


# --- INSERT ---


def test_insert_plan(db_dir: Path):
    _users(db_dir)

    node = plan("INSERT INTO users VALUES (1, 'Alice', 20)", db_dir)

    assert node == lp.Insert("users", (1, "Alice", 20))


def test_insert_unknown_table(db_dir: Path):
    init_db(db_dir)
    with pytest.raises(LogicalError, match="does not exist"):
        plan("INSERT INTO ghosts VALUES (1)", db_dir)


def test_insert_arity_mismatch(db_dir: Path):
    _users(db_dir)
    with pytest.raises(LogicalError, match="3 columns but 2 values"):
        plan("INSERT INTO users VALUES (1, 'Alice')", db_dir)


# --- SELECT lowering ---


def test_select_star_expands_to_scan_under_projection(db_dir: Path):
    _users(db_dir)

    node = plan("SELECT * FROM users", db_dir)

    assert isinstance(node, lp.Projection)
    assert _labels(node) == ("id", "name", "age")
    assert isinstance(node.child, lp.Scan)


def test_select_full_stack_order(db_dir: Path):
    _users(db_dir)

    node = plan("SELECT id, name FROM users WHERE age > 18 ORDER BY name DESC LIMIT 10", db_dir)

    # Limit(Projection(Sort(Filter(Scan)))) — Sort below Projection so ORDER BY
    # can reference a column the projection drops.
    assert isinstance(node, lp.Limit)
    assert node.count == 10
    proj = node.child
    assert isinstance(proj, lp.Projection) and _labels(proj) == ("id", "name")
    sort = proj.child
    assert isinstance(sort, lp.Sort) and sort.column == "name" and sort.descending
    filt = sort.child
    assert isinstance(filt, lp.Filter)
    assert isinstance(filt.child, lp.Scan)


def test_select_wrappers_absent_when_clause_absent(db_dir: Path):
    _users(db_dir)

    node = plan("SELECT id FROM users", db_dir)

    # No WHERE/ORDER/LIMIT → just Projection over Scan.
    assert isinstance(node, lp.Projection)
    assert isinstance(node.child, lp.Scan)


def test_select_unknown_projection_column(db_dir: Path):
    _users(db_dir)
    with pytest.raises(LogicalError, match="nope"):
        plan("SELECT nope FROM users", db_dir)


def test_select_unknown_where_column(db_dir: Path):
    _users(db_dir)
    with pytest.raises(LogicalError, match="missing"):
        plan("SELECT id FROM users WHERE missing = 1", db_dir)


def test_select_unknown_order_column(db_dir: Path):
    _users(db_dir)
    with pytest.raises(LogicalError, match="bogus"):
        plan("SELECT id FROM users ORDER BY bogus", db_dir)


def test_select_unknown_table(db_dir: Path):
    init_db(db_dir)
    with pytest.raises(LogicalError, match="does not exist"):
        plan("SELECT * FROM ghosts", db_dir)


def test_long_boolean_chain_does_not_recurse_to_crash(db_dir: Path):
    # ~1000 AND-terms must fail as a clean ParseError, not RecursionError in the
    # binder's expression walk (or the AST printer).
    from superdb.errors import ParseError

    _users(db_dir)
    where = " AND ".join(["id = 1"] * 1000)
    with pytest.raises(ParseError, match="too long"):
        plan(f"SELECT id FROM users WHERE {where}", db_dir)
