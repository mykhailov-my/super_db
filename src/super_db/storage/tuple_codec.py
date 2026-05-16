"""Tuple codec: encode/decode a single record with a leading null bitmap.

Layout (D-07): [bitmap][fields in schema column order]
  bitmap = ceil(n_cols / 8) bytes, little-endian; bit i set => col i is NULL (zero payload).
  INT  -> 4 bytes int32 LE.
  TEXT -> u16 LE length prefix + UTF-8 bytes inline.
"""
import math
import struct
from collections.abc import Iterator
from dataclasses import dataclass

from ..catalog.schema import Column, ColumnType
from ..common.errors import StorageError

I32 = struct.Struct("<i")   # 4-byte int32 LE
U16 = struct.Struct("<H")   # 2-byte uint16 LE (TEXT length prefix)


@dataclass(slots=True, frozen=True)
class FieldSpan:
    """Per-column byte span within a record (offsets relative to record start)."""

    col_name: str
    col_type: ColumnType
    is_null: bool
    byte_offset: int   # bitmap occupies [0, bitmap_width); first field starts at bitmap_width
    byte_length: int   # 0 when is_null
    value: object      # int | str | None


def _bitmap_width(n_cols: int) -> int:
    return math.ceil(n_cols / 8)


def encode_tuple(columns: list[Column], values: list) -> bytes:
    """Encode values into a record byte string following the null-bitmap layout."""
    if len(values) != len(columns):
        raise StorageError(
            f"values length {len(values)} does not match columns length {len(columns)}"
        )
    bm = 0
    for i, v in enumerate(values):
        if v is None:
            bm |= 1 << i
    parts: list[bytes] = [bm.to_bytes(_bitmap_width(len(columns)), "little")]
    for col, v in zip(columns, values, strict=True):
        if v is None:
            continue
        if col.col_type == ColumnType.INT:
            try:
                parts.append(I32.pack(v))
            except struct.error as exc:
                raise StorageError(f"INT value {v} out of int32 range") from exc
        else:
            b = v.encode("utf-8")
            try:
                parts.append(U16.pack(len(b)))
            except struct.error as exc:
                raise StorageError(f"TEXT value too long: {len(b)} bytes exceeds u16") from exc
            parts.append(b)
    return b"".join(parts)


def _walk_fields(record: bytes, columns: list[Column]) -> Iterator[tuple]:
    """Yield (col, value, byte_offset, byte_length) for each column.

    byte_offset is relative to record start; bitmap occupies [0, bm_w).
    byte_length is 0 for NULL columns; pos does NOT advance for NULLs.
    Raises StorageError on any malformed or truncated record.
    """
    n = len(columns)
    bm_w = _bitmap_width(n)
    if len(record) < bm_w:
        raise StorageError("record shorter than null bitmap")
    bm = int.from_bytes(record[0:bm_w], "little")
    pos = bm_w
    for i, col in enumerate(columns):
        if bm & (1 << i):
            yield col, None, pos, 0
        elif col.col_type == ColumnType.INT:
            if pos + 4 > len(record):
                raise StorageError(f"INT field '{col.name}' runs past record end")
            val = I32.unpack(record[pos : pos + 4])[0]
            yield col, val, pos, 4
            pos += 4
        else:  # TEXT
            if pos + 2 > len(record):
                raise StorageError(f"TEXT length prefix for '{col.name}' runs past record end")
            ln = U16.unpack(record[pos : pos + 2])[0]
            if pos + 2 + ln > len(record):
                raise StorageError(
                    f"TEXT field '{col.name}' length {ln} exceeds remaining bytes"
                )
            val = record[pos + 2 : pos + 2 + ln].decode("utf-8")
            yield col, val, pos, 2 + ln
            pos += 2 + ln


def decode_tuple(record: bytes, columns: list[Column]) -> list:
    """Decode a record into a list of Python values (int | str | None) in column order."""
    return [val for _, val, _, _ in _walk_fields(record, columns)]


def describe_tuple(record: bytes, columns: list[Column]) -> list[FieldSpan]:
    """Return one FieldSpan per column with byte_offset and byte_length relative to record start."""
    spans = []
    for col, val, off, ln in _walk_fields(record, columns):
        spans.append(
            FieldSpan(
                col_name=col.name,
                col_type=col.col_type,
                is_null=(val is None),
                byte_offset=off,
                byte_length=ln,
                value=val,
            )
        )
    return spans
