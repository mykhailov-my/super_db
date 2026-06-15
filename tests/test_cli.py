"""CLI behaviour tests — exercise the installed db-cli entry point via subprocess.

The console script is resolved via shutil.which("db-cli") so tests run against
the venv-installed binary, the same binary a user would call.
"""
import shutil
import subprocess
from pathlib import Path

import pytest


def _run(*args, **kwargs) -> subprocess.CompletedProcess:
    cli = shutil.which("db-cli")
    if cli is None:
        pytest.skip("db-cli not found on PATH; run tests inside the project venv")
    return subprocess.run([cli, *args], capture_output=True, text=True, **kwargs)


def test_help_exits_0() -> None:
    result = _run("--help")
    assert result.returncode == 0
    assert "usage: db-cli" in result.stdout


def test_version_flag() -> None:
    result = _run("--version")
    assert result.returncode == 0
    assert result.stdout.strip() == "db-cli 0.1.0"


def test_bad_arg_exits_2() -> None:
    result = _run("bogus")
    assert result.returncode == 2


def test_no_subcommand_exits_1() -> None:
    result = _run()
    assert result.returncode == 1
    assert "usage: db-cli" in result.stderr


def test_init_success(tmp_path: Path) -> None:
    db_dir = tmp_path / "mydb"
    result = _run("db", "init", "--db", str(db_dir))
    assert result.returncode == 0
    assert "Initialized super_db database at" in result.stdout
    assert (db_dir / "meta.json").exists()


def test_init_already_exits_1(tmp_path: Path) -> None:
    db_dir = tmp_path / "mydb"
    _run("db", "init", "--db", str(db_dir))
    result = _run("db", "init", "--db", str(db_dir))
    assert result.returncode == 1
    assert "already a super_db database" in result.stderr


def test_init_force_reinit(tmp_path: Path) -> None:
    db_dir = tmp_path / "mydb"
    _run("db", "init", "--db", str(db_dir))
    result = _run("db", "init", "--db", str(db_dir), "--force")
    assert result.returncode == 0


def test_debug_flag(tmp_path: Path) -> None:
    db_dir = tmp_path / "mydb"
    result = _run("--debug", "db", "init", "--db", str(db_dir))
    assert result.returncode == 0
    assert "[DEBUG]" in result.stderr


def test_default_quiet(tmp_path: Path) -> None:
    db_dir = tmp_path / "mydb"
    result = _run("db", "init", "--db", str(db_dir))
    assert result.returncode == 0
    assert "[DEBUG]" not in result.stderr
    assert "[INFO]" not in result.stderr


def test_global_db_flag_before_subcommand(tmp_path: Path) -> None:
    # --db is a global flag: it must work placed before the noun/verb.
    db_dir = tmp_path / "mydb"
    result = _run("--db", str(db_dir), "db", "init")
    assert result.returncode == 0
    assert (db_dir / "meta.json").exists()


def test_sql_parse_prints_ast() -> None:
    result = _run("sql", "parse", "--query", "SELECT id FROM users WHERE age > 18")
    assert result.returncode == 0
    assert "Select [id] from users" in result.stdout
    assert "where (age > 18)" in result.stdout


def test_sql_parse_error_exits_nonzero_to_stderr() -> None:
    result = _run("sql", "parse", "--query", "SELECT FROM users")
    assert result.returncode == 1
    assert result.stdout == ""
    assert "expected" in result.stderr.lower()


def test_sql_plan_prints_logical_tree(tmp_path: Path) -> None:
    db_dir = tmp_path / "db"
    _run("db", "init", "--db", str(db_dir))
    _run("table", "create", "--db", str(db_dir), "--table", "users",
         "--columns", "id:INT,name:TEXT,age:INT")

    result = _run("sql", "plan", "--db", str(db_dir),
                  "--query", "SELECT id FROM users WHERE age > 18 LIMIT 5")

    assert result.returncode == 0
    assert "Limit 5" in result.stdout
    assert "Projection [id]" in result.stdout
    assert "Filter [(age > 18)]" in result.stdout
    assert "Scan users" in result.stdout


def test_sql_plan_unknown_table_exits_nonzero(tmp_path: Path) -> None:
    db_dir = tmp_path / "db"
    _run("db", "init", "--db", str(db_dir))

    result = _run("sql", "plan", "--db", str(db_dir), "--query", "SELECT * FROM ghosts")

    assert result.returncode == 1
    assert "does not exist" in result.stderr


def test_sql_query_end_to_end(tmp_path: Path) -> None:
    db_dir = tmp_path / "db"
    _run("db", "init", "--db", str(db_dir))
    _run("sql", "query", "--db", str(db_dir),
         "--query", "CREATE TABLE users (id INT, name TEXT, age INT)")
    _run("sql", "query", "--db", str(db_dir),
         "--query", "INSERT INTO users VALUES (1, 'Alice', 30)")
    _run("sql", "query", "--db", str(db_dir),
         "--query", "INSERT INTO users VALUES (2, 'Bob', 17)")

    result = _run("sql", "query", "--db", str(db_dir),
                  "--query", "SELECT name FROM users WHERE age >= 18")

    assert result.returncode == 0
    assert "Alice" in result.stdout
    assert "Bob" not in result.stdout  # 17 < 18, filtered out


def test_sql_query_persists_across_processes(tmp_path: Path) -> None:
    db_dir = tmp_path / "db"
    _run("db", "init", "--db", str(db_dir))
    _run("sql", "query", "--db", str(db_dir), "--query", "CREATE TABLE t (id INT, v TEXT)")
    _run("sql", "query", "--db", str(db_dir), "--query", "INSERT INTO t VALUES (1, 'kept')")

    # Separate subprocess invocation = restart.
    result = _run("sql", "query", "--db", str(db_dir), "--query", "SELECT v FROM t")

    assert result.returncode == 0
    assert "kept" in result.stdout


def test_sql_explain_shows_both_plans(tmp_path: Path) -> None:
    db_dir = tmp_path / "db"
    _run("db", "init", "--db", str(db_dir))
    _run("table", "create", "--db", str(db_dir), "--table", "users",
         "--columns", "id:INT,age:INT")

    result = _run("sql", "explain", "--db", str(db_dir),
                  "--query", "SELECT id FROM users WHERE age > 18")

    assert result.returncode == 0
    assert "Logical Plan:" in result.stdout
    assert "Scan users" in result.stdout
    assert "Physical Plan:" in result.stdout
    assert "TableScanExec users" in result.stdout


def test_sql_query_join_and_aggregate(tmp_path: Path) -> None:
    db_dir = tmp_path / "db"
    _run("db", "init", "--db", str(db_dir))
    for ddl in ["CREATE TABLE users (id INT, name TEXT)",
                "CREATE TABLE orders (id INT, user_id INT, total INT)"]:
        _run("sql", "query", "--db", str(db_dir), "--query", ddl)
    for dml in ["INSERT INTO users VALUES (1, 'Alice')",
                "INSERT INTO orders VALUES (1, 1, 100)",
                "INSERT INTO orders VALUES (2, 1, 50)"]:
        _run("sql", "query", "--db", str(db_dir), "--query", dml)

    join = _run("sql", "query", "--db", str(db_dir),
                "--query", "SELECT users.name, orders.total FROM users "
                           "JOIN orders ON users.id = orders.user_id")
    assert join.returncode == 0
    assert "Alice" in join.stdout and "100" in join.stdout

    agg = _run("sql", "query", "--db", str(db_dir),
               "--query", "SELECT user_id, SUM(total) FROM orders GROUP BY user_id")
    assert agg.returncode == 0
    assert "150" in agg.stdout
