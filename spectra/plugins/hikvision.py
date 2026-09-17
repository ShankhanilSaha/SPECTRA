"""Hikvision family — master sector, HIKBTREE, MPEG-PS payload (FR-22, doc 4 §4).

Structural source: Han, Jaehyeok; Jeong, Doowon; Lee, Sangjin, "Analysis of the HIKVISION
DVR File System", ICDF2C 2015, LNICST 157, pp. 189-199, doi:10.1007/978-3-319-25512-5_13.
Peer-reviewed and openly available, which is what CLAUDE.md §5 rule 14 accepts as a source
for an implementable claim — but doc 4 §12 rows 1-2 stay unverified until a Tier R image
from real hardware exists, so every structural claim below is reported with its source and
the plugin refuses rather than guesses whenever the bytes do not match.

## Layout

    0x200  master sector, 256 bytes
      +0x10  "HIKVISION@HANGZHOU"        the identification signature (abs 0x210)
      +0x30  version ASCII               e.g. "HIK.2011.03.08"
      +0x48  capacity                    u64
      +0x60  /  +0x68   system log offset / size
      +0x78  video data area offset      u64
      +0x88  data block size             u64   (1 GiB on real units)
      +0x90  data block count            u32
      +0x98  /  +0xA0   HIKBTREE primary offset / size
      +0xA8  /  +0xB0   HIKBTREE backup  offset / size
      +0xF0  volume initialisation time  u32 Unix

    HIKBTREE header
      +0x10  "HIKBTREE"
      +0x3C  created time                u32 Unix
      +0x50  page list offset            u64
      +0x58  first page offset           u64

    page, 0x1000 bytes
      +0x10  entry count                 u32
      +0x20  next page offset            u64   (all-FF terminates the chain)
      +0x60  entries, 48-byte stride, 83 per page

    entry, 48 bytes
      +0x08  status                      u64   (0 in use, all-FF free -> T2 orphan)
      +0x10  channel                     u16 BIG-ENDIAN - the one big-endian field
      +0x18  /  +0x1C   start / end time u32 Unix
      +0x20  data block offset           u64

## Two behaviours worth knowing

**Both trees are read.** The format keeps a backup, so the plugin parses primary and
backup, uses whichever validates, and records which one it used. Divergence between them
is not silently resolved — it is a reportable finding (doc 4 §4.2).

**Freed entries still need validating.** A freed entry is not evidence that its block
survived — HDD initialisation and expiry settings do remove footage, and a lagging backup
HIKBTREE can carry entries the primary has already released. So `enumerate()` reports what
the index says and `recover/orphans.py` decides what to trust, exactly as it does for any
other family. How much T2 actually yields here is an open question for hardware (doc 4 §12
rows 1-2), not something this plugin asserts.

**Per-frame time and channel are not in the PS layer.** Recording time comes from the
index entry; the payload carries no dependable per-frame stamp at this layout version. So
`frames()` reports `t_device=None` and `channel=None` rather than inventing either, and a
carved Hikvision fragment is legitimately "channel unknown" (FR-45, CLAUDE.md §5 rule 2).
"""

from __future__ import annotations

import struct
from collections.abc import Iterator
from datetime import UTC, datetime

from spectra.core import es
from spectra.core.models import (
    Codec,
    DeviceTime,
    DiskLayout,
    Extent,
    Frame,
    FrameKind,
    ProbePlan,
    ProbeResult,
    Recording,
    Signature,
    SignatureMatch,
)
from spectra.core.source import EvidenceSource
from spectra.plugins.base import LayoutNotSupported

FAMILY = "hikvision"
SOURCE_NOTE = "structure per Han, Jeong & Lee, ICDF2C 2015 (doc 4 §12 rows 1-2, [R])"

MASTER_SECTOR = 0x200
MASTER_LEN = 0x100
MASTER_MAGIC = b"HIKVISION@HANGZHOU"
MAGIC_AT = 0x10
VERSION_AT = 0x30
VERSION_LEN = 16

