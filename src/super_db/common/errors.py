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
