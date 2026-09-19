"""Dahua format family — Dahua, CP Plus [R], Dahua-ODM Godrej/Honeywell [H] (doc 4 §3, FR-21).

WHAT IS AND IS NOT VERIFIED — read before relying on any output of this module.

- `DHAV` … `dhav` per-frame framing: **[C]** (doc 4 §3.2).
- Field layout below: **[R]**. It follows the public open-source DHAV demuxer
  (FFmpeg `libavformat/dhav.c`, used as a format description only; no code copied) and has
  not yet been checked against team-owned hardware (doc 4 §12 row 4):

      off  size  field
      0    4     "DHAV"
      4    1     frame type: 0xFD I-frame, 0xFC P-frame, 0xF0 audio, 0xF1 auxiliary
      5    1     sub-type
      6    1     channel (as stored; 0- vs 1-based numbering is [H])
      7    1     frame sub-number
      8    4     frame sequence number, LE
      12   4     total frame length including header and trailer, LE
      16   4     packed date-time, LE: sec 0-5, min 6-11, hour 12-16, day 17-21,
                 month 22-25, year 26-31 (+2000)
      20   2     16-bit millisecond tick, LE, wraps at 65 536
      22   1     extension length
      23   1     header checksum (not validated — algorithm unverified)
      24   n     extension records (resolution, codec, fps, audio …)
      …          payload (H.264/H.265 Annex-B for video)
      L-8  4     "dhav"
      L-4  4     total frame length again, LE

- Timestamp packing: **[R]** (doc 4 §12 row 5). Test vectors in `tests/test_timestamps.py`
  prove conformance to the layout above, not truth; the label moves to [C] only with
  ≥ 12 ground-truth vectors from a real unit.
- On-disk superblock / block index: **unverified** (doc 4 §12 row 3). No layout descriptor
  is shipped, so a raw Dahua-family disk is identified from its DHAV frames with
  `parse_supported=False` and routed to carving (FR-03), never mis-parsed.

Supported parse: `.dav` export file sets (provenance class D, FR-18) — the DHAV frame chain
is their only structure (doc 4 §9.4; doc 6 §3.3 Tier E).
"""

from __future__ import annotations

import struct
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import ClassVar

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
from spectra.core.source import MIB, EvidenceSource
from spectra.plugins.base import LayoutNotSupported

MAGIC = b"DHAV"
END_MAGIC = b"dhav"
FILE_MAGIC = b"DAHUA"  # [R] optional file header seen on some .dav exports
HEADER = struct.Struct("<4sBBBBIIIHBB")
HEADER_LEN = HEADER.size  # 24
TRAILER_LEN = 8
MIN_FRAME_LEN = HEADER_LEN + TRAILER_LEN
# Not a format fact: a sanity bound so a corrupt length can never drive an allocation or a
# skip across the disk (TC-RB-05). 16 MiB is several times a 4K I-frame at CCTV bitrates.
MAX_FRAME_LEN = 16 * MIB

LAYOUT_DAV_EXPORT = "dav_export_v1"
TIME_ENCODING = "dhav_datetime32_ms16"

KIND_BY_TYPE: dict[int, FrameKind] = {0xFD: "I", 0xFC: "P", 0xF0: "audio"}
VIDEO_KINDS = ("I", "P", "B")
AUX_TYPE = 0xF1
CODEC_BY_ID: dict[int, str] = {0x1: "mpeg4", 0x2: "h264", 0x3: "mjpeg", 0x4: "h264", 0x8: "h264",
                               0xC: "h265"}
EXT_SIZES: dict[int, int] = {
    0x80: 4, 0x81: 4, 0x82: 8, 0x83: 4, 0x88: 8, 0x8C: 8,
    0x84: 4, 0x85: 4, 0x8B: 4, 0x94: 4, 0x96: 4, 0xA0: 4, 0xB2: 4, 0xB4: 4,
    0x91: 8, 0x92: 8, 0x93: 8, 0x95: 8, 0x9A: 8, 0x9B: 8, 0xB3: 8,
}

EXPORT_MEMBER_CHECK_LIMIT = 256
EXPORT_HEAD_SCAN = 64 * 1024
MAX_VALIDATIONS_PER_REGION = 64
# Not a format fact: the largest forward step in one channel's device time that still
# counts as continuous recording. The date field has 1 s resolution and the fps extension
# is a whole number (≥ 1 frame/s), so consecutive frames differ by at most 1 s, or 2 s with
# timing jitter at 1 fps. A stream recorded slower than that is split frame by frame, each
# split noted: fragmented, never a false interval.
MAX_CONTINUOUS_STEP = timedelta(seconds=2)
MAX_MATCHES_SHOWN = 4
SOURCE_NOTE_LABEL = "DHAV field layout per doc 4 §3.2 [R], not yet hardware-verified"


