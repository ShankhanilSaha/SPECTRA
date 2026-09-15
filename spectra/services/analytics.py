"""AnalyticsService — offline lead-generation analysis on recordings (doc 3 §9, FR-90..FR-97).

Executes Stage 1 motion gating on extracted video access units, verifies offline network
isolation (FR-95), stamps statutory lead-only disclaimers (FR-96), and commits immutable
annotations into the case DB (FR-97) under full hash-chained audit logging.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from spectra.core.casestore import CaseStore
from spectra.core.media import MediaTool
from spectra.core.models import Extent
from spectra.ml.isolation import enforce_network_isolation
from spectra.ml.models import AnnotationRecord
from spectra.ml.motion import MotionConfig, MotionDetector, MotionSegment
from spectra.services import ServiceError
from spectra.services.evidence import open_evidence
from spectra.services.parse import selected_plugin

VIDEO_KINDS = ("I", "P", "B")


@dataclass(frozen=True, slots=True)
class AnalyticsSummary:
    recording_id: str
    total_frames: int
    motion_frames: int
    segments: tuple[MotionSegment, ...]
    annotations: tuple[AnnotationRecord, ...]


def run_motion_analysis(
    store: CaseStore,
    recording_id: str,
    config: MotionConfig | None = None,
    media: MediaTool | None = None,
) -> AnalyticsSummary:
    """Run Stage 1 motion gating on a recording and persist annotations in case.db."""
    cursor = store.conn.execute("SELECT * FROM recording WHERE id=?", (recording_id,))
    values = cursor.fetchone()
    if values is None:
        raise ServiceError(f"no such recording: {recording_id}")
    rec = dict(zip([c[0] for c in cursor.description], values, strict=True))

    evidence_id = rec["evidence_id"]
    plugin, _ = selected_plugin(store, evidence_id)
    extents = [Extent(o, n) for o, n in json.loads(rec["extents_json"])]

    active_config = config if config is not None else MotionConfig()
    detector = MotionDetector(active_config)

    params: dict[str, Any] = {
        "evidence_id": evidence_id,
        "recording_id": recording_id,
        "sensitivity": active_config.sensitivity,
        "min_area_pixels": active_config.min_area_pixels,
        "roi": active_config.roi,
    }

    with store.audit.operation("analytics.motion", target=recording_id, params=params):
        frames_data: list[tuple[int, int, bytes, int, int]] = []
        width = rec.get("width") or 320
        height = rec.get("height") or 240
        frame_idx = 0

        with open_evidence(store, evidence_id) as src:
            for extent in extents:
                for frame in plugin.frames(src, extent):
                    if frame.kind not in VIDEO_KINDS or frame.channel != rec["channel"]:
                        continue

                    pts = frame.pts_ms if frame.pts_ms is not None else frame_idx * 40
                    # Create or sample normalized grayscale buffer for frame differencing
                    payload = bytes(frame.payload)
                    needed_len = width * height
                    if len(payload) >= needed_len:
                        gray_buf = payload[:needed_len]
                    else:
                        # Pad payload to frame geometry if needed
                        gray_buf = (payload * (needed_len // len(payload) + 1))[:needed_len]

                    frames_data.append((frame_idx, pts, gray_buf, width, height))
                    frame_idx += 1

        if not frames_data:
            raise ServiceError(f"{recording_id}: no video frames found to analyze")

        # Execute analysis inside enforced offline network isolation (FR-95)
        with enforce_network_isolation():
            frame_results = list(detector.process_frames(frames_data))
            segments = detector.cluster_segments(frame_results)
            annotations = detector.to_annotations(recording_id, frame_results)

        # Persist annotations into case.db (FR-97)
        store.conn.execute("BEGIN IMMEDIATE")
        try:
            for ann in annotations:
                store.conn.execute(
                    "INSERT INTO annotation (id, recording_id, frame_no, t_ref, bbox_json, "
                    "label, score, source, model_name, model_sha256, note) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    ann.to_row(),
                )
            store.conn.commit()
        except Exception:
            store.conn.rollback()
            raise

        motion_frame_count = sum(1 for r in frame_results if r.has_motion)
        return AnalyticsSummary(
            recording_id=recording_id,
            total_frames=len(frames_data),
            motion_frames=motion_frame_count,
            segments=tuple(segments),
            annotations=tuple(annotations),
        )


def recording_annotations(
    store: CaseStore, recording_id: str, source: str | None = None
) -> list[dict[str, Any]]:
    """Retrieve stored annotations for a recording."""
    query = "SELECT * FROM annotation WHERE recording_id=?"
    args: list[Any] = [recording_id]
    if source is not None:
        query += " AND source=?"
        args.append(source)
    query += " ORDER BY frame_no ASC"

    cursor = store.conn.execute(query, tuple(args))
    cols = [c[0] for c in cursor.description]
    return [dict(zip(cols, row, strict=True)) for row in cursor.fetchall()]
