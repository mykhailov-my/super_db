import json
from pathlib import Path


def write_file_atomic(path: Path, data: bytes) -> None:
    """Write data to path so it survives a process crash.

    Phase 1: plain write. Phase 2 hardens the body to tmp -> fsync(tmp) ->
    os.replace -> fsync(dir) without changing this signature, so callers get
    durability for free. All mutating file writes must route through here.
    """
    path.write_bytes(data)


def write_json_atomic(path: Path, obj: object) -> None:
    """Serialize obj as indented JSON and write it via write_file_atomic."""
    write_file_atomic(path, json.dumps(obj, indent=2).encode("utf-8"))