TREE_MAGIC = b"HIKBTREE"
TREE_MAGIC_AT = 0x10
PAGE_SIZE = 0x1000
ENTRIES_AT = 0x60
ENTRY_STRIDE = 48
ENTRIES_PER_PAGE = (PAGE_SIZE - ENTRIES_AT) // ENTRY_STRIDE

END_OF_CHAIN = 0xFFFF_FFFF_FFFF_FFFF
FREE_STATUS = 0xFFFF_FFFF_FFFF_FFFF
RECORDING_NOW = 0x7FFF_FFFF

#: Cap on pages walked before we declare the chain hostile (TC-RB-06).
MAX_PAGES = 4096

TIME_ENCODING = "hikvision_unix32"
LAYOUT_2011 = "hik_2011_03_08"
LAYOUT_VERSIONS = (LAYOUT_2011,)

PACK_START = b"\x00\x00\x01\xba"
PACK_HEADER_LEN = 14
#: Stream ids that may legitimately follow a pack header in an MPEG-2 program stream.
_NEXT_START_IDS = frozenset(
    {0xBA, 0xBB, 0xBC, 0xBD, 0xBE, 0xBF, *range(0xC0, 0xE0), *range(0xE0, 0xF0)}
)

_MIN_UNIX = 946_684_800  # 2000-01-01; a Hikvision volume predating this is not plausible
_MAX_UNIX = 4_102_444_800  # 2100-01-01


def _u64(buf: bytes, at: int) -> int:
    return struct.unpack_from("<Q", buf, at)[0]


def _u32(buf: bytes, at: int) -> int:
    return struct.unpack_from("<I", buf, at)[0]


def decode_unix32(raw: int | bytes) -> DeviceTime:
    """Unix seconds to `DeviceTime`. Out-of-range stays `local=None`, never clamped."""
    value = int.from_bytes(raw, "little") if isinstance(raw, bytes) else int(raw)
    local: datetime | None = None
    if _MIN_UNIX <= value < _MAX_UNIX and value != RECORDING_NOW:
        try:
            local = datetime.fromtimestamp(value, UTC).replace(tzinfo=None)
        except (OverflowError, OSError, ValueError):
            local = None
    return DeviceTime(raw=value, encoding=TIME_ENCODING, local=local)


def validate_pack_header(src: EvidenceSource, offset: int) -> int | None:
    """Accept an MPEG-2 pack header only if its marker bits hold *and* a start code follows.

    A bare `00 00 01 BA` in noise is not a pack header. The marker bits alone leave about
    one chance in 256 of a random accept, which over a large image is many false positives,
    so acceptance also requires the next start code to land exactly where the declared
    stuffing length says it will. That chains the check to roughly 1 in 2^32 and is what
    lets this pattern survive the conformance suite's planted-pattern test.
    """
    buf = src.read(offset, PACK_HEADER_LEN + 8)
    if len(buf) < PACK_HEADER_LEN + 4 or buf[:4] != PACK_START:
        return None
    if buf[4] & 0xC0 != 0x40:  # MPEG-2 '01'; MPEG-1 packs use a different layout
        return None
    if not (buf[4] & 0x04 and buf[6] & 0x04 and buf[8] & 0x04 and buf[9] & 0x01):
        return None
    if buf[12] & 0x03 != 0x03:
        return None
    total = PACK_HEADER_LEN + (buf[13] & 0x07)
    nxt = src.read(offset + total, 4)
    if len(nxt) < 4 or nxt[:3] != b"\x00\x00\x01" or nxt[3] not in _NEXT_START_IDS:
        return None
    return total


