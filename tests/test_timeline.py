"""Time model, gaps and correlation (FR-50..FR-53, FR-58, FR-60; AC-07, AC-11).

The doc 8 §4 demo vector runs through most of these: the device OSD read 14:22:00 while a
reference clock read 14:04:18 UTC, so the device clock is ahead of true time by
+00:17:42 (1062 s).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from spectra.core.casestore import CaseMeta, CaseStore
from spectra.core.models import DeviceTime, ReferenceTime
from spectra.services import ServiceError
from spectra.services import evidence as ev
from spectra.services import identify as ident
from spectra.services import parse as ps
from spectra.services import timeline as tls
from spectra.timeline import correlate as correlate_mod
from spectra.timeline.gaps import CoverageSegment, channel_gaps, synchronised_gaps
from spectra.timeline.offset import observe
from spectra.timeline.timemodel import (
    REFUSAL_BANNER,
    ClockModel,
    TimeModelError,
    format_offset,
    not_established,
    ordering,
)
from tests import dhavgen
from tests.test_audit import fixed_clock

DEVICE_READ = datetime(2026, 3, 5, 14, 22, 0)
TRUE_READ = datetime(2026, 3, 5, 14, 4, 18, tzinfo=UTC)
DEMO_OFFSET_S = 1062.0  # device ahead of true time by +00:17:42


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


def demo_model(**kwargs) -> ClockModel:
    return ClockModel.from_observations(
        [observe("B_reference_capture", DEVICE_READ, TRUE_READ, note="GPS clock filmed")],
        **kwargs,
    )


# -- offset arithmetic (FR-51) -----------------------------------------------------------------

def test_demo_vector_offset_and_phrase():
    obs = observe("B_reference_capture", DEVICE_READ, TRUE_READ)
    assert obs.total_offset_s == DEMO_OFFSET_S
    assert format_offset(DEMO_OFFSET_S) == "+00:17:42"
    segment = demo_model().segments[0]
    assert "ahead of true time by 00:17:42" in segment.note
    assert "method B (reference-clock capture)" in segment.note


def test_normalise_subtracts_the_offset_with_the_method_uncertainty():
    ref = demo_model().normalise(datetime(2026, 3, 5, 14, 32, 10))
    assert ref.utc == utc(2026, 3, 5, 14, 14, 28)
    assert ref.uncertainty_s == 1.0
    assert ref.method == "B_reference_capture"
    assert ref.tz_offset_s is None
    assert ref.clock_offset_s == DEMO_OFFSET_S
    assert "timezone not stated" in ref.derivation_note


def test_stated_timezone_splits_the_bundle():
    ref = demo_model(tz_offset_s=19_800).normalise(datetime(2026, 3, 5, 14, 32, 10))
    assert ref.tz_offset_s == 19_800
    assert ref.clock_offset_s == DEMO_OFFSET_S - 19_800
    assert ref.utc == utc(2026, 3, 5, 14, 14, 28)  # the split never moves the result


def test_method_uncertainty_floors_are_enforced():
    with pytest.raises(TimeModelError, match="cannot claim better"):
        observe("B_reference_capture", DEVICE_READ, TRUE_READ, uncertainty_s=0.1)
    assert observe("C_external_event", DEVICE_READ, TRUE_READ).uncertainty_s == 60.0


def test_true_time_must_be_absolute_and_device_time_naive():
    with pytest.raises(TimeModelError, match="timezone-aware"):
        observe("B_reference_capture", DEVICE_READ, TRUE_READ.replace(tzinfo=None))
    with pytest.raises(TimeModelError, match="naive"):
        observe("B_reference_capture", DEVICE_READ.replace(tzinfo=UTC), TRUE_READ)


# -- refusal (FR-53) ---------------------------------------------------------------------------

def test_no_evidence_refuses_absolute_time():
    ref = ClockModel(segments=()).normalise(datetime(2026, 3, 5, 14, 32, 10))
    assert ref.utc is None
    assert ref.method == "none"
    assert ref.derivation_note.startswith(REFUSAL_BANNER)


def test_undecodable_and_missing_device_times_refuse():
    model = demo_model()
    undecodable = DeviceTime(raw=0xFFFFFFFF, encoding="dhav_packed", local=None)
    assert model.normalise(undecodable).utc is None
    assert "ffffffff" in model.normalise(undecodable).derivation_note.replace("4294967295", "")\
        or "4294967295" in model.normalise(undecodable).derivation_note
    assert model.normalise(None).utc is None


def test_reference_time_contract_rejects_asserted_time_without_method():
    with pytest.raises(ValueError, match="FR-53"):
        ReferenceTime(utc=utc(2026, 1, 1), uncertainty_s=1.0, method="none",
                      tz_offset_s=None, clock_offset_s=None, derivation_note="")


# -- per-segment offsets: manual clock change (doc 3 §8) ---------------------------------------

def test_manual_clock_change_means_per_segment_offsets():
    change = datetime(2026, 3, 5, 12, 0, 0)
    before = observe("B_reference_capture", datetime(2026, 3, 5, 10, 0, 0),
                     utc(2026, 3, 5, 9, 0, 0), valid_to=change)       # ahead 1 h
    after = observe("B_reference_capture", datetime(2026, 3, 5, 14, 0, 0),
                    utc(2026, 3, 5, 13, 30, 0), valid_from=change)    # ahead 30 min
    model = ClockModel.from_observations([after, before])
    assert model.normalise(datetime(2026, 3, 5, 11, 0, 0)).utc == utc(2026, 3, 5, 10, 0, 0)
    assert model.normalise(datetime(2026, 3, 5, 15, 0, 0)).utc == utc(2026, 3, 5, 14, 30, 0)


def test_unbounded_overlapping_observations_are_rejected():
    first = observe("B_reference_capture", DEVICE_READ, TRUE_READ)
    second = observe("D_live_rtc", DEVICE_READ + timedelta(hours=1),
                     TRUE_READ + timedelta(hours=1))
    with pytest.raises(TimeModelError, match="per-segment"):
        ClockModel.from_observations([first, second])


def test_time_outside_every_segment_refuses():
    bounded = observe("B_reference_capture", DEVICE_READ, TRUE_READ,
                      valid_from=datetime(2026, 3, 5, 14, 0, 0),
                      valid_to=datetime(2026, 3, 5, 15, 0, 0))
    model = ClockModel.from_observations([bounded])
    assert model.normalise(datetime(2026, 3, 5, 16, 0, 0)).utc is None


# -- uncertainty-safe ordering (FR-52) ---------------------------------------------------------

def _ref(moment: datetime, uncertainty_s: float) -> ReferenceTime:
    return ReferenceTime(utc=moment, uncertainty_s=uncertainty_s, method="B_reference_capture",
                         tz_offset_s=None, clock_offset_s=0.0, derivation_note="test")


def test_ordering_refuses_inside_combined_uncertainty():
    base = utc(2026, 3, 5, 14, 0, 0)
    a = _ref(base, 1.0)
    b = _ref(base + timedelta(seconds=20), 30.0)  # ±31 s combined
    assert ordering(a, b) == "not_determinable"
    c = _ref(base + timedelta(seconds=40), 30.0)
    assert ordering(a, c) == "before"
    assert ordering(c, a) == "after"
    assert ordering(a, not_established("x")) == "not_determinable"


# -- gap analysis (FR-58) ----------------------------------------------------------------------

def seg(channel: int, start_min: int, end_min: int, u: float = 0.0,
        rec: str = "REC") -> CoverageSegment:
    base = utc(2026, 3, 5, 10, 0, 0)
    return CoverageSegment(channel, base + timedelta(minutes=start_min),
                           base + timedelta(minutes=end_min), u, f"{rec}-{channel}-{start_min}")


def test_channel_gaps_respect_threshold_and_uncertainty():
    segments = [seg(1, 0, 10), seg(1, 20, 30), seg(2, 0, 30)]
    gaps = channel_gaps(segments, min_gap_s=60.0)
    assert [(g.channel, g.duration_s) for g in gaps] == [(1, 600.0)]
    # ±30 s on both sides shrinks the assertable gap by a minute
    shrunk = channel_gaps([seg(1, 0, 10, 30.0), seg(1, 20, 30, 30.0)], min_gap_s=60.0)
    assert shrunk[0].duration_s == 540.0
    # and a gap smaller than the combined uncertainty is not claimed at all
    assert not channel_gaps([seg(1, 0, 10, 300.0), seg(1, 20, 30, 300.0)], min_gap_s=60.0)


def test_synchronised_gap_across_all_channels_is_a_finding():
    segments = [seg(1, 0, 10), seg(1, 50, 60), seg(2, 0, 12), seg(2, 50, 58)]
    gaps = synchronised_gaps(segments, min_gap_s=60.0)
    assert len(gaps) == 1
    assert gaps[0].synchronised and gaps[0].channel is None
    assert gaps[0].duration_s == pytest.approx(38 * 60)
    assert not synchronised_gaps([seg(1, 0, 10), seg(1, 50, 60)])  # one channel: never


# -- correlation (FR-60) -----------------------------------------------------------------------

def test_correlate_places_normalised_and_reports_unplaced():
    ok_start, ok_end = _ref(utc(2026, 3, 5, 10, 0), 1.0), _ref(utc(2026, 3, 5, 10, 5), 1.0)
    result = correlate_mod.correlate([
        ("EV-001", "REC-0001", 1, ok_start, ok_end),
        ("EV-002", "REC-0002", 1, not_established("no offset evidence"), ok_end),
    ])
    assert [lane.label for lane in result.lanes] == ["EV-001 ch 1"]
    assert result.lanes[0].segments[0].uncertainty_s == 1.0
    assert result.unplaced[0].recording_id == "REC-0002"
    assert REFUSAL_BANNER in result.unplaced[0].reason


# -- service: the AC-07 shape, end to end ------------------------------------------------------

@pytest.fixture
def store(tmp_path):
    case = CaseStore.create(tmp_path / "case", CaseMeta("CASE-T-1"), "examiner-1",
                            clock=fixed_clock())
    yield case
    case.close()


def parsed_dahua_evidence(store, tmp_path) -> str:
    units = [(b"\x00\x00\x00\x01\x65" + bytes([i]) * 60, i % 5 == 0) for i in range(10)]
    dav, _ = dhavgen.stream(units, channel=2)  # device-local start 2026-03-05T14:32:10
    root = tmp_path / "usb"
    root.mkdir()
    (root / "a.dav").write_bytes(dav)
    evidence_id = ev.import_files(store, root).evidence_id
    ident.run_identify(store, evidence_id)
    ps.parse(store, evidence_id)
    return evidence_id


def test_known_offset_is_recovered_within_stated_uncertainty(store, tmp_path):
    """AC-07: the DHAV device clock is ahead by exactly +00:17:42; one method-B observation
    must land every recording on the true axis within ±1 s."""
    evidence_id = parsed_dahua_evidence(store, tmp_path)
    summary = tls.set_offset(
        store, evidence_id, "B_reference_capture",
        device_time="2026-03-05T14:22:00", true_time="2026-03-05T14:04:18Z",
        note="GPS clock filmed at seizure",
    )
    assert summary.recordings_normalised == 1 and summary.recordings_refused == 0
    row = ps.recording_rows(store, evidence_id)[0]
    truth = datetime.fromisoformat(row["t_local_start"]).replace(tzinfo=UTC) \
        - timedelta(seconds=DEMO_OFFSET_S)
    got = datetime.fromisoformat(row["t_ref_start"])
    assert abs((got - truth).total_seconds()) <= row["t_uncertainty_s"]
    assert row["t_method"] == "B_reference_capture"
    actions = [r.action for r in store.audit.records()]
    assert "time.set.start" in actions and "time.set.complete" in actions


def test_without_observations_everything_stays_device_local(store, tmp_path):
    evidence_id = parsed_dahua_evidence(store, tmp_path)
    row = ps.recording_rows(store, evidence_id)[0]
    assert row["t_ref_start"] is None and row["t_method"] == "none"
    data = tls.timeline_data(store, evidence_id)
    assert data["lanes"] == []
    assert REFUSAL_BANNER in data["unplaced"][0]["reason"]
    gaps = tls.gap_report(store, evidence_id)
    assert gaps["evidence"][0]["basis"] == "device-local"
    assert gaps["evidence"][0]["banner"] == REFUSAL_BANNER


def test_bad_offset_input_is_refused_and_leaves_the_case_consistent(store, tmp_path):
    evidence_id = parsed_dahua_evidence(store, tmp_path)
    with pytest.raises(ServiceError, match="timezone"):
        tls.set_offset(store, evidence_id, "B_reference_capture",
                       "2026-03-05T14:22:00", "2026-03-05T14:04:18")  # naive true time
    with pytest.raises(ServiceError, match="cannot claim better"):
        tls.set_offset(store, evidence_id, "B_reference_capture",
                       "2026-03-05T14:22:00", "2026-03-05T14:04:18Z", uncertainty_s=0.01)
    assert tls.observation_rows(store, evidence_id) == []
    assert store.verify().ok


def test_timeline_and_gap_outputs_are_deterministic(store, tmp_path):
    """AC-11 shape: two reads of the same case serialise byte-identically."""
    evidence_id = parsed_dahua_evidence(store, tmp_path)
    tls.set_offset(store, evidence_id, "B_reference_capture",
                   "2026-03-05T14:22:00", "2026-03-05T14:04:18Z")
    first = (tls.to_json(tls.timeline_data(store)), tls.to_json(tls.gap_report(store)))
    second = (tls.to_json(tls.timeline_data(store)), tls.to_json(tls.gap_report(store)))
    assert first == second