@dataclass(frozen=True, slots=True)
class DhavHeader:
    offset: int
    frame_type: int
    subtype: int
    channel: int
    sub_number: int
    sequence: int
    length: int
    date_raw: int
    tick: int
    ext_length: int
    raw_time: bytes
    width: int | None = None
    height: int | None = None
    codec_id: int | None = None
    fps: int | None = None
    unknown_ext_types: tuple[int, ...] = ()

    @property
    def kind(self) -> FrameKind:
        return KIND_BY_TYPE.get(self.frame_type, "unknown")

    @property
    def payload_offset(self) -> int:
        return self.offset + HEADER_LEN + self.ext_length

    @property
    def payload_length(self) -> int:
        return self.length - HEADER_LEN - self.ext_length - TRAILER_LEN


def parse_header(buf: bytes | memoryview, offset: int) -> DhavHeader | None:
    """Parse and bounds-check a DHAV header at the start of `buf` (≥ 24 bytes).
    Trailer validation is separate; see `validate_frame`."""
    if len(buf) < HEADER_LEN:
        return None
    magic, ftype, subtype, channel, subnum, seq, length, date, tick, ext_len, _ = (
        HEADER.unpack_from(buf)
    )
    if magic != MAGIC or not MIN_FRAME_LEN <= length <= MAX_FRAME_LEN:
        return None
    if ftype == AUX_TYPE:
        ext_len = 0  # [R] auxiliary frames: only the first 20 header bytes are defined
    if HEADER_LEN + ext_len + TRAILER_LEN > length:
        return None
    width = height = codec_id = fps = None
    unknown: list[int] = []
    ext = bytes(buf[HEADER_LEN : HEADER_LEN + ext_len])
    pos = 0
    while pos < len(ext):
        ext_type = ext[pos]
        size = EXT_SIZES.get(ext_type)
        if size is None or pos + size > len(ext):
            unknown.append(ext_type)
            break  # unknown record: stop interpreting, keep the payload boundary from ext_len
        rec = ext[pos : pos + size]
        if ext_type == 0x80:
            width, height = rec[2] * 8, rec[3] * 8
        elif ext_type == 0x81:
            codec_id, fps = rec[2], rec[3]
        elif ext_type == 0x82:
            width, height = struct.unpack_from("<HH", rec, 4)
        pos += size
    return DhavHeader(
        offset=offset, frame_type=ftype, subtype=subtype, channel=channel, sub_number=subnum,
        sequence=seq, length=length, date_raw=date, tick=tick, ext_length=ext_len,
        raw_time=bytes(buf[16:22]), width=width, height=height, codec_id=codec_id, fps=fps,
        unknown_ext_types=tuple(unknown),
    )


def validate_frame(src: EvidenceSource, offset: int, limit: int | None = None) -> DhavHeader | None:
    """Full self-validating check (doc 4 §3.2): header in bounds, and the length field lands
    on a `dhav` trailer that repeats the same length. `limit` caps the frame end (e.g. the
    end of a file inside a file set)."""
    end_limit = src.size if limit is None else min(limit, src.size)
    header = parse_header(src.read(offset, HEADER_LEN + 255), offset)
    if header is None or offset + header.length > end_limit:
        return None
    trailer = src.read(offset + header.length - TRAILER_LEN, TRAILER_LEN)
    if len(trailer) != TRAILER_LEN or trailer[:4] != END_MAGIC:
        return None
    if int.from_bytes(trailer[4:], "little") != header.length:
        return None
    return header


