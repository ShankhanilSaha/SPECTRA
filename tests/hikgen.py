"""Test-only Hikvision volume builder, written from Han, Jeong & Lee (ICDF2C 2015).

Source: "Analysis of the HIKVISION DVR File System", LNICST 157, pp. 189-199,
doi:10.1007/978-3-319-25512-5_13 — peer-reviewed and openly available.

SCOPE WARNING: this is a *spec-conformance* fixture written by the parser author, the same
caveat `dhavgen.py` carries. It proves the parser implements the published layout; it
cannot prove the layout is what a real recorder writes. Only a Tier R image from hardware
(doc 6 §3.2) settles that, and until one exists the structural claims stay labelled per
doc 4 §12 rows 1-2.

DIVERGENCE FROM THE TIER S CORPUS: `tools/make_corpus.py` S-04/S-05 place the master magic
at offset 0, the two tree offsets as `<QQ` at offset 32, and the `HIKBTREE` magic at
tree+0. The paper places them at 0x210, 0x298/0x2A8, and tree+0x10. The two layouts share
only the magic strings. This builder follows the paper; see `test_hikvision.py` for the
test that keeps that conflict visible.
"""

from __future__ import annotations

import struct
from datetime import UTC, datetime

MASTER_SECTOR = 0x200
MASTER_MAGIC = b"HIKVISION@HANGZHOU"
TREE_MAGIC = b"HIKBTREE"
DEFAULT_VERSION = b"HIK.2011.03.08"

PAGE_SIZE = 0x1000
ENTRIES_AT = 0x60
ENTRY_STRIDE = 48
ENTRIES_PER_PAGE = (PAGE_SIZE - ENTRIES_AT) // ENTRY_STRIDE  # 83

END_OF_CHAIN = 0xFFFF_FFFF_FFFF_FFFF
UNUSED_STATUS = 0xFFFF_FFFF_FFFF_FFFF
RECORDING_NOW = 0x7FFF_FFFF  # start_time sentinel: in progress, not 2038

GIB = 1024 * 1024 * 1024


def unix32(moment: datetime) -> int:
    return int(moment.replace(tzinfo=UTC).timestamp())


def entry(
    *,
    channel: int,
    start: int,
    end: int,
    block_offset: int,
    status: int = 0,
) -> bytes:
    """One 48-byte data-block entry. `channel` is BIG-endian; everything else is little."""
    buf = bytearray(ENTRY_STRIDE)
    struct.pack_into("<Q", buf, 0x08, status)
    struct.pack_into(">H", buf, 0x10, channel)  # the one big-endian field
    struct.pack_into("<I", buf, 0x18, start)
    struct.pack_into("<I", buf, 0x1C, end)
    struct.pack_into("<Q", buf, 0x20, block_offset)
    return bytes(buf)


def page(entries: list[bytes], *, next_page: int = END_OF_CHAIN) -> bytes:
    """One 4 KiB HIKBTREE page holding up to 83 entries."""
    if len(entries) > ENTRIES_PER_PAGE:
        raise ValueError(f"{len(entries)} entries exceeds {ENTRIES_PER_PAGE} per page")
    buf = bytearray(PAGE_SIZE)
    struct.pack_into("<I", buf, 0x10, len(entries))
    struct.pack_into("<Q", buf, 0x20, next_page)
    for i, item in enumerate(entries):
        at = ENTRIES_AT + i * ENTRY_STRIDE
        buf[at : at + ENTRY_STRIDE] = item
    return bytes(buf)


def tree(first_page_offset: int, *, created: int = 0) -> bytes:
    """The HIKBTREE header block that precedes the page chain."""
    buf = bytearray(PAGE_SIZE)
    buf[0x10 : 0x10 + len(TREE_MAGIC)] = TREE_MAGIC
    struct.pack_into("<I", buf, 0x3C, created)
    struct.pack_into("<Q", buf, 0x40, 0)  # footer offset, unused by the parser
    struct.pack_into("<Q", buf, 0x50, first_page_offset)
    struct.pack_into("<Q", buf, 0x58, first_page_offset)
    return bytes(buf)


