"""Gap analysis — absence as a finding (doc 3 §8; FR-58).

Works on plain datetimes so it runs on either basis: `t_reference` when offsets exist, or
device-local time under the FR-53 banner (the caller labels the basis). Two products:

- per-channel gaps: windows inside a channel's own observed span with no recording;
- synchronised gaps: windows inside the span common to ALL channels where no channel has
  any coverage — a power event or a deliberate interruption, and always a §7 negative
  finding, never silently skipped footage.

Uncertainty makes gaps conservative: a gap is only asserted for the part that survives
shrinking by the uncertainty of the segments on each side (FR-52). A ±30 s device cannot
support a 40-second gap claim.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True, slots=True)
class CoverageSegment:
    """One recording's span on the analysis axis."""

    channel: int | None
    start: datetime
    end: datetime
    uncertainty_s: float
    recording_id: str

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError(f"{self.recording_id}: segment ends before it starts")


@dataclass(frozen=True, slots=True)
class Gap:
    """A window with no recording. `channel` None means all channels at once."""

    channel: int | None
    start: datetime
    end: datetime
    uncertainty_s: float
    synchronised: bool
    after_recording: str | None
    before_recording: str | None

    @property
    def duration_s(self) -> float:
        return (self.end - self.start).total_seconds()


def _merge(spans: list[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    merged: list[tuple[datetime, datetime]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def channel_gaps(segments: list[CoverageSegment], min_gap_s: float = 1.0) -> list[Gap]:
    """Windows with no recording, per channel, within each channel's own observed span."""
    by_channel: dict[int | None, list[CoverageSegment]] = {}
    for segment in segments:
        by_channel.setdefault(segment.channel, []).append(segment)
    gaps: list[Gap] = []
    for channel in sorted(by_channel, key=lambda c: (c is None, c or 0)):
        ordered = sorted(by_channel[channel], key=lambda s: (s.start, s.end))
        for left, right in zip(ordered, ordered[1:], strict=False):
            uncertainty = left.uncertainty_s + right.uncertainty_s
            start = left.end + timedelta(seconds=left.uncertainty_s)
            end = right.start - timedelta(seconds=right.uncertainty_s)
            if (end - start).total_seconds() >= min_gap_s:
                gaps.append(Gap(channel, start, end, uncertainty, False,
                                left.recording_id, right.recording_id))
    return gaps


def synchronised_gaps(segments: list[CoverageSegment], min_gap_s: float = 1.0) -> list[Gap]:
    """Windows inside the all-channel common span where NO channel has coverage (FR-58)."""
    channels = {s.channel for s in segments}
    if len(channels) < 2:
        return []
    common_start = max(min(s.start for s in segments if s.channel == c) for c in channels)
    common_end = min(max(s.end for s in segments if s.channel == c) for c in channels)
    if common_end <= common_start:
        return []
    worst = max(s.uncertainty_s for s in segments)
    covered = _merge([(s.start, s.end) for s in segments])
    gaps: list[Gap] = []
    cursor = common_start
    for start, end in covered:
        if start > cursor:
            gap_start = min(max(cursor, common_start), common_end)
            gap_end = min(start, common_end)
            _append_synchronised(gaps, gap_start, gap_end, worst, min_gap_s)
        cursor = max(cursor, end)
    if cursor < common_end:
        _append_synchronised(gaps, cursor, common_end, worst, min_gap_s)
    return gaps


def _append_synchronised(
    gaps: list[Gap], start: datetime, end: datetime, worst_uncertainty: float, min_gap_s: float
) -> None:
    start = start + timedelta(seconds=worst_uncertainty)
    end = end - timedelta(seconds=worst_uncertainty)
    if (end - start).total_seconds() >= min_gap_s:
        gaps.append(Gap(None, start, end, 2 * worst_uncertainty, True, None, None))
