"""Cross-device correlation — many recorders, one axis (doc 3 §8; FR-57 data, FR-60, FR-52).

Produces the lane structure the timeline view (FR-57, section P5) and report §9 render:
one lane per (evidence item, channel), every segment on `t_reference` with its own
uncertainty. Recordings that could not be normalised are returned in `unplaced`, never
positioned on the shared axis — a lane mixing normalised and guessed positions would be a
confident, wrong timeline, the one disqualifying failure mode.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from spectra.core.models import ReferenceTime
from spectra.timeline.gaps import CoverageSegment
from spectra.timeline.timemodel import Ordering, ordering

#: Why a recording whose end precedes its start is kept off the axis. The contradiction
#: itself is reported by the FR-55 detector `anomalies.ends_before_start`.
ENDS_BEFORE_START = (
    "the recorded end time is earlier than the recorded start time, so this recording has "
    "no interval to place; reported as a time anomaly"
)


@dataclass(frozen=True, slots=True)
class Lane:
    """One row of the multi-device timeline."""

    evidence_id: str
    channel: int | None
    segments: tuple[CoverageSegment, ...]

    @property
    def label(self) -> str:
        channel = "ch ?" if self.channel is None else f"ch {self.channel}"
        return f"{self.evidence_id} {channel}"


@dataclass(frozen=True, slots=True)
class Placed:
    """One recording placed on the shared reference axis."""

    evidence_id: str
    recording_id: str
    channel: int | None
    start: datetime
    end: datetime
    uncertainty_s: float


@dataclass(frozen=True, slots=True)
class Unplaced:
    """A recording with no defensible position on the axis, and the stated reason."""

    evidence_id: str
    recording_id: str
    channel: int | None
    reason: str


@dataclass(frozen=True, slots=True)
class Correlation:
    lanes: tuple[Lane, ...]
    unplaced: tuple[Unplaced, ...]


def correlate(
    normalised: list[tuple[str, str, int | None, ReferenceTime, ReferenceTime]],
) -> Correlation:
    """(evidence_id, recording_id, channel, ref_start, ref_end) rows → lanes + unplaced.

    Each device's own `ClockModel` produced its `ReferenceTime`s, so per-device
    uncertainty rides along on every segment (FR-60).
    """
    placed: list[Placed] = []
    unplaced: list[Unplaced] = []
    for evidence_id, recording_id, channel, start, end in normalised:
        if start.utc is None or end.utc is None:
            refusal = start if start.utc is None else end
            unplaced.append(Unplaced(evidence_id, recording_id, channel,
                                     refusal.derivation_note))
            continue
        if end.utc < start.utc:
            unplaced.append(Unplaced(evidence_id, recording_id, channel, ENDS_BEFORE_START))
            continue
        placed.append(Placed(evidence_id, recording_id, channel, start.utc, end.utc,
                             max(start.uncertainty_s, end.uncertainty_s)))

    by_lane: dict[tuple[str, int | None], list[Placed]] = {}
    for item in placed:
        by_lane.setdefault((item.evidence_id, item.channel), []).append(item)
    lanes = tuple(
        Lane(
            evidence_id,
            channel,
            tuple(
                CoverageSegment(channel, p.start, p.end, p.uncertainty_s, p.recording_id)
                for p in sorted(items, key=lambda p: (p.start, p.end, p.recording_id))
            ),
        )
        for (evidence_id, channel), items in sorted(
            by_lane.items(), key=lambda kv: (kv[0][0], kv[0][1] is None, kv[0][1] or 0)
        )
    )
    ordered_unplaced = tuple(sorted(unplaced, key=lambda u: (u.evidence_id, u.recording_id)))
    return Correlation(lanes=lanes, unplaced=ordered_unplaced)


def order(a: ReferenceTime, b: ReferenceTime) -> Ordering:
    """Uncertainty-safe order of two cross-device events (FR-52); see timemodel.ordering."""
    return ordering(a, b)
