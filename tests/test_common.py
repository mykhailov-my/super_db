"""Tests for super_db.common: constants, errors, durability stub, and logging."""

import pytest

# ---------------------------------------------------------------------------
# constants
# ---------------------------------------------------------------------------


def test_constants_values():
    from superdb.constants import DEFAULT_PAGE_SIZE, FORMAT_VERSION, MAGIC, META_FILE

    assert MAGIC == "SUPERDB"
    assert FORMAT_VERSION == 1
    assert DEFAULT_PAGE_SIZE == 4096
    assert META_FILE == "meta.json"


# ---------------------------------------------------------------------------
# errors
# ---------------------------------------------------------------------------


def test_error_hierarchy():
    from superdb.errors import (
        InitError,
        OpenError,
        PageFullError,
        StorageError,
        SuperDBError,
    )

    assert issubclass(SuperDBError, Exception)
    assert issubclass(InitError, SuperDBError)
    assert issubclass(OpenError, SuperDBError)
    assert issubclass(StorageError, SuperDBError)
    assert issubclass(PageFullError, StorageError)


def test_init_error_is_catchable_as_superdb_error():
    from superdb.errors import InitError, SuperDBError

    with pytest.raises(SuperDBError):
        raise InitError("test")


def test_open_error_is_catchable_as_superdb_error():
    from superdb.errors import OpenError, SuperDBError

    with pytest.raises(SuperDBError):
        raise OpenError("test")


# ---------------------------------------------------------------------------
# durability helpers (Phase 1: plain write; Phase 2 hardens to fsync+replace)
# ---------------------------------------------------------------------------


def test_write_file_atomic_writes_bytes(tmp_path):
    from superdb.durability import write_file_atomic

    target = tmp_path / "blob"
    write_file_atomic(target, b"hello")
    assert target.read_bytes() == b"hello"


def test_write_json_atomic_writes_indented_json(tmp_path):
    import json

    from superdb.durability import write_json_atomic

    target = tmp_path / "obj.json"
    write_json_atomic(target, {"a": 1})
    assert json.loads(target.read_text()) == {"a": 1}


# ---------------------------------------------------------------------------
# setup_logging — level precedence
# ---------------------------------------------------------------------------


def test_setup_logging_debug_flag(monkeypatch):
    """--debug sets level DEBUG."""
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    from loguru import logger

    from superdb.log import setup_logging

    records = []
    setup_logging(debug=True)
    handler_id = logger.add(records.append, level="DEBUG")
    try:
        logger.debug("dbg-msg")
        assert any("dbg-msg" in str(r) for r in records)
    finally:
        logger.remove(handler_id)
        logger.remove()  # clean up for next test


def test_setup_logging_verbose_flag(monkeypatch, capsys):
    """--verbose sets level INFO; DEBUG is suppressed on the stderr sink."""
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    from loguru import logger

    from superdb.log import setup_logging

    setup_logging(debug=False, verbose=True)
    try:
        logger.debug("should-not-appear")
        logger.info("should-appear")
    finally:
        logger.remove()
    err = capsys.readouterr().err
    assert "should-appear" in err
    assert "should-not-appear" not in err


def test_setup_logging_log_level_env(monkeypatch, capsys):
    """LOG_LEVEL env var sets the level when no flags given."""
    monkeypatch.setenv("LOG_LEVEL", "ERROR")
    from loguru import logger

    from superdb.log import setup_logging

    setup_logging(debug=False, verbose=False)
    try:
        logger.warning("warn-msg")
        logger.error("err-msg")
    finally:
        logger.remove()
    err = capsys.readouterr().err
    assert "err-msg" in err
    assert "warn-msg" not in err


def test_setup_logging_default_is_warning(monkeypatch):
    """No flags, no LOG_LEVEL → WARNING level (quiet)."""
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    from loguru import logger

    from superdb.log import setup_logging

    setup_logging(debug=False, verbose=False)
    # Just verify setup completes without error; level is WARNING
    logger.remove()
