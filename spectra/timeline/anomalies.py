"""Time anomalies — where the recorder's own clock contradicts itself (FR-55, doc 1 §4.5).

Gap analysis (`gaps.py`) asks where footage is missing. This asks something different:
where the times the recorder wrote down cannot all be true at once. Both feed §7.

## What an anomaly is, and what it is not

Every detector here reports a *contradiction in the recorded data*. None of them reports a
cause. That distinction is the whole design, because each of these patterns has several
possible explanations and the tool cannot choose between them from the disk alone:

* Two recordings claiming the same minute on one channel is what a clock set backwards
  looks like — and also what a dead RTC resetting to its epoch on every power-up looks
  like, and what a badly restored backup index looks like.
* Footage timestamped before the volume was formatted is what a clock reset looks like —
  and CLAUDE.md §17 records the specific claim this must **not** become: it is not
  evidence that footage was destroyed, and a dead-RTC reset produces exactly the same
  pattern. It is an anomaly to corroborate against the device log, nothing more.

So each anomaly carries `possible_causes` and never a conclusion, and the wording that
reaches the report says what was observed rather than what it means.

## Basis

Like `gaps.py`, this works on plain datetimes and runs on whichever axis the caller has —
`t_reference` where offsets exist, device-local time under the FR-53 banner. Anomalies
found on the device-local axis are, if anything, *more* interesting: they are the
recorder disagreeing with itself, which no offset can explain away.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

AnomalyKind = Literal[
    "duplicate_window",
    "backwards_sequence",
    "before_volume_format",
    "future_dated",
    "zero_length",
    "ends_before_start",
]

#: Detector → the explanations that fit the pattern. Printed with every anomaly so a
#: reader is never handed a symptom with one implied cause.
POSSIBLE_CAUSES: dict[AnomalyKind, tuple[str, ...]] = {
    "duplicate_window": (
        "the device clock was set backwards, manually or by NTP correction",
        "the real-time clock battery failed and the clock reset on power-up",
        "an index was restored or rebuilt from a stale copy",
    ),
    "backwards_sequence": (
        "the device clock was set backwards between the two recordings",
        "the index entries were written out of order after a power event",
    ),
    "before_volume_format": (
        "the device clock was wrong when the footage was written",
        "the clock was wrong when the volume was formatted",
        "the recorded format time is not the most recent format of this volume",
    ),
    "future_dated": (
        "the device clock was ahead of true time",
        "the acquisition time recorded for this evidence is wrong",
    ),
    "zero_length": (
        "the recording was interrupted before any frame was written",
        "the index entry was allocated but never completed",
    ),
    "ends_before_start": (
        "the device clock was set backwards while the recording was in progress",
        "the start or end field is corrupt, or is being read with the wrong layout",
    ),
}


@dataclass(frozen=True, slots=True)
class Segment:
    """One recording on the analysis axis. `index_position` is its order on the disk."""

    recording_id: str
    channel: int | None
    start: datetime | None
    end: datetime | None
    index_position: int = 0
    extent_key: str = ""

    @property
    def timed(self) -> bool:
        return self.start is not None and self.end is not None


@dataclass(frozen=True, slots=True)
class Anomaly:
    """A contradiction in the recorded times. Never a conclusion about why."""

    kind: AnomalyKind
    channel: int | None
    detail: str
    recording_ids: tuple[str, ...]
    possible_causes: tuple[str, ...] = ()
    numbers: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "channel": self.channel,
            "detail": self.detail,
            "recording_ids": list(self.recording_ids),
            "possible_causes": list(self.possible_causes),
            "numbers": dict(sorted(self.numbers.items())),
        }


def _make(kind: AnomalyKind, channel: int | None, detail: str,
          ids: tuple[str, ...], **numbers: Any) -> Anomaly:
    return Anomaly(kind, channel, detail, ids, POSSIBLE_CAUSES[kind], numbers)


def duplicate_windows(segments: list[Segment]) -> list[Anomaly]:
    """Two recordings on one channel claiming overlapping time but different bytes.

    A channel records one thing at a time, so two entries covering the same minute cannot
    both be describing what the camera saw then. Different physical extents is what makes
    this a contradiction rather than a duplicate row: the device wrote two distinct pieces
    of footage and labelled them with the same period.
    """
    out: list[Anomaly] = []
    by_channel: dict[int | None, list[Segment]] = {}
    for seg in segments:
        # A recording that ends before it starts has no interval to overlap with; it is
        # reported by `ends_before_start` instead.
        if seg.timed and seg.start <= seg.end:  # type: ignore[operator]
            by_channel.setdefault(seg.channel, []).append(seg)

    for channel, group in sorted(by_channel.items(), key=lambda kv: (kv[0] is None, kv[0])):
        ordered = sorted(group, key=lambda s: (s.start, s.end, s.recording_id))  # type: ignore[arg-type,return-value]
        for earlier, later in zip(ordered, ordered[1:], strict=False):
            assert earlier.end is not None and later.start is not None
            if later.start >= earlier.end:
                continue
            if earlier.extent_key and earlier.extent_key == later.extent_key:
                continue  # the same bytes described twice is a merge problem, not a clock one
            overlap = (min(earlier.end, later.end or earlier.end) - later.start).total_seconds()  # type: ignore[operator]
            out.append(
                _make(
                    "duplicate_window", channel,
                    f"{earlier.recording_id} and {later.recording_id} both claim time on "
                    f"channel {channel}, overlapping by {overlap:.0f} s, but occupy "
                    "different regions of the disk. Both cannot describe what this channel "
                    "recorded during that period.",
                    (earlier.recording_id, later.recording_id),
                    overlap_s=round(overlap, 3),
                )
            )
    return out


def backwards_sequence(segments: list[Segment]) -> list[Anomaly]:
    """Footage written later on the disk but timestamped earlier.

    A recorder allocates blocks forward. When the position on the disk advances and the
    timestamp goes back, the two orderings disagree, and only one of them can be tracking
    real time.
    """
    out: list[Anomaly] = []
    by_channel: dict[int | None, list[Segment]] = {}
    for seg in segments:
        if seg.timed:
            by_channel.setdefault(seg.channel, []).append(seg)

    for channel, group in sorted(by_channel.items(), key=lambda kv: (kv[0] is None, kv[0])):
        ordered = sorted(group, key=lambda s: (s.index_position, s.recording_id))
        for earlier, later in zip(ordered, ordered[1:], strict=False):
            assert earlier.start is not None and later.start is not None
            if later.start >= earlier.start:
                continue
            delta = (earlier.start - later.start).total_seconds()
            out.append(
                _make(
                    "backwards_sequence", channel,
                    f"{later.recording_id} sits after {earlier.recording_id} on the disk "
                    f"but is timestamped {delta:.0f} s earlier. The order the device wrote "
                    "these in and the order its clock reports disagree.",
                    (earlier.recording_id, later.recording_id),
                    backwards_s=round(delta, 3),
                )
            )
    return out


def before_volume_format(
    segments: list[Segment], format_time: datetime | None
) -> list[Anomaly]:
    """Footage timestamped before the volume was formatted.

    **This is not evidence that footage was destroyed** (CLAUDE.md §17). A recorder whose
    RTC battery has died resets to a firmware epoch on every power-up and writes genuinely
    new footage bearing an old date, producing exactly this pattern with nothing deleted.
    The recorded format time may also not be the most recent format. It is an observation
    to corroborate against the device system log, and the wording says so.
    """
    if format_time is None:
        return []
    out: list[Anomaly] = []
    affected = sorted(
        (s for s in segments if s.start is not None and s.start < format_time),
        key=lambda s: (s.start, s.recording_id),  # type: ignore[arg-type,return-value]
    )
    if not affected:
        return out
    earliest = affected[0].start
    assert earliest is not None
    out.append(
        _make(
            "before_volume_format", None,
            f"{len(affected)} recording(s) carry a device timestamp earlier than the "
            f"recorded volume format time ({format_time.isoformat()}), the earliest by "
            f"{(format_time - earliest).total_seconds() / 3600:.1f} hours. This is a "
            "contradiction in the recorded times and requires corroboration from the "
            "device system log; on its own it does not establish that anything was "
            "deleted or overwritten.",
            tuple(s.recording_id for s in affected[:20]),
            affected=len(affected),
            earliest_delta_s=round((format_time - earliest).total_seconds(), 3),
        )
    )
    return out


def future_dated(segments: list[Segment], acquired: datetime | None) -> list[Anomaly]:
    """Footage timestamped after the evidence was acquired — impossible as recorded."""
    if acquired is None:
        return []
    affected = sorted(
        (s for s in segments if s.start is not None and s.start > acquired),
        key=lambda s: (s.start, s.recording_id),  # type: ignore[arg-type,return-value]
    )
    if not affected:
        return []
    latest = max(s.start for s in affected if s.start is not None)
    return [
        _make(
            "future_dated", None,
            f"{len(affected)} recording(s) carry a device timestamp after this evidence "
            f"was acquired ({acquired.isoformat()}), the latest by "
            f"{(latest - acquired).total_seconds() / 3600:.1f} hours. Footage cannot have "
            "been written after the disk left the recorder, so at least one of the two "
            "times is wrong.",
            tuple(s.recording_id for s in affected[:20]),
            affected=len(affected),
            latest_delta_s=round((latest - acquired).total_seconds(), 3),
        )
    ]


def zero_length(segments: list[Segment]) -> list[Anomaly]:
    """Recordings whose start and end are the same instant."""
    affected = sorted(
        (s for s in segments if s.timed and s.start == s.end),
        key=lambda s: s.recording_id,
    )
    if not affected:
        return []
    return [
        _make(
            "zero_length", None,
            f"{len(affected)} recording(s) begin and end at the same instant, so no "
            "duration can be stated for them.",
            tuple(s.recording_id for s in affected[:20]),
            affected=len(affected),
        )
    ]


def ends_before_start(segments: list[Segment]) -> list[Anomaly]:
    """Recordings whose recorded end is earlier than their recorded start.

    No duration or interval can be stated for these, so the timeline and gap analysis
    leave them off the axis and list them as not placed. This is where the contradiction
    itself reaches the report.
    """
    affected = sorted(
        (s for s in segments if s.timed and s.end < s.start),  # type: ignore[operator]
        key=lambda s: s.recording_id,
    )
    if not affected:
        return []
    worst = max((s.start - s.end).total_seconds() for s in affected)  # type: ignore[operator]
    return [
        _make(
            "ends_before_start", None,
            f"{len(affected)} recording(s) carry an end time earlier than their start "
            f"time, by up to {worst:.0f} s. No duration can be stated for them, and they "
            "are left off the timeline and out of gap analysis.",
            tuple(s.recording_id for s in affected[:20]),
            affected=len(affected),
            worst_s=round(worst, 3),
        )
    ]


def detect(
    segments: list[Segment],
    *,
    format_time: datetime | None = None,
    acquired: datetime | None = None,
) -> list[Anomaly]:
    """Run every detector and return the anomalies in a deterministic order (NFR-08)."""
    out: list[Anomaly] = []
    out += duplicate_windows(segments)
    out += backwards_sequence(segments)
    out += before_volume_format(segments, format_time)
    out += future_dated(segments, acquired)
    out += zero_length(segments)
    out += ends_before_start(segments)
    out.sort(key=lambda a: (a.kind, a.channel is None, a.channel or 0, a.recording_ids))
    return out
