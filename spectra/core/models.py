"""Shared, vendor-independent types (doc 3 §4, §8.1; FR-28).

DRAFT CONTRACT — frozen at the end of Phase 1 (CLAUDE.md §14). Owners: `Extent`,
`Recording`, `Frame` → P1; `DeviceTime` → P2; `ReferenceTime` → P4. Additions over
doc 3 are marked "contract addition" and must be agreed at the freeze.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from spectra.core.source import EvidenceSource

RecoveryTier = Literal["T1", "T2", "T3", "T4"]
# "unknown" is a contract addition: a stream type the container does not state must not
# be guessed as "main" (CLAUDE.md §5 rule 2).
StreamKind = Literal["main", "sub", "unknown"]
Codec = Literal["h264", "h265", "mjpeg", "unknown"]
FrameKind = Literal["I", "P", "B", "audio", "unknown"]
OffsetMethod = Literal["A_ntp", "B_reference_capture", "C_external_event", "D_live_rtc", "none"]


@dataclass(frozen=True, slots=True, order=True)
class Extent:
    """A physical byte range in an evidence source. Extents, not buffers (D4)."""

    offset: int
    length: int

    def __post_init__(self) -> None:
        if self.offset < 0 or self.length < 0:
            raise ValueError(f"invalid extent offset={self.offset} length={self.length}")

    @property
    def end(self) -> int:
        return self.offset + self.length

    def contains(self, other: Extent) -> bool:
        return self.offset <= other.offset and other.end <= self.end

    def to_json(self) -> list[int]:
        return [self.offset, self.length]


@dataclass(frozen=True, slots=True)
class DeviceTime:
    """A timestamp exactly as the device stored it (doc 3 §8.1).

    `raw` is never discarded. `local` is the decoded wall clock as the device believed
    it — naive, because no timezone is known at this layer — or None when the raw value
    does not decode to a valid calendar time. Plugins return this, never a bare datetime.
    """

    raw: int | bytes
    encoding: str
    local: datetime | None

    def __post_init__(self) -> None:
        if not self.encoding:
            raise ValueError("DeviceTime.encoding is required")
        if self.local is not None and self.local.tzinfo is not None:
            raise ValueError("DeviceTime.local is device wall-clock time and must be naive")

    def raw_repr(self) -> str:
        """Stable text form of the raw value, for storage and reports."""
        if isinstance(self.raw, bytes):
            return self.raw.hex()
        return str(self.raw)


@dataclass(frozen=True, slots=True)
class ReferenceTime:
    """Normalised UTC with method and uncertainty (doc 3 §8.1, FR-50..FR-53).

    Owned by P4; only `timeline/` constructs these. `utc is None` is a legal, expected
    state: no offset evidence means no absolute time anywhere (FR-53).
    """

    utc: datetime | None
    uncertainty_s: float
    method: OffsetMethod
    tz_offset_s: int | None
    clock_offset_s: float | None
    derivation_note: str

    def __post_init__(self) -> None:
        if not math.isfinite(self.uncertainty_s) or self.uncertainty_s < 0:
            raise ValueError("uncertainty_s must be a finite, non-negative number")
        if self.method == "none" and self.utc is not None:
            raise ValueError("method 'none' cannot assert an absolute time (FR-53)")
        if self.utc is not None and self.utc.utcoffset() != timedelta(0):
            raise ValueError("ReferenceTime.utc must be timezone-aware UTC")


@dataclass(frozen=True, slots=True)
class SignatureMatch:
    """Bytes a probe matched on, shown to the examiner verbatim (FR-02, TC-ID-05)."""

    offset: int
    data: bytes
    description: str


@dataclass(frozen=True, slots=True)
class ProbePlan:
    """The regions identification is authorised to read (FR-01).

    Contract addition: doc 3 `probe(src)` leaves the "defined set of offsets" to each
    plugin. Passing the plan makes the scanned regions configurable in one place and
    recordable in the identification result. Plugins may additionally read fixed
    structure offsets their format defines, but must stay bounded.
    """

    lba0: Extent
    head: Extent
    tail: Extent
    sweep: tuple[Extent, ...]

    def all_regions(self) -> tuple[Extent, ...]:
        seen: dict[Extent, None] = {}
        for extent in (self.lba0, self.head, self.tail, *self.sweep):
            if extent.length:
                seen.setdefault(extent)
        return tuple(seen)


@dataclass(frozen=True, slots=True)
class ProbeResult:
    """One plugin's answer to "is this disk mine?" (doc 3 §4, FR-02, FR-03)."""

    family: str
    layout_version: str | None
    confidence: float
    parse_supported: bool
    matches: tuple[SignatureMatch, ...]
    plugin_version: str
    note: str = ""

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be within 0..1")
        if self.parse_supported and self.layout_version is None:
            raise ValueError("parse_supported requires a recognised layout_version (FR-03)")


@dataclass(frozen=True, slots=True)
class DiskLayout:
    """Q2 — the shape of the evidence as the plugin understands it (FR-04)."""

    family: str
    layout_version: str
    block_size: int | None
    block_count: int | None
    blocks_used: int | None
    index_extents: tuple[Extent, ...]
    log_extents: tuple[Extent, ...]
    format_time: DeviceTime | None
    note: str = ""


@dataclass(frozen=True, slots=True)
class Frame:
    """One container frame (doc 3 §4). `payload` is a read-only view, not a copy (D4).

    Contract additions:
    - `extent`: the frame's physical byte range (header + payload + trailer), so the
      recovery engine can build the coverage map and detect chain discontinuities.
    - `sequence`: the container's frame counter where it has one.
    - `pts_ms`: a sub-second presentation tick from the container's own clock, unwrapped
      and monotonic within one `frames()` iteration unless the device clock itself moved
      backwards. Only differences are meaningful; absolute time comes from `t_device`.
      Used to apply per-frame timestamps as PTS (FR-33) without vendor logic in services.
    """

    kind: FrameKind
    channel: int | None
    t_device: DeviceTime | None
    payload: memoryview
    codec_hint: str | None
    extent: Extent
    sequence: int | None = None
    pts_ms: int | None = None


@dataclass(frozen=True, slots=True)
class Recording:
    """The normalised recording record — identical schema for every vendor (FR-28).

    Contract changes over doc 3 §4: `channel`, `t_start` and `t_end` may be None, because
    carved items can legitimately be "channel unknown" (doc 4 §4.2) or "time unknown"
    (FR-45) and must not be given a guessed value; `frame_count` and `notes` added.
    """

    channel: int | None
    stream: StreamKind
    t_start: DeviceTime | None
    t_end: DeviceTime | None
    extents: tuple[Extent, ...]
    codec: Codec
    resolution: tuple[int, int] | None
    fps: float | None
    size_bytes: int
    recovery_tier: RecoveryTier
    confidence: float
    source_note: str
    frame_count: int | None = None
    notes: tuple[str, ...] = field(default=())

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be within 0..1")
        if not self.extents:
            raise ValueError("a Recording must reference at least one extent")


@dataclass(frozen=True, slots=True)
class Signature:
    """A carve signature (T3, FR-42) with a validator that rejects false positives.

    `validate(src, offset)` is called with the absolute offset of a `pattern` hit and
    returns the length of the validated structure, or None to reject the hit. It reads
    through the source so validation is not limited by the carver's window size.
    """

    name: str
    pattern: bytes
    validate: Callable[[EvidenceSource, int], int | None]
