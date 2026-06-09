"""Tests for catalog/schema.py (Column, TableMeta, ColumnType, StorageTrack) and storage/rid.py (RID)."""
import dataclasses

import pytest


def test_column_fields():
    from superdb.schema import Column, ColumnType

    col = Column("id", ColumnType.INT, False)
    assert col.name == "id"
    assert col.col_type == ColumnType.INT
    assert col.nullable is False


def test_column_is_frozen():
    from superdb.schema import Column, ColumnType

    col = Column("id", ColumnType.INT, False)
    with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
        col.name = "x"  # type: ignore[misc]


def test_tablemeta_has_all_cat01_fields():
    from superdb.schema import (
        Column,
        ColumnType,
        StorageTrack,
        TableMeta,
    )

    col_a = Column("id", ColumnType.INT, False)
    col_b = Column("name", ColumnType.TEXT, True)
    meta = TableMeta(
        table_id=1,
        name="users",
        columns=(col_a, col_b),
        storage_track=StorageTrack.ROW,
        page_size=4096,
        format_version=1,
    )

    field_names = {f.name for f in dataclasses.fields(TableMeta)}
    assert field_names == {"table_id", "name", "columns", "storage_track", "page_size", "format_version"}


def test_tablemeta_columns_is_tuple():
    from superdb.schema import Column, ColumnType, StorageTrack, TableMeta

    col = Column("id", ColumnType.INT, False)
    meta = TableMeta(
        table_id=1,
        name="t",
        columns=(col,),
        storage_track=StorageTrack.ROW,
        page_size=4096,
        format_version=1,
    )
    assert isinstance(meta.columns, tuple)


def test_columntype_values():
    from superdb.schema import ColumnType

    assert ColumnType.INT.value == "INT"
    assert ColumnType.TEXT.value == "TEXT"


def test_storagetrack_value():
    from superdb.schema import StorageTrack

    assert StorageTrack.ROW.value == "row"


def test_rid_fields_and_frozen():
    from superdb.rid import RID

    rid = RID(2, 5)
    assert rid.page_id == 2
    assert rid.slot_id == 5
    with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
        rid.page_id = 0  # type: ignore[misc]