def decode_datetime(raw: int | bytes) -> DeviceTime:
    """Packed DHAV date-time [R] → DeviceTime. `raw` is the 6 stored bytes (date LE32 +
    tick LE16) or the date field as an int. Invalid calendar values give `local=None`;
    nothing is clamped or guessed."""
    if isinstance(raw, bytes):
        if len(raw) not in (4, 6):
            raise ValueError("DHAV raw time must be 4 or 6 bytes")
        date = int.from_bytes(raw[:4], "little")
    else:
        if not 0 <= raw <= 0xFFFFFFFF:
            raise ValueError("DHAV date field is 32 bits")
        date = raw
    second = date & 0x3F
    minute = (date >> 6) & 0x3F
    hour = (date >> 12) & 0x1F
    day = (date >> 17) & 0x1F
    month = (date >> 22) & 0x0F
    year = ((date >> 26) & 0x3F) + 2000
    try:
        local: datetime | None = datetime(year, month, day, hour, minute, second)
    except ValueError:
        local = None
    return DeviceTime(raw=raw, encoding=TIME_ENCODING, local=local)


_EPOCH = datetime(2000, 1, 1)


@dataclass
class _TickClock:
    """Unwraps the 16-bit ms tick into `pts_ms`, using the 1 s date to count wraps."""

    last_tick: int | None = None
    last_seconds: float | None = None
    pts: int = 0

    def advance(self, tick: int, local: datetime | None) -> int:
        # Arithmetic on the naive wall clock only — never .timestamp(), which would pull in
        # the host's timezone and DST rules and break determinism (NFR-08).
        seconds = (local - _EPOCH).total_seconds() if local is not None else None
        if self.last_tick is None:
            self.pts = tick
        else:
            delta = (tick - self.last_tick) % 0x10000
            if seconds is not None and self.last_seconds is not None:
                expected = (seconds - self.last_seconds) * 1000
                delta += round((expected - delta) / 0x10000) * 0x10000
            self.pts += delta
        self.last_tick = tick
        if seconds is not None:
            self.last_seconds = seconds
        return self.pts


class _Window:
    """Maps the source a window at a time so frame payloads are views, not copies (D4)."""

    SIZE = 8 * MIB

    def __init__(self, src: EvidenceSource) -> None:
        self.src = src
        self.start = 0
        self.view = memoryview(b"")

    def get(self, offset: int, length: int) -> memoryview:
        if not (self.start <= offset and offset + length <= self.start + len(self.view)):
            self.start = offset
            self.view = self.src.map(offset, max(self.SIZE, length))
        rel = offset - self.start
        return self.view[rel : rel + length]


def _find_next_magic(src: EvidenceSource, start: int, end: int) -> int | None:
    step = MIB
    pos = start
    while pos < end:
        chunk = src.read(pos, min(step + len(MAGIC) - 1, end - pos))
        hit = chunk.find(MAGIC)
        if hit >= 0:
            return pos + hit
        pos += step
    return None


def iter_frames(
    src: EvidenceSource, extent: Extent, resync: bool = True
) -> Iterator[tuple[DhavHeader, Frame]]:
    """Walk validated DHAV frames inside `extent`. Bytes that do not form a valid frame are
    skipped by scanning to the next `DHAV`; the caller sees the jump via `Frame.extent`."""
    window = _Window(src)
    clocks: dict[tuple[int, bool], _TickClock] = {}
    codec_by_channel: dict[int, str] = {}
    pos, end = extent.offset, extent.end
    while pos + MIN_FRAME_LEN <= end:
        header = validate_frame(src, pos, limit=end)
        if header is None:
            if not resync:
                return
            nxt = _find_next_magic(src, pos + 1, end)
            if nxt is None:
                return
            pos = nxt
            continue
        t_device = decode_datetime(header.raw_time)
        video = header.kind in VIDEO_KINDS
        pts_ms: int | None = None
        if video or header.kind == "audio":  # other types do not define the tick bytes
            clock = clocks.setdefault((header.channel, video), _TickClock())
            pts_ms = clock.advance(header.tick, t_device.local)
        if header.codec_id is not None and video:
            codec_by_channel[header.channel] = CODEC_BY_ID.get(header.codec_id, "unknown")
        # Auxiliary and unrecognised frame types are yielded as kind "unknown" so the byte
        # chain stays unbroken for coverage and gap accounting; exporters skip them.
        yield header, Frame(
            kind=header.kind,
            channel=header.channel,
            t_device=t_device,
            payload=window.get(header.payload_offset, header.payload_length),
            codec_hint=codec_by_channel.get(header.channel) if video else None,
            extent=Extent(pos, header.length),
            sequence=header.sequence,
            pts_ms=pts_ms,
        )
        pos += header.length


