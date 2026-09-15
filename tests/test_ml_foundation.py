"""Tests for SPECTRA ML Foundation (models, licenses, disclaimers, isolation).

Traces to FR-84, FR-95, FR-96, FR-97, and NFR-16.
"""

from __future__ import annotations

import hashlib
import socket
from pathlib import Path

import pytest

from spectra.ml.isolation import NetworkEgressForbidden, enforce_network_isolation
from spectra.ml.models import (
    BUILTIN_MOTION_SPEC,
    AnnotationRecord,
    ModelIntegrityError,
    ModelLicenseError,
    ModelSpec,
    format_disclaimer,
)


def test_license_gating_permits_court_compatible_licenses():
    for lic in ("Apache-2.0", "MIT", "BSD-2-Clause", "BSD-3-Clause", "OpenCV"):
        spec = ModelSpec("test-model", "1.0", "0" * 64, lic, "builtin")
        spec.validate_license()  # Should not raise


def test_license_gating_rejects_copyleft_and_noncommercial_licenses():
    for lic in ("AGPL-3.0", "GPL-3.0", "Non-Commercial", "CC-BY-NC-4.0", "Proprietary"):
        spec = ModelSpec("bad-model", "1.0", "0" * 64, lic, "onnx")
        with pytest.raises(ModelLicenseError, match="violates NFR-16"):
            spec.validate_license()


def test_model_file_sha256_verification(tmp_path: Path):
    dummy_model = tmp_path / "detector.onnx"
    dummy_model.write_bytes(b"SPECTRA_TEST_MODEL_WEIGHTS_42")
    correct_hash = hashlib.sha256(b"SPECTRA_TEST_MODEL_WEIGHTS_42").hexdigest()

    spec = ModelSpec("detector", "1.0.0", correct_hash, "Apache-2.0", "onnx")
    spec.verify_file(dummy_model)  # Should pass

    tampered_spec = ModelSpec("detector", "1.0.0", "1" * 64, "Apache-2.0", "onnx")
    with pytest.raises(ModelIntegrityError, match="SHA-256 mismatch"):
        tampered_spec.verify_file(dummy_model)


def test_lead_only_statutory_disclaimer_formatting():
    disclaimer = format_disclaimer(0.8765)
    assert "Machine-generated detection. Confidence 0.88." in disclaimer
    assert "Requires human verification against the source frame." in disclaimer
    assert "Not an identification." in disclaimer


def test_annotation_record_creation_and_row_mapping():
    ann = AnnotationRecord.create_motion(
        recording_id="rec-001",
        frame_no=42,
        t_ref="2026-03-05T14:32:10Z",
        bbox=[10, 20, 100, 150],
        score=0.95,
        spec=BUILTIN_MOTION_SPEC,
    )
    assert ann.id == "ann-rec-001-motion-f42"
    assert ann.source == "motion"
    assert ann.label == "motion"
    assert "Not an identification." in ann.note
    assert ann.model_name == BUILTIN_MOTION_SPEC.name

    row = ann.to_row()
    assert row[0] == ann.id
    assert row[1] == "rec-001"
    assert row[2] == 42
    assert row[4] == "[10, 20, 100, 150]"
    assert row[6] == 0.95
    assert row[7] == "motion"


def test_offline_network_isolation_blocks_egress():
    with enforce_network_isolation():
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        with pytest.raises(NetworkEgressForbidden, match="FR-95"):
            s.connect(("8.8.8.8", 53))
        s.close()
