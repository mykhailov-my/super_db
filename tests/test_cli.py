"""CLI behaviour tests — exercise the installed db-cli entry point via subprocess.

The console script is resolved via shutil.which("db-cli") so tests run against
the venv-installed binary, the same binary a user would call.
"""
import shutil
import subprocess
import sys
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
