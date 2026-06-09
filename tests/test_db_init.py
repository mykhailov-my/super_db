import json
from pathlib import Path

import pytest

from superdb.database import init_db, open_db
from superdb.errors import InitError, OpenError


def test_init_creates_meta(db_dir: Path) -> None:
    init_db(db_dir)
    meta_path = db_dir / "meta.json"
    assert meta_path.exists()
    meta = json.loads(meta_path.read_text())
    assert meta["magic"] == "SUPERDB"
    assert meta["format_version"] == 1
    assert meta["default_page_size"] == 4096
    assert "created_at" in meta


def test_init_already_init(db_dir: Path) -> None:
    init_db(db_dir)
    with pytest.raises(InitError, match="already a super_db database"):
        init_db(db_dir)


def test_init_force(db_dir: Path) -> None:
    init_db(db_dir)
    init_db(db_dir, force=True)
    meta = json.loads((db_dir / "meta.json").read_text())
    assert meta["magic"] == "SUPERDB"


def test_init_nonempty_dir(tmp_path: Path) -> None:
    d = tmp_path / "nonempty"
    d.mkdir()
    (d / "some_file.txt").write_text("data")
    with pytest.raises(InitError, match="not empty"):
        init_db(d)


def test_open_db_valid(db_dir: Path) -> None:
    init_db(db_dir)
    meta = open_db(db_dir)
    assert meta["magic"] == "SUPERDB"


def test_open_db_not_found(db_dir: Path) -> None:
    with pytest.raises(OpenError, match="not found"):
        open_db(db_dir)


def test_open_missing_meta(tmp_path: Path) -> None:
    d = tmp_path / "empty_db"
    d.mkdir()
    with pytest.raises(OpenError, match="missing meta.json"):
        open_db(d)


def test_open_bad_magic(tmp_path: Path) -> None:
    d = tmp_path / "badmagic"
    d.mkdir()
    (d / "meta.json").write_text(json.dumps({"magic": "NOPE", "format_version": 1}))
    with pytest.raises(OpenError, match="bad magic"):
        open_db(d)


def test_open_incompatible_version(tmp_path: Path) -> None:
    d = tmp_path / "badver"
    d.mkdir()
    (d / "meta.json").write_text(json.dumps({"magic": "SUPERDB", "format_version": 999}))
    with pytest.raises(OpenError, match="incompatible format version"):
        open_db(d)


def test_open_malformed_meta(tmp_path: Path) -> None:
    d = tmp_path / "malformed"
    d.mkdir()
    (d / "meta.json").write_text("{not json")
    with pytest.raises(OpenError):
        open_db(d)


def test_init_unwritable_path_raises_init_error(tmp_path: Path) -> None:
    # Use an existing file as the parent so mkdir fails deterministically.
    blocker = tmp_path / "blocker"
    blocker.write_text("I am a file, not a dir")
    with pytest.raises(InitError, match="cannot create database directory"):
        init_db(blocker / "sub")


def test_open_hostile_binary_meta(tmp_path: Path) -> None:
    # A non-UTF-8 meta.json must surface a clean OpenError, not UnicodeDecodeError.
    d = tmp_path / "hostile"
    d.mkdir()
    (d / "meta.json").write_bytes(b"\xff\xfe corrupt")
    with pytest.raises(OpenError):
        open_db(d)


def test_init_non_empty_wrong_magic_dir(tmp_path: Path) -> None:
    # Valid JSON but wrong magic in a non-empty dir is not a super_db db: refuse.
    d = tmp_path / "other"
    d.mkdir()
    (d / "meta.json").write_text(json.dumps({"magic": "OTHER"}))
    (d / "data.bin").write_bytes(b"important")
    with pytest.raises(InitError, match="not empty and not a super_db database"):
        init_db(d)
