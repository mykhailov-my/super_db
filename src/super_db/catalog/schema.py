from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ColumnType(Enum):
    INT = "INT"
    TEXT = "TEXT"


class StorageTrack(Enum):
    ROW = "row"


@dataclass(slots=True, frozen=True)
class Column:
    name: str
    col_type: ColumnType
    nullable: bool


@dataclass(slots=True, frozen=True)
class TableMeta:
    table_id: int
    name: str
    columns: tuple[Column, ...]
    storage_track: StorageTrack
    page_size: int
    format_version: int