@dataclass
class _ChannelRun:
    spans: list[Extent] = field(default_factory=list)
    first: DeviceTime | None = None
    last: DeviceTime | None = None
    codec: str | None = None
    width: int | None = None
    height: int | None = None
    fps: int | None = None
    video_frames: int = 0
    unknown_ext: set[int] = field(default_factory=set)
    first_kind: FrameKind | None = None
    notes: list[str] = field(default_factory=list)

    def discontinuity(self, frame: Frame) -> tuple[datetime, datetime] | None:
        """(this run's latest device time, `frame`'s), if `frame` is a video frame whose
        time does not continue the run: earlier than it, or later by more than
        `MAX_CONTINUOUS_STEP`.

        Frames are walked in the order the device wrote them, and the known DHAV video
        types are I and P only, so write order is capture order. One recording cannot span
        a step back: its end would precede its start. Nor can it span a jump forward: it
        would claim footage for a period it holds none of, hiding a hole from gap analysis.
        Splitting on both also contains a single frame with a corrupt date, which would
        otherwise stretch its segment's interval to that date.
        """
        if frame.kind not in VIDEO_KINDS or frame.t_device is None:
            return None
        if self.last is None or self.last.local is None or frame.t_device.local is None:
            return None
        before, after = self.last.local, frame.t_device.local
        if before <= after <= before + MAX_CONTINUOUS_STEP:
            return None
        return before, after

    def add(self, header: DhavHeader, frame: Frame) -> None:
        span = frame.extent
        if self.spans and self.spans[-1].end == span.offset:
            last = self.spans[-1]
            self.spans[-1] = Extent(last.offset, span.end - last.offset)
        else:
            self.spans.append(span)
        if frame.kind not in VIDEO_KINDS:
            return
        self.first_kind = self.first_kind or frame.kind
        self.video_frames += 1
        if frame.t_device is not None and frame.t_device.local is not None:
            self.first = self.first or frame.t_device
            self.last = frame.t_device
        self.codec = frame.codec_hint or self.codec
        if header.width:
            self.width, self.height = header.width, header.height
        if header.fps:
            self.fps = header.fps
        self.unknown_ext.update(header.unknown_ext_types)


def _as_codec(name: str | None) -> Codec:
    return name if name in ("h264", "h265", "mjpeg") else "unknown"  # type: ignore[return-value]


def _member_end(src: EvidenceSource, offset: int) -> int | None:
    if src.identity.source_format != "file_set":
        return None
    for member in src.identity.members:
        if member.extent.offset <= offset < member.extent.end:
            return member.extent.end
    return None


def _match_pair(src: EvidenceSource, header: DhavHeader, where: str) -> list[SignatureMatch]:
    return [
        SignatureMatch(
            header.offset, src.read(header.offset, HEADER_LEN), f"DHAV frame header ({where})"
        ),
        SignatureMatch(
            header.offset + header.length - TRAILER_LEN,
            src.read(header.offset + header.length - TRAILER_LEN, TRAILER_LEN),
            "matching dhav trailer repeating the frame length",
        ),
    ]


