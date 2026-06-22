from pathlib import Path


def resolve_db_dir(args) -> Path:
    """Resolve the database directory from --db, or raise if it was omitted."""
    db = getattr(args, "db", None)
    if db is None:
        raise ValueError("missing --db PATH (the database directory)")
    return Path(db).resolve()
