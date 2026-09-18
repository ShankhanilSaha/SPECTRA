"""AnalyticsService — Stage 1 motion gating (FR-90, FR-95, FR-96, FR-97, TC-ML-01..03).

These tests run on **real encoded video**, because the thing most worth testing here is
that the service looks at pixels at all.

An earlier version of this service differenced each frame's compressed payload as though
it were a greyscale buffer. Its tests passed, because they fed synthetic flat-byte
payloads and asserted only that some segments came out. The test that catches that bug is
the static-scene one below: coded bytes vary frame to frame whatever the camera sees, so a
bitstream differ reports motion on a motionless scene, and a real one does not.
"""

from __future__ import annotations

import hashlib

import pytest

from spectra.core.casestore import CaseMeta, CaseStore
from spectra.core.media import MediaTool
from spectra.ml.motion import MotionConfig
from spectra.services import ServiceError
from spectra.services import analytics as an
from spectra.services import evidence as ev
from spectra.services import identify as ident
from spectra.services import parse as ps
from tests import dhavgen
from tests.media_fixtures import (
    STATIC_SOURCE,
    encode_test_stream,
    require_ffmpeg,
    split_access_units,
)
from tests.test_audit import fixed_clock


@pytest.fixture
def store(tmp_path):
    case = CaseStore.create(
        tmp_path / "case", CaseMeta("CASE-AN-1"), "examiner-1", clock=fixed_clock()
    )
    yield case
    case.close()


def case_with_video(store: CaseStore, tmp_path, *, source: str | None = None,
                    frames: int = 75) -> str:
    """Ingest a real encoded stream wrapped in DHAV framing, and return its recording id."""
    kwargs = {"source": source} if source else {}
    reference = encode_test_stream(tmp_path, "h264", frames=frames, **kwargs)
    units = split_access_units(reference, "h264")
    dav, _ = dhavgen.stream(units, channel=0, codec_id=dhavgen.CODEC_H264)
    usb = tmp_path / "usb"
    usb.mkdir(exist_ok=True)
    (usb / "NVR_ch1_main_20260305143210.dav").write_bytes(dav)

    ingest = ev.import_files(store, usb, label="test export")
    assert ingest.sha256 == hashlib.sha256(dav).hexdigest()
    ident.run_identify(store, ingest.evidence_id)
    ps.parse(store, ingest.evidence_id)
    (rec,) = ps.recording_rows(store, ingest.evidence_id)
    return rec["id"]


def media_for(store: CaseStore) -> MediaTool:
    return MediaTool(require_ffmpeg(), store.root / "logs")


# --- it looks at pixels -----------------------------------------------------------------


@pytest.mark.ffmpeg
def test_tc_ml_02_a_static_scene_produces_no_motion(store: CaseStore, tmp_path):
    """The test the old implementation could not have passed.

    A motionless grey scene still produces different coded bytes in every frame, so a
    bitstream differ calls it motion. A decoder-backed one finds nothing.
    """
    rec_id = case_with_video(store, tmp_path, source=STATIC_SOURCE)
    summary = an.run_motion_analysis(
        store, rec_id, config=MotionConfig(sensitivity=10, min_area_pixels=50),
        media=media_for(store),
    )
    assert summary.total_frames > 0, "the stream must actually have decoded"
    assert summary.motion_frames == 0, (
        "a motionless scene reported motion — this is the signature of differencing "
        "compressed bytes rather than decoded pixels"
    )
    assert summary.segments == ()


@pytest.mark.ffmpeg
def test_tc_ml_03_a_moving_scene_produces_motion(store: CaseStore, tmp_path):
    rec_id = case_with_video(store, tmp_path)
    summary = an.run_motion_analysis(
        store, rec_id, config=MotionConfig(sensitivity=10, min_area_pixels=50),
        media=media_for(store),
    )
    assert summary.motion_frames > 0
    assert summary.segments, "an animated scene should yield at least one activity segment"


@pytest.mark.ffmpeg
def test_the_frames_analysed_are_the_sampled_ones_not_the_coded_ones(
    store: CaseStore, tmp_path
):
    """75 frames at 25 fps is 3 seconds; at 2 samples a second that is far fewer frames.

    If `total_frames` ever equals the coded frame count again, the service has gone back
    to reading access units instead of decoding.
    """
    rec_id = case_with_video(store, tmp_path, frames=75)
    summary = an.run_motion_analysis(store, rec_id, media=media_for(store))
    assert summary.total_frames < 75
    assert summary.sampled_fps == an.MOTION_SAMPLE_FPS


# --- it refuses rather than approximating -----------------------------------------------


@pytest.mark.ffmpeg
def test_tc_ml_01_without_ffmpeg_it_refuses_instead_of_guessing(store: CaseStore, tmp_path):
    """No decoder means no result. A motion list nobody can trace to pixels is worse than
    none, because the absence is visible and the wrong answer is not."""
    rec_id = case_with_video(store, tmp_path)
    with pytest.raises(ServiceError, match="needs FFmpeg"):
        an.run_motion_analysis(store, rec_id, media=None)
    assert an.recording_annotations(store, rec_id, source="motion") == []


def test_an_unknown_recording_is_refused(store: CaseStore):
    with pytest.raises(ServiceError, match="no such recording"):
        an.run_motion_analysis(store, "REC-9999")


# --- what it records ---------------------------------------------------------------------


@pytest.mark.ffmpeg
def test_annotations_are_persisted_and_carry_the_lead_only_framing(
    store: CaseStore, tmp_path
):
    rec_id = case_with_video(store, tmp_path)
    summary = an.run_motion_analysis(
        store, rec_id, config=MotionConfig(sensitivity=10, min_area_pixels=50),
        media=media_for(store),
    )
    stored = an.recording_annotations(store, rec_id, source="motion")
    assert len(stored) == len(summary.annotations)
    for row in stored:
        assert row["source"] == "motion"
        assert row["recording_id"] == rec_id


@pytest.mark.ffmpeg
def test_the_result_states_how_the_pixels_were_obtained(store: CaseStore, tmp_path):
    """A sampled analysis that does not say it sampled is overstating its coverage."""
    rec_id = case_with_video(store, tmp_path)
    summary = an.run_motion_analysis(store, rec_id, media=media_for(store))
    assert "sampled at" in summary.decode_note
    assert "can fall between samples" in summary.decode_note


@pytest.mark.ffmpeg
def test_the_decode_parameters_reach_the_audit_chain(store: CaseStore, tmp_path):
    """Another examiner must be able to see what was analysed, not just the conclusion."""
    rec_id = case_with_video(store, tmp_path)
    an.run_motion_analysis(store, rec_id, media=media_for(store))
    records = [r for r in store.audit.records() if r.action == "analytics.motion.start"]
    assert records
    params = records[-1].params
    assert params["sampled_fps"] == an.MOTION_SAMPLE_FPS
    assert params["analysis_resolution"] == [an.MOTION_WIDTH, an.MOTION_HEIGHT]
    assert "ffmpeg" in params
    assert store.audit.verify().ok


@pytest.mark.ffmpeg
def test_the_decoded_frames_are_not_left_behind(store: CaseStore, tmp_path):
    """They are a working derivative, not an artefact, and must never be exportable."""
    rec_id = case_with_video(store, tmp_path)
    an.run_motion_analysis(store, rec_id, media=media_for(store))
    leftovers = list((store.root / "logs").glob("spectra-motion-*"))
    assert leftovers == []
    kinds = {r[0] for r in store.conn.execute("SELECT kind FROM artifact")}
    assert "gray" not in kinds and "es" not in kinds
