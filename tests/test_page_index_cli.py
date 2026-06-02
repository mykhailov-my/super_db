"""CLI integration tests for the `page` and `index` nouns (subprocess)."""
import shutil
import subprocess
from pathlib import Path

import pytest


def _run(*args, **kwargs) -> subprocess.CompletedProcess:
    cli = shutil.which("db-cli")
    if cli is None:
        pytest.skip("db-cli not found on PATH; run tests inside the project venv")
    return subprocess.run([cli, *args], capture_output=True, text=True, **kwargs)


def test_cli_page_show(tmp_path: Path) -> None:
    # Arrange
    db_dir = tmp_path / "mydb"
    _run("db", "init", "--db", str(db_dir))
    _run("table", "create", "--db", str(db_dir), "--table", "t", "--columns", "id:INT")
    _run("row", "insert", "--db", str(db_dir), "--table", "t", "--values", "1")

    # Act
    result = _run("page", "show", "--db", str(db_dir), "--table", "t", "--page", "0")

    # Assert
    assert result.returncode == 0
    assert "Header" in result.stdout
    assert "Traceback" not in result.stderr


def test_cli_page_show_out_of_range(tmp_path: Path) -> None:
    # Arrange
    db_dir = tmp_path / "mydb"
    _run("db", "init", "--db", str(db_dir))
    _run("table", "create", "--db", str(db_dir), "--table", "t", "--columns", "id:INT")
    _run("row", "insert", "--db", str(db_dir), "--table", "t", "--values", "1")

    # Act — page 99 does not exist
    result = _run("page", "show", "--db", str(db_dir), "--table", "t", "--page", "99")

    # Assert
    assert result.returncode == 1
    assert "Traceback" not in result.stderr


def test_cli_index_show_no_index(tmp_path: Path) -> None:
    # Arrange — table exists but no .idx file has been created
    db_dir = tmp_path / "mydb"
    _run("db", "init", "--db", str(db_dir))
    _run("table", "create", "--db", str(db_dir), "--table", "t", "--columns", "id:INT")
    _run("row", "insert", "--db", str(db_dir), "--table", "t", "--values", "1")

    # Act
    result = _run("index", "show", "--db", str(db_dir), "--table", "t")

    # Assert — success (not an error), informational message
    assert result.returncode == 0
    assert "no index found" in result.stdout
    assert "Traceback" not in result.stderr
