"""Time anomalies (FR-55, TC-TS-05).

The detectors report contradictions. The tests that matter are the ones pinning what they
must *not* do: name a cause, or let the one claim CLAUDE.md §17 explicitly rejects back in
through the wording.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from spectra.core.casestore import CaseMeta, CaseStore
from spectra.report.negative import negative_findings
from spectra.services import evidence as ev
from spectra.services import identify as ident
from spectra.services import parse as ps
from spectra.services import timeline as ts
from spectra.timeline import anomalies as anom
from spectra.timeline import correlate as correlate_mod
from tests import hikgen
from tests.test_audit import fixed_clock

T0 = datetime(2026, 3, 5, 14, 0, 0)


def seg(rec_id: str, channel: int | None, start: datetime | None,
        end: datetime | None, position: int = 0, extent: str = "") -> anom.Segment:
    return anom.Segment(rec_id, channel, start, end, position, extent or rec_id)


# --- duplicate windows ------------------------------------------------------------------


def test_two_recordings_claiming_one_minute_on_one_channel_is_a_contradiction() -> None:
    found = anom.duplicate_windows([
        seg("REC-1", 1, T0, T0 + timedelta(minutes=30)),
        seg("REC-2", 1, T0 + timedelta(minutes=10), T0 + timedelta(minutes=40)),
    ])
    assert len(found) == 1
    assert found[0].kind == "duplicate_window"
    assert found[0].numbers["overlap_s"] == 1200.0


def test_the_same_bytes_described_twice_is_not_a_clock_anomaly() -> None:
    """That is a merge defect, and reporting it here would point at the wrong problem."""
    found = anom.duplicate_windows([
        seg("REC-1", 1, T0, T0 + timedelta(minutes=30), extent="same"),
        seg("REC-2", 1, T0 + timedelta(minutes=10), T0 + timedelta(minutes=40),
            extent="same"),
    ])
    assert found == []


def test_overlap_across_different_channels_is_normal() -> None:
    """Channels record simultaneously; that is the point of a multi-channel recorder."""
    found = anom.duplicate_windows([
        seg("REC-1", 1, T0, T0 + timedelta(minutes=30)),
        seg("REC-2", 2, T0, T0 + timedelta(minutes=30)),
    ])
    assert found == []


def test_recordings_without_a_time_are_skipped_not_guessed_at() -> None:
    found = anom.duplicate_windows([
        seg("REC-1", 1, None, None),
        seg("REC-2", 1, T0, T0 + timedelta(minutes=30)),
    ])
    assert found == []


# --- backwards sequence -----------------------------------------------------------------


def test_later_on_disk_but_earlier_in_time_is_flagged() -> None:
    found = anom.backwards_sequence([
        seg("REC-1", 1, T0 + timedelta(hours=2), T0 + timedelta(hours=3), position=0),
        seg("REC-2", 1, T0, T0 + timedelta(hours=1), position=1),
    ])
    assert len(found) == 1
    assert found[0].numbers["backwards_s"] == 7200.0


def test_forward_progress_on_disk_and_clock_is_not_flagged() -> None:
    found = anom.backwards_sequence([
        seg("REC-1", 1, T0, T0 + timedelta(hours=1), position=0),
        seg("REC-2", 1, T0 + timedelta(hours=2), T0 + timedelta(hours=3), position=1),
    ])
    assert found == []


# --- before volume format: the claim that must not be made ------------------------------


def test_footage_older_than_the_volume_format_is_reported_as_an_observation() -> None:
    found = anom.before_volume_format(
        [seg("REC-1", 1, T0, T0 + timedelta(hours=1))], T0 + timedelta(days=2)
    )
    assert len(found) == 1
    assert found[0].kind == "before_volume_format"


def test_it_never_claims_footage_was_destroyed() -> None:
    """CLAUDE.md §17 rejects 'timestamp < volume init => footage destroyed' explicitly.

    A dead RTC resetting on power-up writes genuinely new footage with an old date and
    produces exactly this pattern with nothing deleted.
    """
    found = anom.before_volume_format(
        [seg("REC-1", 1, T0, T0 + timedelta(hours=1))], T0 + timedelta(days=2)
    )
    detail = found[0].detail.lower()
    assert "does not establish that anything was deleted" in detail
    for forbidden in ("destroyed", "wiped", "erased", "proves", "confirms"):
        assert forbidden not in detail, f"{forbidden!r} overstates what this shows"
    assert any("battery" in c or "RTC" in c or "wrong" in c
               for c in found[0].possible_causes)


def test_no_format_time_means_no_finding_rather_than_a_guess() -> None:
    found = anom.before_volume_format([seg("REC-1", 1, T0, T0)], None)
    assert found == []


# --- future dated and zero length -------------------------------------------------------


def test_footage_dated_after_acquisition_is_impossible_as_recorded() -> None:
    found = anom.future_dated(
        [seg("REC-1", 1, T0 + timedelta(days=1), T0 + timedelta(days=1))], T0
    )
    assert len(found) == 1
    assert "at least one of the two times is wrong" in found[0].detail


def test_a_zero_length_recording_is_reported(tmp_path: Path) -> None:
    found = anom.zero_length([seg("REC-1", 1, T0, T0)])
    assert len(found) == 1
    assert found[0].numbers["affected"] == 1


# --- ends before start ------------------------------------------------------------------


def test_a_recording_that_ends_before_it_starts_is_reported() -> None:
    found = anom.ends_before_start([
        seg("REC-1", 1, T0, T0 - timedelta(hours=2)),
        seg("REC-2", 1, T0, T0 + timedelta(minutes=5)),
        seg("REC-3", 1, None, None),
    ])
    assert len(found) == 1
    assert found[0].kind == "ends_before_start"
    assert found[0].recording_ids == ("REC-1",)
    assert found[0].numbers == {"affected": 1, "worst_s": 7200.0}


def test_an_inverted_recording_is_not_also_reported_as_an_overlap() -> None:
    """It has no interval, so it overlaps nothing; the overlap maths on it was nonsense
    ("overlapping by -4200 s")."""
    found = anom.detect([
        seg("REC-2", 1, T0, T0 + timedelta(minutes=30), position=0),
        seg("REC-1", 1, T0 + timedelta(minutes=10), T0 - timedelta(hours=1), position=1),
    ])
    assert [a.kind for a in found] == ["ends_before_start"]


def test_an_inverted_index_entry_is_reported_and_breaks_nothing_downstream(
    tmp_path: Path,
) -> None:
    """The same shape from a second family: timeline, gaps and the report all cope, and
    each says what it left out rather than crashing or drawing a negative-length bar."""
    path = tmp_path / "hik.raw"
    path.write_bytes(hikgen.volume(recordings=[
        (1, T0, T0 - timedelta(hours=2)),
        (1, T0 + timedelta(hours=1), T0 + timedelta(hours=2)),
    ]))
    store = CaseStore.create(
        tmp_path / "case", CaseMeta("CASE-A-3"), "examiner-1", clock=fixed_clock()
    )
    ingest = ev.import_image(store, path, "A")
    ident.run_identify(store, ingest.evidence_id)
    ps.parse(store, ingest.evidence_id)

    assert [a.kind for a in ts.time_anomalies(store, ingest.evidence_id)] == [
        "ends_before_start"
    ]
    assert "NF-TIME-ENDS-BEFORE-START" in {f.code for f in negative_findings(store)}

    (entry,) = ts.gap_report(store, ingest.evidence_id)["evidence"]
    assert [p["recording_id"] for p in entry["not_placed"]] == ["REC-0001"]
    assert entry["not_placed"][0]["reason"] == correlate_mod.ENDS_BEFORE_START

    ts.set_offset(store, ingest.evidence_id, "B_reference_capture",
                  device_time="2026-03-05T14:22:00", true_time="2026-03-05T14:04:18Z")
    data = ts.timeline_data(store, ingest.evidence_id)
    assert [s["recording_id"] for lane in data["lanes"] for s in lane["segments"]] == [
        "REC-0002"
    ]
    assert [(u["recording_id"], u["reason"]) for u in data["unplaced"]] == [
        ("REC-0001", correlate_mod.ENDS_BEFORE_START)
    ]
    store.close()


# --- every anomaly offers more than one explanation -------------------------------------


def test_no_detector_ever_states_a_single_cause() -> None:
    """A symptom handed over with one implied cause is a conclusion in disguise."""
    for kind, causes in anom.POSSIBLE_CAUSES.items():
        assert len(causes) >= 2, f"{kind} implies a single explanation"


def test_every_anomaly_carries_its_causes() -> None:
    found = anom.detect(
        [
            seg("REC-1", 1, T0, T0 + timedelta(minutes=30), position=0),
            seg("REC-2", 1, T0 + timedelta(minutes=10), T0 + timedelta(minutes=40),
                position=1),
        ],
        format_time=T0 + timedelta(days=2),
    )
    assert found
    for anomaly in found:
        assert anomaly.possible_causes, f"{anomaly.kind} has no stated explanations"


def test_detection_is_deterministically_ordered() -> None:
    segments = [
        seg("REC-3", 2, T0 + timedelta(hours=2), T0 + timedelta(hours=3), position=0),
        seg("REC-1", 1, T0, T0 + timedelta(minutes=30), position=1),
        seg("REC-2", 1, T0 + timedelta(minutes=10), T0 + timedelta(minutes=40), position=2),
    ]
    first = [a.to_json() for a in anom.detect(segments)]
    second = [a.to_json() for a in anom.detect(list(reversed(segments)))]
    assert first == second


# --- through the service and into the report ---------------------------------------------


def test_anomalies_reach_the_report_as_negative_findings(tmp_path: Path) -> None:
    path = tmp_path / "hik.raw"
    path.write_bytes(hikgen.volume(recordings=[
        (1, T0, T0 + timedelta(minutes=30)),
        (1, T0 + timedelta(minutes=10), T0 + timedelta(minutes=40)),
    ]))
    store = CaseStore.create(
        tmp_path / "case", CaseMeta("CASE-A-1"), "examiner-1", clock=fixed_clock()
    )
    ingest = ev.import_image(store, path, "A")
    ident.run_identify(store, ingest.evidence_id)
    ps.parse(store, ingest.evidence_id)

    found = ts.time_anomalies(store, ingest.evidence_id)
    assert any(a.kind == "duplicate_window" for a in found)

    codes = {f.code for f in negative_findings(store)}
    assert "NF-TIME-DUPLICATE-WINDOW" in codes
    store.close()


def test_a_well_behaved_case_produces_no_anomalies(tmp_path: Path) -> None:
    """The detectors must be quiet on ordinary evidence or nobody will read them."""
    path = tmp_path / "hik.raw"
    path.write_bytes(hikgen.volume(recordings=[
        (1, T0, T0 + timedelta(minutes=30)),
        (2, T0 + timedelta(minutes=5), T0 + timedelta(minutes=35)),
    ]))
    store = CaseStore.create(
        tmp_path / "case", CaseMeta("CASE-A-2"), "examiner-1", clock=fixed_clock()
    )
    ingest = ev.import_image(store, path, "A")
    ident.run_identify(store, ingest.evidence_id)
    ps.parse(store, ingest.evidence_id)
    assert ts.time_anomalies(store, ingest.evidence_id) == []
    store.close()