def _read_master(src: EvidenceSource) -> bytes | None:
    if src.size < MASTER_SECTOR + MASTER_LEN:
        return None
    buf = src.read(MASTER_SECTOR, MASTER_LEN)
    if len(buf) < MASTER_LEN:
        return None
    if buf[MAGIC_AT : MAGIC_AT + len(MASTER_MAGIC)] != MASTER_MAGIC:
        return None
    return buf


def _version_of(master: bytes) -> str:
    raw = master[VERSION_AT : VERSION_AT + VERSION_LEN]
    text = raw.split(b"\x00", 1)[0].decode("ascii", "replace").strip()
    return text


def _layout_for(version: str) -> str | None:
    return LAYOUT_2011 if version.startswith("HIK.2011") else None


class _TreeReader:
    """Walks one HIKBTREE copy. Bounds-checked and cycle-safe by construction."""

    def __init__(self, src: EvidenceSource, offset: int, size: int) -> None:
        self.src = src
        self.offset = offset
        self.size = size

    def valid(self) -> bool:
        if self.offset <= 0 or self.offset + PAGE_SIZE > self.src.size:
            return False
        head = self.src.read(self.offset, PAGE_SIZE)
        if len(head) < PAGE_SIZE:
            return False
        return head[TREE_MAGIC_AT : TREE_MAGIC_AT + len(TREE_MAGIC)] == TREE_MAGIC

    def entries(self) -> list[tuple[int, bytes]]:
        """Every 48-byte entry in the page chain, as `(absolute_offset, raw)`."""
        head = self.src.read(self.offset, PAGE_SIZE)
        page_at = _u64(head, 0x58) or _u64(head, 0x50)
        out: list[tuple[int, bytes]] = []
        seen: set[int] = set()
        pages = 0
        while page_at and page_at != END_OF_CHAIN:
            if pages >= MAX_PAGES or page_at in seen:
                break  # cycle or runaway chain: stop, keep what we have (TC-RB-06)
            if page_at < 0 or page_at + PAGE_SIZE > self.src.size:
                break
            seen.add(page_at)
            pages += 1
            page = self.src.read(page_at, PAGE_SIZE)
            if len(page) < PAGE_SIZE:
                break
            count = min(_u32(page, 0x10), ENTRIES_PER_PAGE)
            for i in range(count):
                at = ENTRIES_AT + i * ENTRY_STRIDE
                out.append((page_at + at, page[at : at + ENTRY_STRIDE]))
            page_at = _u64(page, 0x20)
        return out


def _decode_entry(raw: bytes) -> dict[str, int]:
    return {
        "status": _u64(raw, 0x08),
        "channel": struct.unpack_from(">H", raw, 0x10)[0],
        "start": _u32(raw, 0x18),
        "end": _u32(raw, 0x1C),
        "block": _u64(raw, 0x20),
    }


