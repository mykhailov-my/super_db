"""Tests for the hardened write_file_atomic / write_json_atomic (CAT-08)."""

import json
import tempfile as _tempfile

import pytest

from superdb.durability import write_file_atomic, write_json_atomic


def test_write_file_atomic_round_trip(tmp_path):
    # Arrange
    target = tmp_path / "blob"
    data = b"\x00\x01\x02hello\xff"

    # Act
    write_file_atomic(target, data)

    # Assert
    assert target.read_bytes() == data


def test_write_json_atomic_round_trip(tmp_path):
    # Arrange
    target = tmp_path / "c.json"
    obj = {"version": 1, "next_table_id": 1, "tables": []}

    # Act
    write_json_atomic(target, obj)

    # Assert
    assert json.loads(target.read_text()) == obj


def test_no_temp_files_left_behind(tmp_path):
    # Arrange
    target = tmp_path / "data.bin"

    # Act
    write_file_atomic(target, b"payload")

    # Assert — hardened impl must rename temp away; stub leaves no .tmp either
    # but the dir= assertion in test_temp_in_same_dir_supports_replace is the
    # real discriminator
    assert list(tmp_path.glob("*.tmp")) == []


def test_overwrite_replaces_existing(tmp_path):
    # Arrange
    target = tmp_path / "file.bin"
    write_file_atomic(target, b"old")

    # Act
    write_file_atomic(target, b"new")

    # Assert
    assert target.read_bytes() == b"new"
    # Only one file with that name
    assert len(list(tmp_path.glob("file.bin"))) == 1


def test_temp_in_same_dir_supports_replace(tmp_path, monkeypatch):
    """Hardened impl must pass dir=path.parent to mkstemp (same-filesystem
    invariant so os.replace stays atomic)."""
    # Arrange
    recorded_dirs: list = []
    real_mkstemp = _tempfile.mkstemp

    def capturing_mkstemp(*args, **kwargs):
        recorded_dirs.append(kwargs.get("dir"))
        return real_mkstemp(*args, **kwargs)

    import superdb.durability as _dur

    monkeypatch.setattr(_dur.tempfile, "mkstemp", capturing_mkstemp)

    target = tmp_path / "out.bin"

    # Act
    write_file_atomic(target, b"test")

    # Assert — the stub uses path.write_bytes and never calls mkstemp, so
    # recorded_dirs will be empty and the assertion fails (expected RED)
    assert len(recorded_dirs) == 1
    assert recorded_dirs[0] == tmp_path


def test_no_temp_left_on_write_failure(tmp_path, monkeypatch):
    # If os.write raises, the temp file must be cleaned up and any existing
    # target left intact (the atomic-write contract under failure).
    target = tmp_path / "data.json"
    write_file_atomic(target, b"original")

    import superdb.durability as _dur

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(_dur.os, "write", boom)
    with pytest.raises(OSError):
        write_file_atomic(target, b"new")

    assert target.read_bytes() == b"original"
    assert not list(tmp_path.glob("*.tmp"))
