import json
import os
import tempfile
from pathlib import Path


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
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)
    dfd = os.open(str(parent), os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


def write_json_atomic(path: Path, obj: object) -> None:
    """Serialize obj as indented JSON and write it via write_file_atomic."""
    write_file_atomic(path, json.dumps(obj, indent=2).encode("utf-8"))
