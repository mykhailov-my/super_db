import struct

# Page header (D-01): four u16 LE — format_version, slot_count, free_start, free_end
PAGE_HDR = struct.Struct("<HHHH")
# Slot entry (D-04): three u16 LE — offset, length, flags (bit 0 = live/tombstone)
SLOT = struct.Struct("<HHH")

HEADER_SIZE = PAGE_HDR.size      # 8
SLOT_ENTRY_SIZE = SLOT.size      # 6

SLOT_FLAG_LIVE = 1               # flags bit 0 set => live; cleared => tombstone
