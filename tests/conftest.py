from pathlib import Path

import pytest


@pytest.fixture
def db_dir(tmp_path: Path) -> Path:
    """An empty directory to initialize a super_db database into."""
    return tmp_path / "mydb"
