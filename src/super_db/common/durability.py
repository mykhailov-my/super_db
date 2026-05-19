import json
import os
import tempfile
from pathlib import Path

from super_db.common.errors import StorageError


def write_file_atomic(path: Path, data: bytes) -> None:
    """Write data to path so it survives a process crash.

    Pattern: mkstemp(dir=parent) -> write -> fsync(file) -> os.replace -> fsync(dir).
    Placing the temp file in the same directory guarantees the same filesystem,
    keeping os.replace atomic on POSIX. Directory fsync ensures the rename entry
    reaches stable storage before returning.
    """
    parent = path.parent
    fd, tmp = tempfile.mkstemp(dir=parent, suffix=".tmp")
    try:
        try:
            os.write(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    dfd = os.open(str(parent), os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


def write_page(fd: int, page_id: int, page_bytes: bytes, page_size: int) -> None:
    """Write one full page to fd at page_id * page_size, then fsync.

    Caller invariant: len(page_bytes) == page_size (always a full padded page).
    Raises StorageError on a short write (POSIX pwrite should not short-write
    a 4 KiB buffer, but checking the return is the correct pattern).
    """
    if len(page_bytes) != page_size:
        raise StorageError(
            f"write_page expects a full {page_size}B page, got {len(page_bytes)}B"
        )
    n = os.pwrite(fd, page_bytes, page_id * page_size)
    if n != page_size:
        raise StorageError(f"pwrite short write: wrote {n}/{page_size} bytes")
    os.fsync(fd)


def write_json_atomic(path: Path, obj: object) -> None:
    """Serialize obj as indented JSON and write it via write_file_atomic."""
    write_file_atomic(path, json.dumps(obj, indent=2).encode("utf-8"))
