"""AnalyticsService — offline lead-generation analysis on recordings (doc 3 §9, FR-90..FR-97).

Runs Stage 1 motion gating on **decoded** frames, verifies offline network isolation
(FR-95), stamps the lead-only disclaimer (FR-96), and commits annotations into the case DB
(FR-97) under hash-chained audit logging.

## Why this decodes, and refuses if it cannot

Frame differencing needs pixels. An earlier version of this service took each frame's
compressed H.264/H.265 payload and treated it as a greyscale buffer, padding it out when
it was too short. That measures entropy-coded byte churn, not motion.

The reason it was not obviously broken is the reason it was dangerous: a video encoder
spends more bits on a moving scene, so byte churn correlates with real activity well
enough to produce segment lists that look right in a demo. It was a proxy for the wrong
quantity, wearing the output format of the right one — the confident wrong answer doc 6 §1
calls the only disqualifying failure.

So the decode is mandatory. Without FFmpeg this service raises, and no annotation is
written. A motion result that nobody can trace to decoded pixels is worse than no motion
result, because the second is visibly absent and the first is not.

Analysis runs on a derivative that exists only for the duration of the run and is deleted
afterwards. It is never exported and never presented as the evidence copy.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from spectra.core.casestore import CaseStore
from spectra.core.media import (
    DecodedGray,
    FrameIndexEntry,
    MediaTool,
    iter_gray_frames,
)
from spectra.core.models import Extent
from spectra.ml.isolation import enforce_network_isolation
from spectra.ml.models import AnnotationRecord
from spectra.ml.motion import MotionConfig, MotionDetector, MotionSegment
from spectra.services import ServiceError
from spectra.services.evidence import open_evidence
from spectra.services.parse import selected_plugin

VIDEO_KINDS = ("I", "P", "B")

#: Stage 1 sampling. Motion gating exists to remove 90-99% of the compute before the
#: expensive stages, so it deliberately does not look at every frame or every pixel:
#: two samples a second at 320x240 is ample to find where activity is, and cheap enough
#: that running it first is always worth it. The cost is stated in the result and in the
#: report — activity shorter than one sample interval can fall between samples.
MOTION_SAMPLE_FPS = 2.0
MOTION_WIDTH = 320
MOTION_HEIGHT = 240


@dataclass(frozen=True, slots=True)
class AnalyticsSummary:
    recording_id: str
    total_frames: int
    motion_frames: int
    segments: tuple[MotionSegment, ...]
    annotations: tuple[AnnotationRecord, ...]
    #: How the pixels analysed were obtained. Printed in the report so a reader knows the
    #: result came from decoded frames at a stated sample rate, not from the whole stream.
    decode_note: str = ""
    sampled_fps: float = 0.0


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

    if media is None:
        raise ServiceError(
            f"{recording_id}: motion analysis needs FFmpeg to decode the video "
            "(set SPECTRA_FFMPEG or put ffmpeg on PATH). It is not run on undecoded "
            "data: differencing a compressed bitstream measures bitrate, not motion."
        )
    params["ffmpeg"] = media.info().to_json()
    params["sampled_fps"] = MOTION_SAMPLE_FPS
    params["analysis_resolution"] = [MOTION_WIDTH, MOTION_HEIGHT]

    with store.audit.operation("analytics.motion", target=recording_id, params=params):
        work = Path(tempfile.mkdtemp(prefix="spectra-motion-", dir=store.root / "logs"))
        decoded: DecodedGray | None = None
        try:
            es_path, index = _extract_stream(store, plugin, evidence_id, rec, extents, work)
            decoded = media.decode_gray(
                es_path, index, rec["codec"], work, f"{recording_id}-motion",
                width=MOTION_WIDTH, height=MOTION_HEIGHT, fps=MOTION_SAMPLE_FPS,
            )
            frames_data = [
                (position, decoded.pts_ms(position), block, decoded.width, decoded.height)
                for position, block in iter_gray_frames(decoded)
            ]
            if not frames_data:
                raise ServiceError(
                    f"{recording_id}: the stream decoded to no frames, so there is nothing "
                    "to analyse. No motion result is produced."
                )

            # The analysis itself runs with no route to the network (FR-95).
            with enforce_network_isolation():
                frame_results = list(detector.process_frames(frames_data))
                segments = detector.cluster_segments(frame_results)
                annotations = detector.to_annotations(recording_id, frame_results)
        finally:
            # The decoded frames are a working derivative, not evidence and not an
            # artefact. They exist for this run only.
            shutil.rmtree(work, ignore_errors=True)

        # Annotations live beside the evidence, never inside it (FR-97).
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
        except BaseException:
            store.conn.rollback()
            raise

        motion_frame_count = sum(1 for r in frame_results if r.has_motion)
        return AnalyticsSummary(
            recording_id=recording_id,
            total_frames=len(frames_data),
            motion_frames=motion_frame_count,
            segments=tuple(segments),
            annotations=tuple(annotations),
            decode_note=(
                f"Decoded with FFmpeg and sampled at {MOTION_SAMPLE_FPS} frames per second "
                f"at {MOTION_WIDTH}x{MOTION_HEIGHT} greyscale. Activity shorter than one "
                "sample interval can fall between samples and is not detected."
            ),
            sampled_fps=MOTION_SAMPLE_FPS,
        )


def _extract_stream(
    store: CaseStore,
    plugin: Any,
    evidence_id: str,
    rec: dict[str, Any],
    extents: list[Extent],
    work: Path,
) -> tuple[Path, list[FrameIndexEntry]]:
    """Write this recording's elementary stream to the working directory for decoding.

    Reads evidence through `EvidenceSource` like everything else, and writes only into the
    case's own working area. The stream is a derivative used to feed the decoder; it is not
    stored as an artefact, because analytics must not mint evidence copies as a side effect
    of looking at something.
    """
    es_path = work / f"{rec['id']}.es"
    index: list[FrameIndexEntry] = []
    offset = 0
    with open_evidence(store, evidence_id) as src, open(es_path, "wb") as out:
        for extent in extents:
            for frame in plugin.frames(src, extent):
                if frame.kind not in VIDEO_KINDS or frame.channel != rec["channel"]:
                    continue
                size = len(frame.payload)
                out.write(frame.payload)
                index.append(
                    FrameIndexEntry(size=size, pts_ms=frame.pts_ms, key=frame.kind == "I")
                )
                offset += size
    if not index:
        raise ServiceError(f"{rec['id']}: no video frames found in its extents")
    return es_path, index


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
