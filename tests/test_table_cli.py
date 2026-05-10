"""CLI integration tests for the `table` noun.

Tests exercise the installed db-cli entry point via subprocess,
mirroring the pattern in tests/test_cli.py.
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


def test_cli_table_create(tmp_path: Path) -> None:
    # Arrange
    db_dir = tmp_path / "mydb"
    _run("db", "init", "--db", str(db_dir))

    # Act
    result = _run("table", "create", "--db", str(db_dir), "--table", "users", "--columns", "id:INT,name:TEXT")

    # Assert
    assert result.returncode == 0
    assert (db_dir / "catalog.json").exists()
    assert (db_dir / "users.tbl").exists()


def test_cli_table_create_bad_spec(tmp_path: Path) -> None:
    # Arrange
    db_dir = tmp_path / "mydb"
    _run("db", "init", "--db", str(db_dir))

    # Act — missing colon in column spec
    result = _run("table", "create", "--db", str(db_dir), "--table", "users", "--columns", "idINT")

    # Assert
    assert result.returncode == 1
    assert "Traceback" not in result.stderr


def test_cli_table_create_bad_type(tmp_path: Path) -> None:
    # Arrange
    db_dir = tmp_path / "mydb"
    _run("db", "init", "--db", str(db_dir))

    # Act — FLOAT is not a supported type
    result = _run("table", "create", "--db", str(db_dir), "--table", "users", "--columns", "id:FLOAT")

    # Assert
    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    assert "FLOAT" in result.stderr


def test_cli_table_list(tmp_path: Path) -> None:
    # Arrange
    db_dir = tmp_path / "mydb"
    _run("db", "init", "--db", str(db_dir))
    _run("table", "create", "--db", str(db_dir), "--table", "users", "--columns", "id:INT")
    _run("table", "create", "--db", str(db_dir), "--table", "orders", "--columns", "id:INT,total:TEXT")

    # Act
    result = _run("table", "list", "--db", str(db_dir))

    # Assert
    assert result.returncode == 0
    assert "users" in result.stdout
    assert "orders" in result.stdout


def test_cli_table_describe(tmp_path: Path) -> None:
    # Arrange
    db_dir = tmp_path / "mydb"
    _run("db", "init", "--db", str(db_dir))
    _run("table", "create", "--db", str(db_dir), "--table", "users", "--columns", "id:INT,name:TEXT")

    # Act
    result = _run("table", "describe", "--db", str(db_dir), "--table", "users")

    # Assert
    assert result.returncode == 0
    assert "id" in result.stdout
    assert "INT" in result.stdout
    assert "name" in result.stdout
    assert "TEXT" in result.stdout


def test_cli_table_drop(tmp_path: Path) -> None:
    # Arrange
    db_dir = tmp_path / "mydb"
    _run("db", "init", "--db", str(db_dir))
    _run("table", "create", "--db", str(db_dir), "--table", "users", "--columns", "id:INT")

    # Act
    result = _run("table", "drop", "--db", str(db_dir), "--table", "users")

    # Assert
    assert result.returncode == 0
    list_result = _run("table", "list", "--db", str(db_dir))
    assert "users" not in list_result.stdout


def test_cli_table_drop_missing(tmp_path: Path) -> None:
    # Arrange
    db_dir = tmp_path / "mydb"
    _run("db", "init", "--db", str(db_dir))

    # Act
    result = _run("table", "drop", "--db", str(db_dir), "--table", "ghost")

    # Assert
    assert result.returncode == 1
    assert "Traceback" not in result.stderr
