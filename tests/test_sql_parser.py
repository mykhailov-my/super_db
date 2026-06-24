import pytest

from superdb.errors import ParseError
from superdb.sql.ast import (
    BoolOp,
    ColumnDef,
    ColumnRef,
    Comparison,
    CreateTable,
    Insert,
    Literal,
    OrderBy,
    Select,
)
from superdb.sql.parser import parse

# --- CREATE TABLE ---


def test_create_table():
    stmt = parse("CREATE TABLE users (id INT, name TEXT, age INT)")

    assert stmt == CreateTable(
        "users",
        (ColumnDef("id", "INT"), ColumnDef("name", "TEXT"), ColumnDef("age", "INT")),
    )


def test_create_table_rejects_unknown_type():
    with pytest.raises(ParseError, match="unsupported column type"):
        parse("CREATE TABLE t (id BIGINT)")


# --- INSERT ---


def test_insert_values():
    stmt = parse("INSERT INTO users VALUES (1, 'Alice', NULL)")

    assert stmt == Insert(
        "users",
        (Literal("INT", 1), Literal("STRING", "Alice"), Literal("NULL", None)),
    )


# --- SELECT ---


def test_select_star():
    stmt = parse("SELECT * FROM users")

    assert stmt == Select(None, "users", None, None, None, None, None)


def test_select_columns_where_order_limit():
    stmt = parse("SELECT id, name FROM users WHERE age > 18 ORDER BY name DESC LIMIT 10")

    assert stmt.projections == (ColumnRef("id"), ColumnRef("name"))
    assert stmt.table == "users"
    assert stmt.where == Comparison(">", ColumnRef("age"), Literal("INT", 18))
    assert stmt.order_by == OrderBy("name", descending=True)
    assert stmt.limit == 10


def test_where_and_or_precedence():
    # AND binds tighter than OR: a OR b AND c  ==  a OR (b AND c)
    stmt = parse("SELECT * FROM t WHERE a = 1 OR b = 2 AND c = 3")

    assert stmt.where == BoolOp(
        "OR",
        Comparison("=", ColumnRef("a"), Literal("INT", 1)),
        BoolOp(
            "AND",
            Comparison("=", ColumnRef("b"), Literal("INT", 2)),
            Comparison("=", ColumnRef("c"), Literal("INT", 3)),
        ),
    )


@pytest.mark.parametrize("op", ["=", "!=", "<", "<=", ">", ">="])
def test_all_comparison_operators(op):
    stmt = parse(f"SELECT * FROM t WHERE a {op} 1")

    assert stmt.where == Comparison(op, ColumnRef("a"), Literal("INT", 1))


def test_trailing_semicolon_ok():
    assert parse("SELECT * FROM t;").table == "t"


def test_limit_zero_ok():
    assert parse("SELECT * FROM t LIMIT 0").limit == 0


def test_negative_limit_rejected():
    with pytest.raises(ParseError, match="LIMIT must not be negative"):
        parse("SELECT * FROM t LIMIT -1")


# --- the 5 named HW error cases ---


@pytest.mark.parametrize(
    "sql",
    [
        "SELEC id FROM users",  # misspelled keyword
        "SELECT FROM users",  # empty projection list
        "INSERT INTO users VALUES",  # missing values
        "CREATE TABLE users ()",  # no columns
        "SELECT id users",  # missing FROM
    ],
)
def test_named_error_cases_raise_parse_error(sql):
    with pytest.raises(ParseError):
        parse(sql)


def test_error_carries_position():
    with pytest.raises(ParseError) as exc:
        parse("SELECT FROM users")

    assert exc.value.pos == len("SELECT ")  # points at FROM


def test_bad_character_does_not_crash():
    with pytest.raises(ParseError, match="unexpected character"):
        parse("SELECT @ FROM t")


def test_unterminated_string_does_not_crash():
    with pytest.raises(ParseError):
        parse("INSERT INTO t VALUES ('oops)")


def test_deeply_nested_parens_raise_parse_error_not_recursion():
    # Pathological nesting must fail as ParseError, never RecursionError.
    sql = "SELECT * FROM t WHERE " + "(" * 500 + "a = 1" + ")" * 500
    with pytest.raises(ParseError):
        parse(sql)
