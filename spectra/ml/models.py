"""Model registry, license verification, and disclaimer formatting (doc 3 §9, FR-84, FR-96).

Enforces:
1. Pinned model hashes: every model file has a SHA-256 verified at load time.
2. License gating: only commercially usable, court-acceptable open licenses (Apache-2.0, MIT, BSD)
   are permitted; copyleft (AGPL) and non-commercial models are rejected (NFR-16, doc 2 Q4).
3. Non-negotiable statutory disclaimer (FR-96) attached to every prediction.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

PERMITTED_LICENSES = frozenset({"Apache-2.0", "MIT", "BSD-2-Clause", "BSD-3-Clause", "OpenCV"})
FORBIDDEN_LICENSES = frozenset({"AGPL-3.0", "GPL-3.0", "Non-Commercial", "CC-BY-NC-4.0"})

DISCLAIMER_TEMPLATE = (
    "Machine-generated detection. Confidence {score:.2f}. "
    "Requires human verification against the source frame. Not an identification."
)


class ModelLicenseError(Exception):
    """Raised when a model license violates redistribution or court compliance rules (NFR-16)."""


class ModelIntegrityError(Exception):
    """Raised when a model file's SHA-256 does not match its pinned expected hash."""


@dataclass(frozen=True, slots=True)
class ModelSpec:
    name: str
    version: str
    sha256: str
    license: str
    framework: Literal["builtin", "opencv", "onnx"]
    description: str = ""

    def validate_license(self) -> None:
        if self.license in FORBIDDEN_LICENSES or self.license not in PERMITTED_LICENSES:
            allowed = ", ".join(sorted(PERMITTED_LICENSES))
            raise ModelLicenseError(
                f"Model {self.name!r} has license {self.license!r}, which violates NFR-16. "
                f"Only court-compatible permissive licenses ({allowed}) are permitted."
            )

    def verify_file(self, path: Path) -> None:
        if not path.is_file():
            raise FileNotFoundError(f"Model file not found: {path}")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest.lower() != self.sha256.lower():
            raise ModelIntegrityError(
                f"Model file {path.name} SHA-256 mismatch!\n"
                f"  Expected: {self.sha256}\n"
                f"  Actual:   {digest}"
            )


# Built-in baseline algorithm specs (always available, no external downloads)
BUILTIN_MOTION_SPEC = ModelSpec(
    name="spectra-motion-differencing",
    version="1.0.0",
    sha256="0" * 64,  # Builtin algorithmic method
    license="Apache-2.0",
    framework="builtin",
    description=(
        "Greyscale frame differencing: a pixel has changed when it moved by at least the "
        "sensitivity; motion when at least min_area_pixels changed (no ML, no smoothing)."
    ),
)


def format_disclaimer(score: float) -> str:
    """Return the non-negotiable statutory disclaimer required by FR-96."""
    return DISCLAIMER_TEMPLATE.format(score=score)


@dataclass(slots=True)
class AnnotationRecord:
    """Mirrors the case.db `annotation` table (doc 3 §11, FR-97)."""

    id: str
    recording_id: str
    frame_no: int
    t_ref: str | None
    bbox: list[int] | None  # [x, y, w, h]
    label: str
    score: float
    source: Literal["motion", "object", "face", "anpr", "human"]
    model_name: str
    model_sha256: str
    note: str

    @classmethod
    def create_motion(
        cls,
        *,
        recording_id: str,
        frame_no: int,
        t_ref: str | None = None,
        bbox: list[int] | None = None,
        score: float = 1.0,
        spec: ModelSpec = BUILTIN_MOTION_SPEC,
        extra_note: str = "",
    ) -> AnnotationRecord:
        disclaimer = format_disclaimer(score)
        note = f"{disclaimer} {extra_note}".strip() if extra_note else disclaimer
        ann_id = f"ann-{recording_id}-motion-f{frame_no}"
        return cls(
            id=ann_id,
            recording_id=recording_id,
            frame_no=frame_no,
            t_ref=t_ref,
            bbox=bbox,
            label="motion",
            score=round(score, 4),
            source="motion",
            model_name=spec.name,
            model_sha256=spec.sha256,
            note=note,
        )

    def to_row(self) -> tuple[Any, ...]:
        return (
            self.id,
            self.recording_id,
            self.frame_no,
            self.t_ref,
            json.dumps(self.bbox) if self.bbox is not None else None,
            self.label,
            self.score,
            self.source,
            self.model_name,
            self.model_sha256,
            self.note,
        )
