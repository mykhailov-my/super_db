"""Tests for Phase 8 visualization methods.

PlainRenderer: deterministic text assertions.
RichRenderer: smoke-only (renders without raising).
No golden ANSI string assertions on RichRenderer output.
"""


from superdb.plain_renderer import PlainRenderer
from superdb.rich_renderer import RichRenderer
from superdb.schema import Column, ColumnType, StorageTrack, TableMeta

# ---------------------------------------------------------------------------
# Helper: build a minimal TableMeta without a real database
# ---------------------------------------------------------------------------

def _make_meta() -> TableMeta:
    return TableMeta(
        table_id=1,
        name="users",
        columns=(
            Column("id", ColumnType.INT, nullable=False),
            Column("name", ColumnType.TEXT, nullable=True),
        ),
        storage_track=StorageTrack.ROW,
        page_size=4096,
        format_version=1,
    )


# ---------------------------------------------------------------------------
# PlainRenderer assertion tests
# ---------------------------------------------------------------------------

def test_render_rows_plain(capsys) -> None:
    # Arrange
    meta = _make_meta()
    renderer = PlainRenderer()

    # Act
    renderer.render_rows(meta, [("0:0", {"id": 1, "name": "Alice"})])
    out, err = capsys.readouterr()

    # Assert — tab-separated, RID first
    assert "RID\tid\tname" in out
    assert "0:0\t1\tAlice" in out
    assert err == ""


def test_render_rows_empty_plain(capsys) -> None:
    # Arrange
    meta = _make_meta()
    renderer = PlainRenderer()

    # Act
    renderer.render_rows(meta, [])
    out, _ = capsys.readouterr()

    # Assert
    assert "no rows" in out


def test_render_rows_null_plain(capsys) -> None:
    # Arrange — name is nullable, supply None
    meta = _make_meta()
    renderer = PlainRenderer()

    # Act
    renderer.render_rows(meta, [("0:1", {"id": 2, "name": None})])
    out, err = capsys.readouterr()

    # Assert
    assert "NULL" in out
    assert err == ""


def test_render_hexdump_plain(capsys) -> None:
    # Arrange
    renderer = PlainRenderer()
    raw = b"\x01\x00\x00\x00\x05hello"

    # Act
    renderer.render_hexdump(
        "0:0",
        raw,
        [("id", 0, 4, "INT"), ("name", 4, 7, "TEXT")],
        (0, 1),
    )
    out, _ = capsys.readouterr()

    # Assert — first hex row offset marker
    assert "0x0000" in out


def test_render_btree_plain(capsys) -> None:
    # Arrange — a minimal single-leaf tree
    renderer = PlainRenderer()
    nodes = [
        {
            "type": "leaf",
            "keys": [1, 2],
            "rids": ["0:0", "0:1"],
            "next_leaf": None,
        }
    ]

    # Act
    renderer.render_btree("users", "id", nodes)
    out, _ = capsys.readouterr()

    # Assert
    assert "[leaf]" in out


def test_render_btree_internal_plain(capsys) -> None:
    # Arrange — internal node with two leaf children
    renderer = PlainRenderer()
    nodes = [
        {
            "type": "internal",
            "keys": [5],
            "children": [
                {"type": "leaf", "keys": [1, 3], "rids": ["0:0", "0:1"], "next_leaf": None},
                {"type": "leaf", "keys": [5, 8], "rids": ["0:2", "0:3"], "next_leaf": None},
            ],
        }
    ]

    # Act
    renderer.render_btree("users", "id", nodes)
    out, _ = capsys.readouterr()

    # Assert
    assert "[internal]" in out
    assert "[leaf]" in out


# ---------------------------------------------------------------------------
# RichRenderer smoke tests — just must not raise
# ---------------------------------------------------------------------------

def test_render_rows_rich_no_error() -> None:

    meta = _make_meta()
    RichRenderer().render_rows(meta, [("0:0", {"id": 1, "name": "Alice"})])
    # No assertion — must not raise


def test_render_schema_rich_no_error() -> None:

    meta = _make_meta()
    RichRenderer().render_schema(meta)


def test_render_page_rich_no_error() -> None:

    RichRenderer().render_page(
        table_name="users",
        page_id=0,
        page_size=4096,
        header_bytes=8,
        slot_count=1,
        slots=[(0, 4084, 12, True)],
        free_space_start=14,
        free_space_end=4084,
    )


def test_render_hexdump_rich_no_error() -> None:

    raw = b"\x01\x00\x00\x00\x05hello"
    RichRenderer().render_hexdump(
        "0:0",
        raw,
        [("id", 0, 4, "INT"), ("name", 4, 7, "TEXT")],
        (0, 1),
    )


def test_render_btree_rich_no_error() -> None:

    nodes = [{"type": "leaf", "keys": [1], "rids": ["0:0"], "next_leaf": None}]
    RichRenderer().render_btree("users", "id", nodes)
