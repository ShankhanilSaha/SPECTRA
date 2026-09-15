"""Constructing offset observations — methods A–D (doc 3 §8 table; FR-51).

Each method is the same shape of evidence: the device's wall clock read against true time
at one instant, with an uncertainty no better than the method's floor. What differs is
where the two readings come from:

  A  NTP synchronisation proved from firmware config + the system log        ±1 s
  B  reference clock (GPS/NTP) held in front of a live camera at seizure     ±1 s
     (SOP-1 step 6: the examiner enters the true time and marks the frame)
  C  externally timestamped event visible in frame (card swipe, POS, call)   ±2–60 s
  D  device RTC read from the live UI at a noted instant before power-down   ±1 s then,
     degrading as it is extrapolated

Method C defaults to the conservative end of its range unless the examiner states better;
method D beyond its floor is the examiner's statement about drift (proper drift modelling
is a finals item).
"""

from __future__ import annotations

from datetime import datetime

from spectra.core.models import OffsetMethod
from spectra.timeline.timemodel import METHOD_FLOOR_S, OffsetObservation, TimeModelError

C_DEFAULT_UNCERTAINTY_S = 60.0


def observe(
    method: OffsetMethod,
    device_local: datetime,
    true_time: datetime,
    uncertainty_s: float | None = None,
    note: str = "",
    valid_from: datetime | None = None,
    valid_to: datetime | None = None,
) -> OffsetObservation:
    """Build one offset observation, defaulting the uncertainty to the method's floor."""
    if method not in METHOD_FLOOR_S:
        raise TimeModelError(f"method {method!r} is not an offset method (FR-51)")
    if uncertainty_s is None:
        uncertainty_s = (
            C_DEFAULT_UNCERTAINTY_S if method == "C_external_event" else METHOD_FLOOR_S[method]
        )
    return OffsetObservation(
        method=method,
        device_local=device_local,
        true_utc=true_time,
        uncertainty_s=uncertainty_s,
        note=note,
        valid_from=valid_from,
        valid_to=valid_to,
    )
