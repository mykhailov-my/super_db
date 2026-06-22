import json
from datetime import UTC, datetime
from pathlib import Path

from loguru import logger

from superdb.constants import DEFAULT_PAGE_SIZE, FORMAT_VERSION, MAGIC, META_FILE
from superdb.durability import write_json_atomic
from superdb.errors import InitError, OpenError


def _try_load_meta(db_dir: Path) -> dict | None:
    p = db_dir / META_FILE
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return None


def _write_meta(db_dir: Path) -> None:
    meta = {
        "magic": MAGIC,
        "format_version": FORMAT_VERSION,
        "created_at": datetime.now(UTC).isoformat(),
        "default_page_size": DEFAULT_PAGE_SIZE,
    }
    write_json_atomic(db_dir / META_FILE, meta)
    logger.debug("init: created meta.json path={path}", path=db_dir / META_FILE)


def init_db(db_dir: Path, force: bool = False) -> None:
    db_dir = Path(db_dir).resolve()
    if db_dir.exists():
        meta = _try_load_meta(db_dir)
        is_db = meta is not None and meta.get("magic") == MAGIC
        if is_db and not force:
            raise InitError(f"{db_dir}: already a super_db database (use --force to re-initialize)")
        if not is_db and any(db_dir.iterdir()):
            raise InitError(f"{db_dir}: directory is not empty and not a super_db database")
    else:
        try:
            db_dir.mkdir(parents=True)
        except OSError as e:
            raise InitError(f"{db_dir}: cannot create database directory ({e.strerror})") from e
    _write_meta(db_dir)


def open_db(db_dir: Path) -> dict:
    db_dir = Path(db_dir).resolve()
    if not db_dir.exists():
        raise OpenError(f"{db_dir}: database directory not found")
    meta = _try_load_meta(db_dir)
    if meta is None:
        raise OpenError(f"{db_dir}: not a super_db database (missing meta.json)")
    if meta.get("magic") != MAGIC:
        raise OpenError(f"{db_dir}: not a super_db database (bad magic)")
    if meta.get("format_version") != FORMAT_VERSION:
        raise OpenError(f"{db_dir}: incompatible format version {meta.get('format_version')}")
    logger.debug(
        "open_db: opened path={path} format_version={v}",
        path=db_dir,
        v=meta["format_version"],
    )
    return meta