class HikvisionPlugin:
    """Hikvision-family volume parser (FR-22)."""

    family = FAMILY
    layout_versions = LAYOUT_VERSIONS
    plugin_version = "0.1.0"

    # --- Q1: is this disk mine? ------------------------------------------------------

    @classmethod
    def probe(cls, src: EvidenceSource, plan: ProbePlan) -> ProbeResult | None:  # noqa: ARG003
        master = _read_master(src)
        if master is None:
            return None
        version = _version_of(master)
        layout = _layout_for(version)
        matches = (
            SignatureMatch(
                offset=MASTER_SECTOR + MAGIC_AT,
                data=MASTER_MAGIC,
                description="HIKVISION master sector signature",
            ),
        )
        if layout is None:
            return ProbeResult(
                family=FAMILY,
                layout_version=None,
                confidence=0.9,
                parse_supported=False,
                matches=matches,
                plugin_version=cls.plugin_version,
                note=(
                    f"Hikvision master sector found but layout version {version!r} is not "
                    f"one this plugin knows: carve-only (FR-03). {SOURCE_NOTE}."
                ),
            )
        trees = cls._tree_readers(src, master)
        usable = [name for name, reader in trees if reader.valid()]
        if not usable:
            return ProbeResult(
                family=FAMILY,
                layout_version=None,
                confidence=0.85,
                parse_supported=False,
                matches=matches,
                plugin_version=cls.plugin_version,
                note=(
                    "Hikvision master sector found but neither HIKBTREE copy validates: "
                    f"carve-only (FR-03). {SOURCE_NOTE}."
                ),
            )
        return ProbeResult(
            family=FAMILY,
            layout_version=layout,
            confidence=0.99,
            parse_supported=True,
            matches=matches,
            plugin_version=cls.plugin_version,
            note=f"master sector and {'+'.join(usable)} HIKBTREE validated. {SOURCE_NOTE}.",
        )

    @staticmethod
    def _tree_readers(
        src: EvidenceSource, master: bytes
    ) -> tuple[tuple[str, _TreeReader], ...]:
        return (
            ("primary", _TreeReader(src, _u64(master, 0x98), _u32(master, 0xA0))),
            ("backup", _TreeReader(src, _u64(master, 0xA8), _u32(master, 0xB0))),
        )

    # --- Q2: what shape is the disk? -------------------------------------------------

    def superblock(self, src: EvidenceSource) -> DiskLayout:
        master = _read_master(src)
        if master is None:
            raise LayoutNotSupported("no Hikvision master sector at 0x200")
        version = _version_of(master)
        layout_version = _layout_for(version)
        if layout_version is None:
            raise LayoutNotSupported(
                f"unknown Hikvision layout version {version!r}; carve only (FR-03)"
            )
        trees = self._tree_readers(src, master)
        valid = [(name, r) for name, r in trees if r.valid()]
        if not valid:
            raise LayoutNotSupported(
                "neither HIKBTREE copy validates; carve only (FR-03)"
            )

        used, reader = valid[0]
        notes = [f"HIKBTREE copy used: {used}", SOURCE_NOTE]
        if len(valid) == 1:
            other = "backup" if used == "primary" else "primary"
            notes.append(
                f"the {other} HIKBTREE did not validate — recorded as a finding, "
                "not silently ignored (doc 4 §4.2)"
            )
        else:
            counts = {name: len(r.entries()) for name, r in valid}
            if counts["primary"] != counts["backup"]:
                notes.append(
                    f"HIKBTREE copies diverge: primary has {counts['primary']} entries, "
                    f"backup has {counts['backup']} — divergence is itself a finding"
                )

        index_extents = tuple(
            Extent(r.offset, min(r.size or PAGE_SIZE, max(0, src.size - r.offset)))
            for _, r in valid
            if 0 < r.offset < src.size
        )
        log_offset, log_size = _u64(master, 0x60), _u64(master, 0x68)
        log_extents = (
            (Extent(log_offset, min(log_size, src.size - log_offset)),)
            if 0 < log_offset < src.size and log_size > 0
            else ()
        )
        init_raw = _u32(master, 0xF0)
        return DiskLayout(
            family=FAMILY,
            layout_version=layout_version,
            block_size=_u64(master, 0x88) or None,
            block_count=_u32(master, 0x90) or None,
            blocks_used=len(reader.entries()) or None,
            index_extents=index_extents,
            log_extents=log_extents,
            format_time=decode_unix32(init_raw) if init_raw else None,
            note="; ".join(notes),
        )

    # --- Q3: what does the index claim? ----------------------------------------------

    def enumerate(
        self, src: EvidenceSource, layout: DiskLayout, include_orphans: bool
    ) -> Iterator[Recording]:
        master = _read_master(src)
        if master is None:
            raise LayoutNotSupported("no Hikvision master sector at 0x200")
        trees = self._tree_readers(src, master)
        reader = next((r for _, r in trees if r.valid()), None)
        if reader is None:
            raise LayoutNotSupported("neither HIKBTREE copy validates")

        block_size = layout.block_size or _u64(master, 0x88)
        for _at, raw in reader.entries():
            field = _decode_entry(raw)
            free = field["status"] == FREE_STATUS
            if free and not include_orphans:
                continue
            block = field["block"]
            if block <= 0 or block >= src.size:
                continue
            length = min(block_size or 0, src.size - block)
            if length <= 0:
                continue

            in_progress = field["start"] == RECORDING_NOW or field["end"] == RECORDING_NOW
            notes: list[str] = [SOURCE_NOTE]
            if free:
                notes.append(
                    "orphaned index entry (status free): the entry was released but the "
                    "block was not overwritten (T2, FR-41)"
                )
            if in_progress:
                notes.append(
                    "end time is 0x7FFFFFFF, reported [R] as the in-progress sentinel "
                    "(recorder still writing). Treated as 'end unknown' rather than "
                    "rendered as 2038-01-19; unverified until a Tier R image confirms it"
                )
            yield Recording(
                channel=field["channel"],
                stream="main",
                t_start=decode_unix32(field["start"]),
                t_end=None if in_progress else decode_unix32(field["end"]),
                extents=(Extent(block, length),),
                codec="unknown",
                resolution=None,
                fps=None,
                size_bytes=length,
                recovery_tier="T2" if free else "T1",
                confidence=0.6 if free else 0.95,
                source_note=(
                    "HIKBTREE orphaned entry, block not yet validated"
                    if free
                    else "HIKBTREE index entry"
                ),
                notes=tuple(notes),
            )

    # --- Q4: frames out of a byte range ----------------------------------------------

    def frames(self, src: EvidenceSource, extent: Extent) -> Iterator[Frame]:
        """Yield the elementary-stream payload of each MPEG-PS pack in `extent`.

        `channel` and `t_device` are reported as None: neither is dependable in the PS
        layer at this layout version, and inventing them would be the exact failure the
        never-fabricate rule forbids. Recording-level time comes from the index entry.
        """
        offset = extent.offset
        limit = min(extent.end, src.size)
        sequence = 0
        while offset < limit:
            window = src.read(offset, min(1 << 20, limit - offset))
            if not window:
                return
            found = window.find(PACK_START)
            if found < 0:
                offset += max(1, len(window) - 3)
                continue
            at = offset + found
            header_len = validate_pack_header(src, at)
            if header_len is None:
                offset = at + 1
                continue
            body_at = at + header_len
            nxt = src.read(body_at, min(1 << 20, limit - body_at))
            end_rel = nxt.find(PACK_START, 1)
            body_len = (end_rel if end_rel > 0 else len(nxt))
            if body_len <= 0:
                return
            payload = src.map(body_at, body_len)
            yield Frame(
                kind=_kind_of(payload),
                channel=None,
                t_device=None,
                payload=payload,
                codec_hint="h264",
                extent=Extent(at, header_len + body_len),
                sequence=sequence,
                pts_ms=None,
            )
            sequence += 1
            offset = body_at + body_len

    # --- carving and time -------------------------------------------------------------

    def carve_signatures(self) -> list[Signature]:
        return [
            Signature(
                name="hikvision.ps_pack",
                pattern=PACK_START,
                validate=validate_pack_header,
            )
        ]

    def decode_time(self, raw: int | bytes, layout: DiskLayout | None) -> DeviceTime:  # noqa: ARG002
        return decode_unix32(raw)


def _kind_of(payload: memoryview) -> FrameKind:
    """I/P from the first VCL NAL where the payload makes it visible, else 'unknown'."""
    try:
        for nal in es.iter_nal_units([payload]):
            if not es.is_vcl(nal, "h264"):
                continue
            return "I" if es.nal_type(nal, "h264") == 5 else "P"
    except (ValueError, IndexError):
        return "unknown"
    return "unknown"


_CODECS: tuple[Codec, ...] = ("h264", "h265", "mjpeg", "unknown")
