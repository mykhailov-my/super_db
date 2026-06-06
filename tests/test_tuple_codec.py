"""Tests for storage/tuple_codec.py: golden-byte, round-trip, FieldSpan, malformed-buffer."""
import pytest

from super_db.catalog.schema import Column, ColumnType
from super_db.common.errors import StorageError
from super_db.storage.tuple_codec import FieldSpan, decode_tuple, describe_tuple, encode_tuple

# Two-column schema reused by the golden-byte tests (matches RESEARCH golden sequences).
# Both columns are nullable so the NULL-bitmap cases below are valid records.
SCHEMA = [
    Column("id", ColumnType.INT, True),
    Column("name", ColumnType.TEXT, True),
]


def test_golden_bytes_no_nulls():
    # Case A: id=1, name='ab' — no nulls, bitmap=0x00
    assert encode_tuple(SCHEMA, [1, "ab"]).hex() == "000100000002006162"


def test_golden_bytes_null_text():
    # Case B: id=7, name=NULL — bitmap bit 1 set (0x02), id has payload, name has zero bytes
    assert encode_tuple(SCHEMA, [7, None]).hex() == "0207000000"


def test_golden_bytes_null_int():
    # Case C: id=NULL, name='hi' — bitmap bit 0 set (0x01), id has zero bytes, name has payload
    assert encode_tuple(SCHEMA, [None, "hi"]).hex() == "0102006869"


def test_roundtrip_all_combinations():
    cases = [
        [1, "ab"],
        [7, None],
        [None, "hi"],
        [None, None],
        [0, ""],
    ]
    for vals in cases:
        assert decode_tuple(encode_tuple(SCHEMA, vals), SCHEMA) == vals


def test_empty_text():
    # Empty string: 2-byte zero length prefix, no payload bytes
    vals = [42, ""]
    assert decode_tuple(encode_tuple(SCHEMA, vals), SCHEMA) == vals


def test_fieldspan_offsets():
    # id=1, name='ab': bitmap=1 byte at offset 0; id at offset 1 (len 4); name at offset 5 (len 4)
    spans = describe_tuple(encode_tuple(SCHEMA, [1, "ab"]), SCHEMA)
    assert spans[0].byte_offset == 1
    assert spans[0].byte_length == 4
    assert not spans[0].is_null
    assert spans[1].byte_offset == 5
    assert spans[1].byte_length == 4  # 2 (len prefix) + 2 (utf-8 'ab')
    assert not spans[1].is_null


def test_fieldspan_null_length():
    # NULL column must have byte_length=0 and is_null=True
    spans = describe_tuple(encode_tuple(SCHEMA, [7, None]), SCHEMA)
    assert spans[1].byte_length == 0
    assert spans[1].is_null


def test_fieldspan_is_fieldspan_type():
    spans = describe_tuple(encode_tuple(SCHEMA, [1, "ab"]), SCHEMA)
    assert isinstance(spans[0], FieldSpan)
    assert isinstance(spans[1], FieldSpan)


def test_nine_column_bitmap_width():
    # 9 columns → ceil(9/8)=2 byte bitmap; first non-null INT field starts at offset 2
    cols = [Column(f"c{i}", ColumnType.INT, True) for i in range(9)]
    vals = [i for i in range(9)]
    spans = describe_tuple(encode_tuple(cols, vals), cols)
    assert spans[0].byte_offset == 2


def test_int_out_of_range_raises():
    schema = [Column("n", ColumnType.INT, False)]
    with pytest.raises(StorageError):
        encode_tuple(schema, [2**31])


def test_int_negative_out_of_range_raises():
    schema = [Column("n", ColumnType.INT, False)]
    with pytest.raises(StorageError):
        encode_tuple(schema, [-(2**31) - 1])


def test_truncated_text_raises():
    # bitmap=0x00, TEXT length prefix claims 1000 bytes but only 'hi' follows
    text_schema = [Column("t", ColumnType.TEXT, False)]
    bad = bytes.fromhex("00") + (1000).to_bytes(2, "little") + b"hi"
    with pytest.raises(StorageError):
        decode_tuple(bad, text_schema)


def test_truncated_int_raises():
    # Record with only 3 bytes of INT payload (needs 4)
    int_schema = [Column("n", ColumnType.INT, False)]
    bad = bytes.fromhex("00") + b"\x01\x00\x00"  # bitmap + only 3 bytes
    with pytest.raises(StorageError):
        decode_tuple(bad, int_schema)


def test_record_shorter_than_bitmap_raises():
    # A 9-column schema needs at least 2 bitmap bytes; give it only 1
    cols = [Column(f"c{i}", ColumnType.INT, True) for i in range(9)]
    with pytest.raises(StorageError):
        decode_tuple(b"\x00", cols)


def test_mismatched_lengths_raises():
    with pytest.raises(StorageError):
        encode_tuple(SCHEMA, [1])  # only 1 value for 2-column schema


def test_all_null_round_trip():
    vals = [None, None]
    assert decode_tuple(encode_tuple(SCHEMA, vals), SCHEMA) == vals


def test_text_non_str_value_raises_storage_error():
    # A TEXT column given a non-str value must raise StorageError, not a raw
    # AttributeError from .encode, mirroring the INT-range guard.
    schema = [Column("name", ColumnType.TEXT, True)]
    with pytest.raises(StorageError, match="must be str"):
        encode_tuple(schema, [123])


def test_not_null_column_with_none_raises():
    schema = [Column("id", ColumnType.INT, False)]
    with pytest.raises(StorageError, match="NOT NULL"):
        encode_tuple(schema, [None])


def test_nullable_column_with_none_round_trips():
    schema = [Column("name", ColumnType.TEXT, True)]
    assert decode_tuple(encode_tuple(schema, [None]), schema) == [None]


def test_invalid_utf8_text_raises_storage_error():
    # A TEXT payload with non-UTF-8 bytes must surface StorageError, not a raw
    # UnicodeDecodeError, so malformed pages are caught by the storage contract.
    schema = [Column("name", ColumnType.TEXT, True)]
    # bitmap 0x00 (not null), u16 length 2, then two invalid bytes
    record = b"\x00" + b"\x02\x00" + b"\xff\xfe"
    with pytest.raises(StorageError, match="invalid UTF-8"):
        decode_tuple(record, schema)
