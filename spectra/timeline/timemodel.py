"""The three-layer time model: t_device → t_local → t_reference (doc 3 §8; FR-50, FR-52, FR-53).

Sign convention, used everywhere in this package and printed in every derivation note:
`total_offset_s` is the number of seconds the device wall clock was AHEAD of true UTC
(positive = device fast). Normalisation therefore subtracts it:

    t_reference = t_local − total_offset_s

`total_offset_s` bundles the timezone and the clock error, because that is what an
observation actually measures (a reference clock filmed by the camera yields the device's
display against true time — it cannot tell the timezone apart from the error). When the
examiner states a timezone, the split is recorded: `clock_offset_s = total − tz_offset_s`;
otherwise `tz_offset_s` stays None and `clock_offset_s` carries the bundle, with the note
saying so.

A device time not covered by any offset segment normalises to the refusal value —
`ReferenceTime(utc=None, method="none")` — never to a guess (FR-53).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from spectra.core.models import DeviceTime, OffsetMethod, ReferenceTime

REFUSAL_BANNER = "absolute time not established"

# Typical uncertainty floors per offset method (doc 3 §8 table). An observation may be
# worse than its floor (the examiner says so), never better.
METHOD_FLOOR_S: dict[str, float] = {
    "A_ntp": 1.0,
    "B_reference_capture": 1.0,
    "C_external_event": 2.0,
    "D_live_rtc": 1.0,
}

METHOD_LABEL: dict[str, str] = {
    "A_ntp": "method A (NTP synchronisation proved)",
    "B_reference_capture": "method B (reference-clock capture)",
    "C_external_event": "method C (externally timestamped event)",
    "D_live_rtc": "method D (live RTC read)",
}


class TimeModelError(Exception):
    """Invalid offset evidence — wrong types, impossible bounds, overlapping segments."""


def format_offset(seconds: float) -> str:
    """`+HH:MM:SS` form used in derivation notes and the demo script (doc 8 §4)."""
    sign = "+" if seconds >= 0 else "-"
    whole = int(round(abs(seconds)))
    return f"{sign}{whole // 3600:02d}:{whole % 3600 // 60:02d}:{whole % 60:02d}"


def _require_naive(value: datetime, name: str) -> datetime:
    if value.tzinfo is not None:
        raise TimeModelError(f"{name} is device wall-clock time and must be naive")
    return value


def _require_utc(value: datetime, name: str) -> datetime:
    if value.tzinfo is None:
        raise TimeModelError(f"{name} must be timezone-aware (the true time is absolute)")
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class OffsetObservation:
    """One piece of offset evidence (FR-51): the device clock read against true time.

    `valid_from` / `valid_to` bound the device-local range this observation speaks for
    (None = open). Bounded segments exist because a manual clock change in the device log
    means per-segment offsets, never one global offset (doc 3 §8).
    """

    method: OffsetMethod
    device_local: datetime
    true_utc: datetime
    uncertainty_s: float
    note: str = ""
    valid_from: datetime | None = None
    valid_to: datetime | None = None

    def __post_init__(self) -> None:
        if self.method not in METHOD_FLOOR_S:
            raise TimeModelError(f"method {self.method!r} is not an offset method (FR-51)")
        _require_naive(self.device_local, "device_local")
        object.__setattr__(self, "true_utc", _require_utc(self.true_utc, "true_utc"))
        floor = METHOD_FLOOR_S[self.method]
        if self.uncertainty_s < floor:
            raise TimeModelError(
                f"{METHOD_LABEL[self.method]} cannot claim better than ±{floor} s"
            )
        for name, bound in (("valid_from", self.valid_from), ("valid_to", self.valid_to)):
            if bound is not None:
                _require_naive(bound, name)
        if self.valid_from and self.valid_to and self.valid_from >= self.valid_to:
            raise TimeModelError("valid_from must precede valid_to")

    @property
    def total_offset_s(self) -> float:
        """Seconds the device clock was ahead of true UTC (positive = device fast)."""
        believed_utc = self.device_local.replace(tzinfo=UTC)
        return (believed_utc - self.true_utc).total_seconds()


@dataclass(frozen=True, slots=True)
class OffsetSegment:
    """The offset holding over one device-local range (open-ended when a bound is None)."""

    total_offset_s: float
    uncertainty_s: float
    method: OffsetMethod
    note: str
    valid_from: datetime | None = None
    valid_to: datetime | None = None

    def covers(self, local: datetime) -> bool:
        if self.valid_from is not None and local < self.valid_from:
            return False
        return not (self.valid_to is not None and local >= self.valid_to)


def not_established(reason: str) -> ReferenceTime:
    """The refusal value (FR-53): device-local only, no absolute time anywhere."""
    return ReferenceTime(
        utc=None,
        uncertainty_s=0.0,
        method="none",
        tz_offset_s=None,
        clock_offset_s=None,
        derivation_note=f"{REFUSAL_BANNER}: {reason}",
    )


@dataclass(frozen=True, slots=True)
class ClockModel:
    """One device's clock behaviour: a timezone (if stated) and offset segments (FR-50)."""

    segments: tuple[OffsetSegment, ...]
    tz_offset_s: int | None = None

    @classmethod
    def from_observations(
        cls, observations: list[OffsetObservation], tz_offset_s: int | None = None
    ) -> ClockModel:
        segments = tuple(
            OffsetSegment(
                total_offset_s=obs.total_offset_s,
                uncertainty_s=obs.uncertainty_s,
                method=obs.method,
                note=_segment_note(obs),
                valid_from=obs.valid_from,
                valid_to=obs.valid_to,
            )
            for obs in sorted(
                observations, key=lambda o: (o.valid_from or datetime.min, o.device_local)
            )
        )
        for left, right in zip(segments, segments[1:], strict=False):
            if left.valid_to is None or right.valid_from is None or (
                right.valid_from < left.valid_to
            ):
                raise TimeModelError(
                    "offset observations overlap: bound each with valid_from/valid_to — a "
                    "manual clock change means per-segment offsets, never one global offset"
                )
        return cls(segments=segments, tz_offset_s=tz_offset_s)

    def normalise(self, value: DeviceTime | datetime | None) -> ReferenceTime:
        """t_local → t_reference, or the refusal value when no evidence covers it (FR-53)."""
        if value is None:
            return not_established("no device time on this item")
        if isinstance(value, DeviceTime):
            if value.local is None:
                return not_established(
                    f"raw value {value.raw_repr()} ({value.encoding}) does not decode to a "
                    "calendar time"
                )
            local = value.local
        else:
            local = _require_naive(value, "device time")
        segment = next((s for s in self.segments if s.covers(local)), None)
        if segment is None:
            return not_established("no offset evidence covers this device time")
        utc = local.replace(tzinfo=UTC) - timedelta(seconds=segment.total_offset_s)
        clock_offset = (
            segment.total_offset_s - self.tz_offset_s
            if self.tz_offset_s is not None
            else segment.total_offset_s
        )
        tz_note = (
            f"timezone {format_offset(self.tz_offset_s)} stated by the examiner"
            if self.tz_offset_s is not None
            else "timezone not stated; the offset bundles timezone and clock error"
        )
        return ReferenceTime(
            utc=utc,
            uncertainty_s=segment.uncertainty_s,
            method=segment.method,
            tz_offset_s=self.tz_offset_s,
            clock_offset_s=clock_offset,
            derivation_note=f"{segment.note}; {tz_note}",
        )


def _segment_note(obs: OffsetObservation) -> str:
    direction = "ahead of" if obs.total_offset_s >= 0 else "behind"
    note = (
        f"{METHOD_LABEL[obs.method]}: device clock {direction} true time by "
        f"{format_offset(obs.total_offset_s).lstrip('+-')} "
        f"(offset {format_offset(obs.total_offset_s)}) ± {obs.uncertainty_s:g} s"
    )
    return f"{note}; {obs.note}" if obs.note else note


Ordering = Literal["before", "after", "not_determinable"]


def ordering(a: ReferenceTime, b: ReferenceTime) -> Ordering:
    """Uncertainty-safe relative order of two normalised times (FR-52).

    ±1 s and ±30 s devices give ±31 s on relative ordering — an ordering inside the
    combined uncertainty is refused, not guessed.
    """
    if a.utc is None or b.utc is None:
        return "not_determinable"
    margin = a.uncertainty_s + b.uncertainty_s
    delta = (a.utc - b.utc).total_seconds()
    if abs(delta) <= margin:
        return "not_determinable"
    return "before" if delta < 0 else "after"
