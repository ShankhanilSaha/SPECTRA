"""SPECTRA ML & Offline Analytics Subsystem (doc 3 §9, doc 2 §5.8, FR-90..FR-97).

Offline-by-construction lead generation (motion gating, object detection, face clustering).
All results are leads, never identifications (Rule 12, FR-96).
"""

from __future__ import annotations

from spectra.ml.models import AnnotationRecord, ModelSpec, format_disclaimer

__all__ = ["AnnotationRecord", "ModelSpec", "format_disclaimer"]