class DahuaPlugin:
    family: ClassVar[str] = "dahua"
    layout_versions: ClassVar[tuple[str, ...]] = (LAYOUT_DAV_EXPORT,)
    # 0.2.0: a channel's recording is split where its device time steps back or jumps
    # forward (MAX_CONTINUOUS_STEP); recordings not starting on an I-frame say so.
    plugin_version: ClassVar[str] = "0.2.0"

    # -- Q1 ---------------------------------------------------------------------------
    @classmethod
    def probe(cls, src: EvidenceSource, plan: ProbePlan) -> ProbeResult | None:
        if src.identity.source_format == "file_set":
            result = cls._probe_export(src)
            if result is not None:
                return result
        return cls._probe_frames(src, plan)

    @classmethod
    def _export_members(
        cls, src: EvidenceSource
    ) -> tuple[list[tuple[str, DhavHeader]], list[str], bool]:
        """Members whose content starts with a validated DHAV frame (after an optional
        DAHUA file header), members that do not, and whether the check was capped."""
        parsed: list[tuple[str, DhavHeader]] = []
        other: list[str] = []
        members = [m for m in src.identity.members if m.extent.length]
        capped = len(members) > EXPORT_MEMBER_CHECK_LIMIT
        for member in members[:EXPORT_MEMBER_CHECK_LIMIT]:
            start, end = member.extent.offset, member.extent.end
            header = validate_frame(src, start, limit=end)
            if header is None:
                head = src.read(start, min(EXPORT_HEAD_SCAN, member.extent.length))
                if head.startswith(FILE_MAGIC):
                    hit = head.find(MAGIC)
                    header = validate_frame(src, start + hit, limit=end) if hit >= 0 else None
            if header is None:
                other.append(member.path)
            else:
                parsed.append((member.path, header))
        return parsed, other, capped

    @classmethod
    def _probe_export(cls, src: EvidenceSource) -> ProbeResult | None:
        parsed, other, capped = cls._export_members(src)
        if not parsed:
            return None
        matches: list[SignatureMatch] = []
        for path, header in parsed[: MAX_MATCHES_SHOWN // 2]:
            matches += _match_pair(src, header, path)
        note = (f"{len(parsed)} export file(s) begin with validated DHAV frames; "
                f"{len(other)} file(s) do not and will not be parsed")
        if other:
            note += ": " + ", ".join(other[:10]) + (" …" if len(other) > 10 else "")
        if capped:
            note += f"; only the first {EXPORT_MEMBER_CHECK_LIMIT} files were checked"
        note += f". {SOURCE_NOTE_LABEL}."
        return ProbeResult(
            family=cls.family,
            layout_version=LAYOUT_DAV_EXPORT,
            confidence=0.95 if not other and not capped else 0.9,
            parse_supported=True,
            matches=tuple(matches),
            plugin_version=cls.plugin_version,
            note=note,
        )

    @classmethod
    def _probe_frames(cls, src: EvidenceSource, plan: ProbePlan) -> ProbeResult | None:
        """DHAV fallback in the data area (doc 4 §3.4): a Dahua-family disk whose
        superblock is damaged or unrecognised is still identifiable and carvable."""
        regions_with_frames = 0
        matches: list[SignatureMatch] = []
        for region in plan.all_regions():
            data = src.read(region.offset, region.length)
            found_here = False
            hit = data.find(MAGIC)
            checked = 0
            while hit >= 0 and checked < MAX_VALIDATIONS_PER_REGION:
                checked += 1
                offset = region.offset + hit
                header = validate_frame(src, offset, limit=_member_end(src, offset))
                if header is not None:
                    found_here = True
                    if len(matches) < MAX_MATCHES_SHOWN:
                        matches += _match_pair(src, header, "data area")
                    break
                hit = data.find(MAGIC, hit + 1)
            regions_with_frames += found_here
        if not regions_with_frames:
            return None
        return ProbeResult(
            family=cls.family,
            layout_version=None,
            confidence=min(0.8, 0.5 + 0.1 * (regions_with_frames - 1)),
            parse_supported=False,
            matches=tuple(matches[:MAX_MATCHES_SHOWN]),
            plugin_version=cls.plugin_version,
            note=(
                f"validated DHAV frames found in {regions_with_frames} probed region(s), but no "
                "recognised Dahua superblock: the on-disk layout is unverified (doc 4 §12 "
                "row 3), so this evidence is carve-only (FR-03). " + SOURCE_NOTE_LABEL + "."
            ),
        )

    # -- Q2 ---------------------------------------------------------------------------
    def superblock(self, src: EvidenceSource) -> DiskLayout:
        if src.identity.source_format != "file_set":
            raise LayoutNotSupported(
                "no verified Dahua on-disk superblock layout (doc 4 §12 row 3); carve only"
            )
        parsed, other, _ = self._export_members(src)
        if not parsed:
            raise LayoutNotSupported("no export file begins with a validated DHAV frame")
        return DiskLayout(
            family=self.family,
            layout_version=LAYOUT_DAV_EXPORT,
            block_size=None,
            block_count=None,
            blocks_used=None,
            index_extents=(),
            log_extents=(),
            format_time=None,
            note=f"export file set: {len(parsed)} DHAV file(s), {len(other)} other file(s)",
        )

    # -- Q3 ---------------------------------------------------------------------------
    def enumerate(
        self, src: EvidenceSource, layout: DiskLayout, include_orphans: bool
    ) -> Iterator[Recording]:
        if layout.layout_version != LAYOUT_DAV_EXPORT:
            raise LayoutNotSupported(f"layout {layout.layout_version!r} is not parseable")
        for member in src.identity.members:
            if not member.extent.length:
                continue
            yield from self._enumerate_member(src, member.path, member.extent)

    def _enumerate_member(
        self, src: EvidenceSource, path: str, extent: Extent
    ) -> Iterator[Recording]:
        """One recording per channel per unbroken segment of the file. A skipped region
        (bytes that are not valid frames) closes the segment and is reported in `notes`.
        So does a break in a channel's device time (see `_ChannelRun.discontinuity`), for
        that channel only; the contradiction itself is left to the FR-55 anomaly detectors
        and gap analysis to report."""
        runs: dict[int, _ChannelRun] = {}
        cursor = extent.offset
        leading_gap: int | None = None
        for header, frame in iter_frames(src, extent):
            if frame.extent.offset != cursor:
                gap = Extent(cursor, frame.extent.offset - cursor)
                if cursor == extent.offset:
                    leading_gap = gap.length  # e.g. a DAHUA file header before the first frame
                else:
                    yield from self._emit(path, runs, closing_gap=gap)
                    runs = {}
            run = runs.get(header.channel)
            broken = run.discontinuity(frame) if run is not None else None
            if run is not None and broken is not None:
                before, after = broken
                seconds = abs((after - before).total_seconds())
                step = (f"{'back' if after < before else 'forward'} {seconds:.0f} s, from "
                        f"{before.isoformat()} to {after.isoformat()}, at offset "
                        f"{frame.extent.offset}")
                run.notes.append(f"device time steps {step}, after this segment; the frames "
                                 "from there on are reported as a separate recording")
                yield from self._emit(path, {header.channel: run}, closing_gap=None)
                runs[header.channel] = _ChannelRun(notes=[
                    f"device time steps {step}, at the start of this segment; the frames "
                    "before it on this channel are reported as a separate recording"
                ])
            runs.setdefault(header.channel, _ChannelRun()).add(header, frame)
            cursor = header.offset + header.length
        trailing = Extent(cursor, extent.end - cursor) if cursor < extent.end else None
        yield from self._emit(path, runs, closing_gap=trailing, leading_gap=leading_gap)

    def _emit(
        self,
        path: str,
        runs: dict[int, _ChannelRun],
        closing_gap: Extent | None,
        leading_gap: int | None = None,
    ) -> Iterator[Recording]:
        for channel in sorted(runs):
            run = runs[channel]
            if not run.video_frames:
                continue
            notes = ["channel number as stored in the DHAV header; numbering base unverified [H]"]
            if leading_gap:
                notes.append(f"{leading_gap} byte(s) before the first frame were not frames")
            if closing_gap is not None:
                notes.append(
                    f"{closing_gap.length} byte(s) at offset {closing_gap.offset} after this "
                    "segment are not valid DHAV frames"
                )
            if run.first_kind != "I":
                notes.append("the first video frame is not an I-frame: frames before the "
                             "first I-frame depend on earlier data and will not decode from "
                             "this recording alone")
            if run.first is None:
                notes.append("no frame carried a decodable date-time: time unknown")
            if run.codec is None:
                notes.append("codec not stated in any frame extension")
            elif _as_codec(run.codec) == "unknown":
                notes.append(f"codec id indicates {run.codec}, which has no evidence-remux path")
            if run.unknown_ext:
                notes.append("unrecognised DHAV extension type(s): "
                             + ", ".join(f"0x{t:02X}" for t in sorted(run.unknown_ext)))
            notes += run.notes
            yield Recording(
                channel=channel,
                stream="unknown",
                t_start=run.first,
                t_end=run.last,
                extents=tuple(run.spans),
                codec=_as_codec(run.codec),
                resolution=(run.width, run.height) if run.width and run.height else None,
                fps=float(run.fps) if run.fps else None,
                size_bytes=sum(s.length for s in run.spans),
                recovery_tier="T1",
                confidence=1.0,
                source_note=(f"{path}: DHAV frame walk of a vendor export file (no on-disk "
                             f"index; provenance class D). {SOURCE_NOTE_LABEL}."),
                frame_count=run.video_frames,
                notes=tuple(notes),
            )

    # -- Q4 ---------------------------------------------------------------------------
    def frames(self, src: EvidenceSource, extent: Extent) -> Iterator[Frame]:
        for _, frame in iter_frames(src, extent):
            yield frame

    def carve_signatures(self) -> list[Signature]:
        def validate(src: EvidenceSource, offset: int) -> int | None:
            header = validate_frame(src, offset, limit=_member_end(src, offset))
            return None if header is None else header.length

        return [Signature(name="dahua.dhav_frame", pattern=MAGIC, validate=validate)]

    def decode_time(self, raw: int | bytes, layout: DiskLayout | None) -> DeviceTime:
        return decode_datetime(raw)
