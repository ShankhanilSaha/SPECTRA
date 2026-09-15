"""Tests for AnalyticsService (spectra/services/analytics.py).

Verifies end-to-end execution of motion analysis, database persistence of annotations,
audit chain updates, and refusal behavior.
"""

from __future__ import annotations

import pytest

from spectra.core.casestore import CaseMeta, CaseStore
from spectra.ml.motion import MotionConfig
from spectra.services import ServiceError
from spectra.services import analytics as an
from spectra.services import evidence as ev
from spectra.services import identify as ident
from spectra.services import parse as ps
from tests import dhavgen
from tests.test_audit import fixed_clock

UNITS = [(b"\x00\x00\x00\x01\x65" + bytes([(i * 15) % 256]) * 500, i % 3 == 0) for i in range(15)]


@pytest.fixture
def store(tmp_path):
    case = CaseStore.create(
        tmp_path / "case", CaseMeta("CASE-AN-1"), "examiner-1", clock=fixed_clock()
    )
    yield case
    case.close()


def setup_case_with_recording(store: CaseStore, tmp_path) -> str:
    dav, _ = dhavgen.stream(UNITS, channel=0)
    usb_dir = tmp_path / "usb"
    usb_dir.mkdir(exist_ok=True)
    (usb_dir / "test.dav").write_bytes(dav)

    ingest = ev.import_files(store, usb_dir, label="test export")
    ident.run_identify(store, ingest.evidence_id)
    ps.parse(store, ingest.evidence_id)

    (rec,) = ps.recording_rows(store, ingest.evidence_id)
    return rec["id"]


def test_motion_analysis_persists_annotations_and_audits(store: CaseStore, tmp_path):
    rec_id = setup_case_with_recording(store, tmp_path)

    config = MotionConfig(sensitivity=10, min_area_pixels=50)
    summary = an.run_motion_analysis(store, rec_id, config=config)

    assert summary.recording_id == rec_id
    assert summary.total_frames == 15
    assert len(summary.annotations) > 0

    # Verify annotations in DB
    db_annotations = an.recording_annotations(store, rec_id, source="motion")
    assert len(db_annotations) == len(summary.annotations)
    for ann in db_annotations:
        assert ann["recording_id"] == rec_id
        assert ann["source"] == "motion"
        assert ann["label"] == "motion"
        assert "Not an identification." in ann["note"]

    # Verify audit chain
    verification = store.verify()
    assert verification.ok, verification.problems
    actions = [r.action for r in store.audit.records()]
    assert "analytics.motion.start" in actions
    assert "analytics.motion.complete" in actions


def test_motion_analysis_refuses_nonexistent_recording(store: CaseStore):
    with pytest.raises(ServiceError, match="no such recording: nonexistent-id"):
        an.run_motion_analysis(store, "nonexistent-id")
