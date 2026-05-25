class SuperDBError(Exception):
    """Base class for all super_db runtime errors (exit code 1)."""


class InitError(SuperDBError):
    """Raised when `db init` cannot create or re-initialize a database."""


class OpenError(SuperDBError):
    """Raised when an existing path is not a valid/compatible super_db database."""


class StorageError(SuperDBError):
    """Raised for page-level or tuple-codec failures (malformed buffer, bad layout)."""


class PageFullError(StorageError):
    """Raised when a record does not fit in a page's available free space."""


class RecordNotFoundError(StorageError):
    """Raised when a RID addresses no live record (out-of-range, tombstoned, or never written)."""


class IndexKeyNotFoundError(StorageError):
    """Raised when search(key) finds no matching entry in the B+Tree index."""


class DuplicateKeyError(StorageError):
    """Raised when insert(key) finds the key already present (unique index, D-08)."""


class IndexKeyTooLongError(StorageError):
    """Raised when a TEXT key exceeds the index's text_key_cap bytes (D-06)."""
