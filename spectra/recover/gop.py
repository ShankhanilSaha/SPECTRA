"""T3/T4 reassembly — validated hits to playable runs (doc 3 §7.2, FR-42, FR-43, FR-45).

`carver.scan()` finds structures. This turns them into `Recording`s, and the difference
between the two is where carving usually goes wrong: a pile of frame offsets is not
footage, and emitting one clip per hit produces thousands of unplayable fragments that
inflate every total in the report.

Three rules do the work:

- **A run ends at a discontinuity.** Channel changing, time running backwards, a sequence
  number skipping, or a byte gap all mean the next frame belongs to a different recording —
  or to whatever overwrote this one. Walking through a discontinuity is how a carver
  produces a single "recording" that is actually two, with a start time from one and an
  end time from another.
- **A run starts at an I-frame.** Everything before the first keyframe references data
  that is gone, so it cannot be decoded and is trimmed. Reporting it would be reporting
  bytes we know will not play.
- **Confidence follows what the container actually carried**, on the fixed ladder in
  doc 3 §7.2. A run with no recoverable time scores 0.4 and is labelled *time unknown* —
  it is placed in physical order and never given an inferred time (FR-45).
- **A run never spans an unreadable hole.** Pass `unreadable` and every candidate span
  is cut at each bad-sector run, so each survivable piece becomes its own clip (T4,
  FR-43). One long clip with an invisible hole in the middle is the failure this
  prevents, and it is why T4 is a tier rather than a flag.

Vendor-free: everything here reads `Frame` fields that the contract guarantees, and asks
the plugin for frames. It has no idea which family it is working on.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from spectra.core.models import Codec, DeviceTime, Extent, Recording, RecoveryTier
from spectra.core.source import EvidenceSource
from spectra.recover.carver import CarveHit

#: Hits further apart than this start a new candidate span. Zero means strictly adjacent.
MAX_HIT_GAP = 0

#: A run of at least this many keyframes is treated as a real recording rather than a
#: fragment, which is the ">= 2 consecutive GOPs" condition of the doc 3 §7.2 ladder.
GOPS_FOR_FULL_CONFIDENCE = 2

CONF_CHANNEL_AND_TIME = 0.9
CONF_TIME_ONLY = 0.7
CONF_NO_TIME = 0.4
CONF_FRAGMENT = 0.2

#: A disk becomes unreadable a sector at a time, so that is the window used when
#: deciding whether a bad-sector run bounded a clip.
_SECTOR = 512


class _Plugin(Protocol):
    """The slice of `VendorPlugin` used here."""

    family: str

    def frames(self, src: EvidenceSource, extent: Extent) -> Iterator[object]: ...


@dataclass(frozen=True, slots=True)
class Run:
    """A contiguous, decodable sequence of frames from one recording."""

    extent: Extent
    channel: int | None
    t_start: DeviceTime | None
    t_end: DeviceTime | None
    codec: Codec
    frame_count: int
    keyframes: int
    trimmed_bytes: int
    reason_ended: str

    @property
    def confidence(self) -> float:
        """The doc 3 §7.2 ladder, applied to what this run actually recovered."""
        has_time = self.t_start is not None
        if self.frame_count <= 1:
            return CONF_FRAGMENT
        if has_time and self.channel is not None and self.keyframes >= GOPS_FOR_FULL_CONFIDENCE:
            return CONF_CHANNEL_AND_TIME
        if has_time:
            return CONF_TIME_ONLY
        return CONF_NO_TIME

    @property
    def confidence_note(self) -> str:
        """Why this run scored what it did — the ladder's bands are not self-evident.

        Doc 3 §7.2 defines 0.7 as "time but no channel", but a run *with* a channel that
        has only one keyframe also lands there: it has not demonstrated chain continuity.
        Both cases score the same and mean different things, so the reason is recorded
        rather than left for a report reader to infer from a bare number.
        """
        if self.frame_count <= 1:
            return "single fragment: decodes, but no chain to corroborate it"
        if self.confidence == CONF_CHANNEL_AND_TIME:
            return f"channel and time recovered across {self.keyframes} keyframes"
        if self.confidence == CONF_TIME_ONLY and self.channel is not None:
            return (
                f"time and channel recovered, but only {self.keyframes} keyframe(s): "
                f"{GOPS_FOR_FULL_CONFIDENCE} are needed to evidence a continuous chain"
            )
        if self.confidence == CONF_TIME_ONLY:
            return "time recovered, channel not carried by the container"
        return "no recoverable timestamp in the carved payload"


def group_hits(
    hits: Iterable[CarveHit], *, max_gap: int = MAX_HIT_GAP
) -> Iterator[Extent]:
    """Coalesce adjacent validated hits into candidate spans.

    `scan()` emits hits in increasing offset order, so this is a single pass. Frames that
    sit back-to-back are one span; a gap wider than `max_gap` starts a new one.
    """
    start: int | None = None
    end = 0
    for hit in hits:
        if start is None:
            start, end = hit.offset, hit.end
            continue
        if hit.offset - end <= max_gap:
            end = max(end, hit.end)
        else:
            yield Extent(start, end - start)
            start, end = hit.offset, hit.end
    if start is not None and end > start:
        yield Extent(start, end - start)


def _local(t: object) -> datetime | None:
    return getattr(t, "local", None) if t is not None else None


def _discontinuity(prev: object, nxt: object) -> str | None:
    """Why the chain breaks between two frames, or None if it does not."""
    p_ch, n_ch = getattr(prev, "channel", None), getattr(nxt, "channel", None)
    if p_ch is not None and n_ch is not None and p_ch != n_ch:
        return f"channel changed {p_ch} -> {n_ch}"

    p_t, n_t = _local(getattr(prev, "t_device", None)), _local(getattr(nxt, "t_device", None))
    if p_t is not None and n_t is not None and n_t < p_t:
        return "timestamp ran backwards"

    p_seq, n_seq = getattr(prev, "sequence", None), getattr(nxt, "sequence", None)
    if p_seq is not None and n_seq is not None and n_seq < p_seq:
        return "sequence number ran backwards"

    p_ext, n_ext = getattr(prev, "extent", None), getattr(nxt, "extent", None)
    if p_ext is not None and n_ext is not None and n_ext.offset > p_ext.end:
        return f"byte gap of {n_ext.offset - p_ext.end}"
    return None


def _split(frames: Sequence[object]) -> Iterator[tuple[list[object], str]]:
    """Split a frame sequence at every discontinuity."""
    if not frames:
        return
    run: list[object] = [frames[0]]
    for prev, nxt in zip(frames, frames[1:], strict=False):
        reason = _discontinuity(prev, nxt)
        if reason is None:
            run.append(nxt)
            continue
        yield run, reason
        run = [nxt]
    yield run, "end of span"


def _trim_to_keyframe(frames: list[object]) -> tuple[list[object], int]:
    """Drop everything before the first I-frame. Returns the run and the bytes dropped."""
    for i, frame in enumerate(frames):
        if getattr(frame, "kind", None) == "I":
            dropped = sum(
                f.extent.length for f in frames[:i] if getattr(f, "extent", None)
            )
            return frames[i:], dropped
    return [], sum(f.extent.length for f in frames if getattr(f, "extent", None))


def _to_run(frames: list[object], trimmed: int, reason: str) -> Run | None:
    extents = [f.extent for f in frames if getattr(f, "extent", None) is not None]
    if not extents:
        return None
    channels = {getattr(f, "channel", None) for f in frames}
    channels.discard(None)
    times = [getattr(f, "t_device", None) for f in frames]
    with_local = [t for t in times if _local(t) is not None]
    codecs = {getattr(f, "codec_hint", None) for f in frames}
    codecs.discard(None)
    codec: Codec = codecs.pop() if len(codecs) == 1 and codecs else "unknown"  # type: ignore[assignment]
    return Run(
        extent=Extent(extents[0].offset, extents[-1].end - extents[0].offset),
        channel=channels.pop() if len(channels) == 1 else None,
        t_start=with_local[0] if with_local else None,
        t_end=with_local[-1] if with_local else None,
        codec=codec,
        frame_count=len(frames),
        keyframes=sum(1 for f in frames if getattr(f, "kind", None) == "I"),
        trimmed_bytes=trimmed,
        reason_ended=reason,
    )


def reassemble(
    plugin: _Plugin,
    src: EvidenceSource,
    hits: Iterable[CarveHit],
    *,
    max_gap: int = MAX_HIT_GAP,
    unreadable: Sequence[Extent] = (),
) -> Iterator[Recording]:
    """Turn validated carve hits into recordings, in physical order.

    Physical order is the ordering that always exists. A run with no recoverable time is
    reported with its position on the disk and no timestamp at all, because inventing one
    from its neighbours is the failure FR-45 names.

    Passing `unreadable` makes this T4 (FR-43): candidate spans are cut at every bad-sector
    run, so **each survivable span becomes its own clip** and none is reported as spanning
    bytes that were never read. The alternative — one long clip with an invisible hole in
    the middle — is the confident-wrong answer, and it is what makes T4 a separate tier
    rather than a flag on T3.
    """
    position = 0
    tier: RecoveryTier = "T4" if unreadable else "T3"
    for span in _subtract(group_hits(hits, max_gap=max_gap), unreadable):
        try:
            frames = list(plugin.frames(src, span.extent))
        except (ValueError, OSError):
            continue
        for chain, reason in _split(frames):
            kept, trimmed = _trim_to_keyframe(chain)
            if not kept:
                continue
            run = _to_run(kept, trimmed, reason)
            if run is None:
                continue
            position += 1
            yield _as_recording(run, position, tier, span)


@dataclass(frozen=True, slots=True)
class Span:
    """A candidate span, and what bounded it. `cut_by` names the bad-sector run if any."""

    extent: Extent
    cut_by: tuple[Extent, ...] = ()


def _subtract(
    spans: Iterable[Extent], holes: Sequence[Extent]
) -> Iterator[Span]:
    """Cut every span at every hole, keeping the survivable pieces and what bounded them.

    `cut_by` records holes that **split** a span and holes that **abut** a surviving piece.
    Both matter to an examiner and only the first is a cut: by the time this runs, the
    carve has usually already been restricted to readable ranges and the hits either side
    of a hole have already grouped separately, so the common case is a clip that simply
    stops at a bad-sector run. Saying "this clip ends here because the next sectors could
    not be read" is the difference between a short clip and an unexplained one.
    """
    ordered = sorted(holes, key=lambda e: e.offset)

    def adjacent(piece: Extent) -> tuple[Extent, ...]:
        """Holes within a sector of either end of the piece.

        Exact adjacency is too strict to be useful: a span usually stops a little short of
        a hole, because the last frame that *would* have crossed into it failed to
        validate and was dropped. The hole still bounded the clip. A sector is the right
        window because a sector is the granularity at which a disk becomes unreadable.
        """
        return tuple(
            h
            for h in ordered
            if 0 <= h.offset - piece.end <= _SECTOR or 0 <= piece.offset - h.end <= _SECTOR
        )

    for span in spans:
        if not ordered:
            yield Span(span)
            continue
        cursor = span.offset
        pieces: list[tuple[Extent, tuple[Extent, ...]]] = []
        split_by: list[Extent] = []
        for hole in ordered:
            if hole.end <= cursor or hole.offset >= span.end:
                continue
            split_by.append(hole)
            if hole.offset > cursor:
                pieces.append((Extent(cursor, hole.offset - cursor), (hole,)))
            cursor = max(cursor, hole.end)
        if cursor < span.end:
            pieces.append((Extent(cursor, span.end - cursor), tuple(split_by[-1:])))
        for piece, direct in pieces:
            bounding = {*direct, *adjacent(piece)}
            yield Span(piece, tuple(sorted(bounding, key=lambda e: e.offset)))


def _as_recording(
    run: Run, position: int, tier: RecoveryTier = "T3", span: Span | None = None
) -> Recording:
    notes = [
        f"{tier} carve, physical position {position} on the image",
        f"run ended: {run.reason_ended}",
        f"confidence {run.confidence}: {run.confidence_note}",
    ]
    if span is not None and span.cut_by:
        where = ", ".join(f"{e.offset}+{e.length}" for e in span.cut_by)
        notes.append(
            f"bounded by unreadable sector run(s) at {where}: this clip covers only the "
            "survivable span, and no bytes across the hole are claimed (FR-43)"
        )
    if run.trimmed_bytes:
        notes.append(
            f"{run.trimmed_bytes} bytes before the first keyframe were trimmed: they "
            "reference data that is gone and cannot be decoded"
        )
    if run.t_start is None:
        notes.append(
            "time unknown — the container carried no recoverable timestamp for this run, "
            "so it is placed in physical order and no time is asserted (FR-45)"
        )
    if run.channel is None:
        notes.append("channel unknown — not recoverable from the carved payload")
    return Recording(
        channel=run.channel,
        stream="unknown",
        t_start=run.t_start,
        t_end=run.t_end,
        extents=(run.extent,),
        codec=run.codec,
        resolution=None,
        fps=None,
        size_bytes=run.extent.length,
        recovery_tier=tier,
        confidence=run.confidence,
        source_note=f"{tier} carved run, {run.frame_count} frames, {run.keyframes} keyframe(s)",
        frame_count=run.frame_count,
        notes=tuple(notes),
    )
