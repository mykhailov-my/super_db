from pathlib import Path


def write_file_atomic(path: Path, data: bytes) -> None:
    """Write data to path atomically: tmp -> fsync(tmp) -> os.replace -> fsync(dir).

    Phase 1 stub. Phase 2 provides the full implementation.
    Must be used for all mutating file writes so data survives a process crash.
    """
    raise NotImplementedError


def write_json_atomic(path: Path, obj: object) -> None:
    """Serialize obj as indented JSON and write atomically via write_file_atomic."""
    raise NotImplementedError
