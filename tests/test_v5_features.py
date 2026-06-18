"""v5.0: INNER JOIN, aggregation + GROUP BY, scalar function — end to end."""

from pathlib import Path

import pytest

from superdb import executor as ex
from superdb import logical_plan as lp
from superdb.database import init_db
from superdb.engine import StorageEngine
from superdb.errors import LogicalError
from superdb.sql_parser import parse


def run(sql: str, db_dir: Path) -> ex.Result:
    return ex.execute(ex.lower(lp.build_plan(parse(sql), db_dir)), StorageEngine(db_dir))


@pytest.fixture
def shop(db_dir: Path) -> Path:
    init_db(db_dir)
    run("CREATE TABLE users (id INT, name TEXT)", db_dir)
    run("CREATE TABLE orders (id INT, user_id INT, total INT)", db_dir)
    for v in ["(1, 'Alice')", "(2, 'Bob')", "(3, 'Carol')"]:
        run(f"INSERT INTO users VALUES {v}", db_dir)
    for v in ["(1, 1, 100)", "(2, 1, 50)", "(3, 2, 30)"]:  # Carol has no orders
        run(f"INSERT INTO orders VALUES {v}", db_dir)
    return db_dir


# --- INNER JOIN ---


def test_inner_join_matches_rows(shop: Path):
    result = run(
        "SELECT users.name, orders.total FROM users JOIN orders ON users.id = orders.user_id",
        shop,
    )

    assert result.columns == ("users.name", "orders.total")
    pairs = {(r["users.name"], r["orders.total"]) for r in result.rows}
    assert pairs == {("Alice", 100), ("Alice", 50), ("Bob", 30)}  # Carol excluded


def test_join_unknown_table_errors(shop: Path):
    with pytest.raises(LogicalError, match="does not exist"):
        run("SELECT users.id FROM users JOIN ghosts ON users.id = ghosts.x", shop)


def test_join_ambiguous_bare_column_errors(shop: Path):
    # 'id' exists in both users and orders.
    with pytest.raises(LogicalError, match="ambiguous"):
        run("SELECT id FROM users JOIN orders ON users.id = orders.user_id", shop)


# --- aggregation without GROUP BY ---


def test_count_star(shop: Path):
    result = run("SELECT COUNT(*) FROM orders", shop)

    assert result.rows == [{"COUNT(*)": 3}]


def test_sum(shop: Path):
    result = run("SELECT SUM(total) FROM orders", shop)

    assert result.rows == [{"SUM(total)": 180}]


def test_min_max_avg(shop: Path):
    assert run("SELECT MIN(total) FROM orders", shop).rows == [{"MIN(total)": 30}]
    assert run("SELECT MAX(total) FROM orders", shop).rows == [{"MAX(total)": 100}]
    assert run("SELECT AVG(total) FROM orders", shop).rows == [{"AVG(total)": 60}]


# --- aggregation with GROUP BY ---


def test_group_by_count(shop: Path):
    result = run("SELECT user_id, COUNT(*) FROM orders GROUP BY user_id", shop)

    by_user = {r["user_id"]: r["COUNT(*)"] for r in result.rows}
    assert by_user == {1: 2, 2: 1}


def test_group_by_sum(shop: Path):
    result = run("SELECT user_id, SUM(total) FROM orders GROUP BY user_id", shop)

    by_user = {r["user_id"]: r["SUM(total)"] for r in result.rows}
    assert by_user == {1: 150, 2: 30}


def test_group_by_non_aggregate_non_group_column_errors(shop: Path):
    # total is neither the GROUP BY column nor inside an aggregate.
    with pytest.raises(LogicalError, match="GROUP BY or an aggregate"):
        run("SELECT total, COUNT(*) FROM orders GROUP BY user_id", shop)


# --- scalar function ---


def test_length_scalar(shop: Path):
    result = run("SELECT name, LENGTH(name) FROM users", shop)

    by_name = {r["name"]: r["LENGTH(name)"] for r in result.rows}
    assert by_name == {"Alice": 5, "Bob": 3, "Carol": 5}


def test_length_of_null_is_null(db_dir: Path):
    init_db(db_dir)
    run("CREATE TABLE t (id INT, name TEXT)", db_dir)
    run("INSERT INTO t VALUES (1, NULL)", db_dir)

    result = run("SELECT LENGTH(name) FROM t", db_dir)

    assert result.rows == [{"LENGTH(name)": None}]


# --- regressions from the v5.0 adversarial review ---


def test_join_on_reversed_order_works(shop: Path):
    # Equality is symmetric: ON b.y = a.x must match ON a.x = b.y.
    result = run(
        "SELECT users.name, orders.total FROM users JOIN orders ON orders.user_id = users.id",
        shop,
    )

    pairs = {(r["users.name"], r["orders.total"]) for r in result.rows}
    assert pairs == {("Alice", 100), ("Alice", 50), ("Bob", 30)}


def test_join_on_same_table_both_sides_errors(shop: Path):
    with pytest.raises(LogicalError, match="one column from each table"):
        run("SELECT users.name FROM users JOIN orders ON users.id = users.id", shop)


def test_sum_over_text_column_is_logical_error(shop: Path):
    with pytest.raises(LogicalError, match="numeric"):
        run("SELECT SUM(name) FROM users", shop)


def test_aggregate_honors_order_by_and_limit(shop: Path):
    result = run(
        "SELECT user_id, SUM(total) FROM orders GROUP BY user_id ORDER BY user_id DESC LIMIT 1",
        shop,
    )

    assert result.rows == [{"user_id": 2, "SUM(total)": 30}]  # highest user_id, one row


def test_aggregate_order_by_unknown_column_errors(shop: Path):
    with pytest.raises(LogicalError, match="not in the result"):
        run("SELECT user_id, COUNT(*) FROM orders GROUP BY user_id ORDER BY total", shop)


def test_aggregate_preserves_select_list_column_order(shop: Path):
    # Group column written AFTER the aggregate must stay second, not be yanked first.
    result = run("SELECT SUM(total), user_id FROM orders GROUP BY user_id", shop)
    assert result.columns == ("SUM(total)", "user_id")
    # ...and the control: written first stays first.
    control = run("SELECT user_id, SUM(total) FROM orders GROUP BY user_id", shop)
    assert control.columns == ("user_id", "SUM(total)")


def test_duplicate_output_label_rejected(shop: Path):
    with pytest.raises(LogicalError, match="duplicate output column"):
        run("SELECT id, id FROM users", shop)
