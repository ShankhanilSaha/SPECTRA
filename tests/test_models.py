"""Shared types refuse invalid states at construction (FR-03, FR-28, FR-45, FR-53)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from spectra.core.models import (
    DeviceTime,
    Extent,
    ProbePlan,
    ProbeResult,
    Recording,
    ReferenceTime,
)


def test_extent_arithmetic_and_validation():
    outer = Extent(100, 50)
    assert outer.end == 150
    assert outer.contains(Extent(100, 50)) and outer.contains(Extent(120, 0))
    assert not outer.contains(Extent(99, 10)) and not outer.contains(Extent(140, 11))
    assert outer.to_json() == [100, 50]
    for offset, length in ((-1, 5), (5, -1)):
        with pytest.raises(ValueError, match="invalid extent"):
            Extent(offset, length)


def test_device_time_keeps_raw_and_rejects_ambiguous_wall_clock():
    assert DeviceTime(1234, "unix_le32", None).raw_repr() == "1234"
    assert DeviceTime(b"\x01\xff", "packed", None).raw_repr() == "01ff"
    with pytest.raises(ValueError, match="encoding is required"):
        DeviceTime(1, "", None)
    with pytest.raises(ValueError, match="must be naive"):  # device clocks carry no timezone
        DeviceTime(1, "unix_le32", datetime(2026, 3, 5, 14, 0, tzinfo=UTC))


def test_reference_time_cannot_assert_absolute_time_without_a_method():
    """FR-53: no offset evidence ⇒ no absolute time, enforced by the type itself."""
    with pytest.raises(ValueError, match="cannot assert an absolute time"):
        ReferenceTime(datetime(2026, 3, 5, 8, 34, tzinfo=UTC), 1.0, "none", None, None, "")
    unknown = ReferenceTime(None, 0.0, "none", None, None, "no offset evidence")
    assert unknown.utc is None


@pytest.mark.parametrize("uncertainty", [-0.1, float("nan"), float("inf")])
def test_reference_time_uncertainty_must_be_finite_and_non_negative(uncertainty):
    with pytest.raises(ValueError, match="uncertainty_s"):
        ReferenceTime(None, uncertainty, "none", None, None, "")


def test_reference_time_utc_must_be_utc():
    ist = timezone(timedelta(hours=5, minutes=30))
    for bad in (datetime(2026, 3, 5, 14, 4, 18), datetime(2026, 3, 5, 14, 4, 18, tzinfo=ist)):
        with pytest.raises(ValueError, match="timezone-aware UTC"):
            ReferenceTime(bad, 1.0, "B_reference_capture", 19800, 1062.0, "F-1 capture")
    ok = ReferenceTime(datetime(2026, 3, 5, 8, 34, 18, tzinfo=UTC), 1.0, "B_reference_capture",
                       19800, 1062.0, "F-1 capture")
    assert ok.method == "B_reference_capture"


def test_probe_result_confidence_and_parse_support_rules():
    for confidence in (-0.01, 1.01):
        with pytest.raises(ValueError, match="confidence"):
            ProbeResult("dahua", "v1", confidence, False, (), "0.1.0")
    with pytest.raises(ValueError, match="recognised layout_version"):  # FR-03
        ProbeResult("dahua", None, 0.9, True, (), "0.1.0")
    assert not ProbeResult("dahua", None, 0.6, False, (), "0.1.0").parse_supported


def _recording(**overrides):
    fields = dict(channel=None, stream="unknown", t_start=None, t_end=None,
                  extents=(Extent(0, 10),), codec="unknown", resolution=None, fps=None,
                  size_bytes=10, recovery_tier="T3", confidence=0.4, source_note="carved")
    return Recording(**{**fields, **overrides})


def test_recording_allows_unknown_channel_and_time_but_not_empty_or_bad_values():
    carved = _recording()  # FR-45: time unknown and channel unknown are legal states
    assert carved.t_start is None and carved.channel is None
    with pytest.raises(ValueError, match="confidence"):
        _recording(confidence=1.5)
    with pytest.raises(ValueError, match="at least one extent"):
        _recording(extents=())


def test_probe_plan_regions_are_deduplicated_and_skip_empty_windows():
    head = Extent(0, 1024)
    plan = ProbePlan(lba0=Extent(0, 512), head=head, tail=head,
                     sweep=(Extent(2048, 0), Extent(4096, 512)))
    assert plan.all_regions() == (Extent(0, 512), head, Extent(4096, 512))
