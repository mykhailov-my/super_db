"""CLI integration tests for the `row` noun (subprocess)."""

import shutil
import subprocess
from pathlib import Path

import pytest


def _run(*args, **kwargs) -> subprocess.CompletedProcess:
    cli = shutil.which("db-cli")
    if cli is None:
        pytest.skip("db-cli not found on PATH; run tests inside the project venv")
    return subprocess.run([cli, *args], capture_output=True, text=True, **kwargs)


def test_cli_row_insert(tmp_path: Path) -> None:
    # Arrange
    db_dir = tmp_path / "mydb"
    _run("db", "init", "--db", str(db_dir))
    _run(
        "table", "create", "--db", str(db_dir), "--table", "users", "--columns", "id:INT,name:TEXT"
    )

    # Act
    result = _run("row", "insert", "--db", str(db_dir), "--table", "users", "--values", "1,Alice")

    # Assert
    assert result.returncode == 0
    assert "inserted rid=" in result.stdout
    assert "Traceback" not in result.stderr


def test_cli_row_scan_shows_data(tmp_path: Path) -> None:
    # Arrange
    db_dir = tmp_path / "mydb"
    _run("db", "init", "--db", str(db_dir))
    _run(
        "table", "create", "--db", str(db_dir), "--table", "users", "--columns", "id:INT,name:TEXT"
    )
    _run("row", "insert", "--db", str(db_dir), "--table", "users", "--values", "1,Alice")

    # Act
    result = _run("row", "scan", "--db", str(db_dir), "--table", "users")

    # Assert
    assert result.returncode == 0
    assert "Alice" in result.stdout
    assert "Traceback" not in result.stderr


def test_cli_row_get(tmp_path: Path) -> None:
    # Arrange
    db_dir = tmp_path / "mydb"
    _run("db", "init", "--db", str(db_dir))
    _run(
        "table", "create", "--db", str(db_dir), "--table", "users", "--columns", "id:INT,name:TEXT"
    )
    insert_result = _run(
        "row", "insert", "--db", str(db_dir), "--table", "users", "--values", "1,Alice"
    )
    rid = insert_result.stdout.strip().split("=", 1)[1]

    # Act
    result = _run("row", "get", "--db", str(db_dir), "--table", "users", "--rid", rid)

    # Assert
    assert result.returncode == 0
    assert "Alice" in result.stdout
    assert "Traceback" not in result.stderr


def test_cli_row_update(tmp_path: Path) -> None:
    # Arrange
    db_dir = tmp_path / "mydb"
    _run("db", "init", "--db", str(db_dir))
    _run(
        "table", "create", "--db", str(db_dir), "--table", "users", "--columns", "id:INT,name:TEXT"
    )
    insert_result = _run(
        "row", "insert", "--db", str(db_dir), "--table", "users", "--values", "1,Alice"
    )
    rid = insert_result.stdout.strip().split("=", 1)[1]

    # Act
    result = _run(
        "row", "update", "--db", str(db_dir), "--table", "users", "--rid", rid, "--values", "1,Bob"
    )

    # Assert
    assert result.returncode == 0
    assert "updated" in result.stdout
    assert "Traceback" not in result.stderr


def test_cli_row_delete(tmp_path: Path) -> None:
    # Arrange
    db_dir = tmp_path / "mydb"
    _run("db", "init", "--db", str(db_dir))
    _run(
        "table", "create", "--db", str(db_dir), "--table", "users", "--columns", "id:INT,name:TEXT"
    )
    insert_result = _run(
        "row", "insert", "--db", str(db_dir), "--table", "users", "--values", "1,Alice"
    )
    rid = insert_result.stdout.strip().split("=", 1)[1]

    # Act
    result = _run("row", "delete", "--db", str(db_dir), "--table", "users", "--rid", rid)

    # Assert
    assert result.returncode == 0
    assert "deleted rid=" in result.stdout
    assert "Traceback" not in result.stderr


def test_cli_row_scan_empty(tmp_path: Path) -> None:
    # Arrange
    db_dir = tmp_path / "mydb"
    _run("db", "init", "--db", str(db_dir))
    _run(
        "table", "create", "--db", str(db_dir), "--table", "users", "--columns", "id:INT,name:TEXT"
    )

    # Act
    result = _run("row", "scan", "--db", str(db_dir), "--table", "users")

    # Assert
    assert result.returncode == 0
    assert "no rows" in result.stdout


def test_cli_row_bad_values(tmp_path: Path) -> None:
    # Arrange
    db_dir = tmp_path / "mydb"
    _run("db", "init", "--db", str(db_dir))
    _run(
        "table", "create", "--db", str(db_dir), "--table", "users", "--columns", "id:INT,name:TEXT"
    )

    # Act — INT column receives a non-integer value
    result = _run(
        "row", "insert", "--db", str(db_dir), "--table", "users", "--values", "not_an_int,Alice"
    )

    # Assert
    assert result.returncode == 1
    assert "Traceback" not in result.stderr


def test_cli_row_bad_rid(tmp_path: Path) -> None:
    # Arrange
    db_dir = tmp_path / "mydb"
    _run("db", "init", "--db", str(db_dir))
    _run(
        "table", "create", "--db", str(db_dir), "--table", "users", "--columns", "id:INT,name:TEXT"
    )

    # Act
    result = _run("row", "get", "--db", str(db_dir), "--table", "users", "--rid", "xyz")

    # Assert
    assert result.returncode == 1
    assert "Traceback" not in result.stderr


def test_cli_row_hexdump(tmp_path: Path) -> None:
    # Arrange
    db_dir = tmp_path / "mydb"
    _run("db", "init", "--db", str(db_dir))
    _run(
        "table", "create", "--db", str(db_dir), "--table", "users", "--columns", "id:INT,name:TEXT"
    )
    insert_result = _run(
        "row", "insert", "--db", str(db_dir), "--table", "users", "--values", "1,Alice"
    )
    rid = insert_result.stdout.strip().split("=", 1)[1]

    # Act
    result = _run("row", "hexdump", "--db", str(db_dir), "--table", "users", "--rid", rid)

    # Assert
    assert result.returncode == 0
    assert "Traceback" not in result.stderr


def test_cli_row_data_survives_restart(tmp_path: Path) -> None:
    # Arrange
    db_dir = tmp_path / "mydb"
    _run("db", "init", "--db", str(db_dir))
    _run("table", "create", "--db", str(db_dir), "--table", "t", "--columns", "id:INT")
    _run("row", "insert", "--db", str(db_dir), "--table", "t", "--values", "42")

    # Act: fresh CLI invocation = restart (new process, new StorageEngine)
    scan_result = _run("row", "scan", "--db", str(db_dir), "--table", "t")

    # Assert
    assert "42" in scan_result.stdout