def master_sector(
    *,
    capacity: int,
    data_area: int,
    block_size: int,
    block_count: int,
    tree1: tuple[int, int],
    tree2: tuple[int, int],
    version: bytes = DEFAULT_VERSION,
    init_time: int = 0,
    syslog: tuple[int, int] = (0, 0),
) -> bytes:
    """The 256-byte master sector, returned already positioned for offset 0x200."""
    buf = bytearray(0x100)
    buf[0x10 : 0x10 + len(MASTER_MAGIC)] = MASTER_MAGIC
    buf[0x30 : 0x30 + len(version)] = version
    struct.pack_into("<Q", buf, 0x48, capacity)
    struct.pack_into("<Q", buf, 0x60, syslog[0])
    struct.pack_into("<Q", buf, 0x68, syslog[1])
    struct.pack_into("<Q", buf, 0x78, data_area)
    struct.pack_into("<Q", buf, 0x88, block_size)
    struct.pack_into("<I", buf, 0x90, block_count)
    struct.pack_into("<Q", buf, 0x98, tree1[0])
    struct.pack_into("<I", buf, 0xA0, tree1[1])
    struct.pack_into("<Q", buf, 0xA8, tree2[0])
    struct.pack_into("<I", buf, 0xB0, tree2[1])
    struct.pack_into("<I", buf, 0xF0, init_time)
    return bytes(buf)


def volume(
    *,
    recordings: list[tuple[int, datetime, datetime]] | None = None,
    block_size: int = 64 * 1024,
    size: int | None = None,
    corrupt_primary: bool = False,
    orphan_indices: tuple[int, ...] = (),
    version: bytes = DEFAULT_VERSION,
    init_time: datetime | None = None,
) -> bytes:
    """Build a small but structurally faithful Hikvision volume.

    `recordings` is `(channel, start, end)`; `orphan_indices` marks those entries unused
    (status all-FF) while leaving their data blocks intact — the T2 orphan case.
    Real units use 1 GiB blocks; the default here is 64 KiB so fixtures stay small.
    """
    recordings = recordings or [
        (1, datetime(2026, 3, 5, 14, 0, 0), datetime(2026, 3, 5, 14, 30, 0)),
        (2, datetime(2026, 3, 5, 14, 5, 0), datetime(2026, 3, 5, 14, 35, 0)),
    ]

    tree1_at, tree2_at = 0x10000, 0x20000
    page1_at, page2_at = 0x11000, 0x21000
    data_area = 0x30000
    total = size or (data_area + block_size * (len(recordings) + 1))
    img = bytearray(total)

    entries = [
        entry(
            channel=ch,
            start=unix32(start),
            end=unix32(end),
            block_offset=data_area + i * block_size,
            status=UNUSED_STATUS if i in orphan_indices else 0,
        )
        for i, (ch, start, end) in enumerate(recordings)
    ]

    img[MASTER_SECTOR : MASTER_SECTOR + 0x100] = master_sector(
        capacity=total,
        data_area=data_area,
        block_size=block_size,
        block_count=len(recordings) + 1,
        tree1=(tree1_at, PAGE_SIZE),
        tree2=(tree2_at, PAGE_SIZE),
        version=version,
        init_time=unix32(init_time) if init_time else 0,
    )

    for tree_at, page_at in ((tree1_at, page1_at), (tree2_at, page2_at)):
        img[tree_at : tree_at + PAGE_SIZE] = tree(page_at)
        img[page_at : page_at + PAGE_SIZE] = page(entries)

    if corrupt_primary:
        img[tree1_at : tree1_at + 0x40] = b"\x00" * 0x40

    # Each data block opens with MPEG-PS, which is what the payload actually looks like
    # on a Hikvision volume (paper §4).
    for i in range(len(recordings)):
        at = data_area + i * block_size
        blob = ps_stream(packs=3)
        img[at : at + len(blob)] = blob

    return bytes(img)


def pack_header(stuffing: int = 0) -> bytes:
    """A structurally valid MPEG-2 pack header, marker bits and all.

    The parser accepts a pack only if these markers hold *and* a start code follows where
    the stuffing length says it will, so a fixture has to be honest about both.
    """
    if not 0 <= stuffing <= 7:
        raise ValueError("stuffing length is 3 bits")
    buf = bytearray(14 + stuffing)
    buf[0:4] = b"\x00\x00\x01\xba"
    buf[4] = 0x44  # '01' + SCR bits + marker
    buf[6] = 0x04  # marker
    buf[8] = 0x04  # marker
    buf[9] = 0x01  # marker
    buf[12] = 0x03  # two markers
    buf[13] = stuffing
    return bytes(buf)


def pes_packet(payload: bytes, stream_id: int = 0xE0) -> bytes:
    """A minimal PES packet carrying `payload`."""
    return b"\x00\x00\x01" + bytes([stream_id]) + struct.pack(">H", len(payload)) + payload


def ps_stream(*, packs: int = 3, idr_first: bool = True) -> bytes:
    """A short program stream: pack header, PES, repeated. First slice is an IDR."""
    out = bytearray()
    for i in range(packs):
        nal = b"\x00\x00\x00\x01" + bytes([0x65 if (i == 0 and idr_first) else 0x41])
        out += pack_header()
        out += pes_packet(nal + bytes(64))
    return bytes(out)
