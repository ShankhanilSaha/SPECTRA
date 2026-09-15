"""Stage 1 Motion & Activity Gating Engine (doc 3 §9, doc 2 §5.8, FR-90).

Performs frame differencing with sensitivity control, minimum area gating, and ROI masking.
Acts as Stage 1 filter: removes 80–99 % of inactive footage before heavier ML runs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from spectra.ml.models import BUILTIN_MOTION_SPEC, AnnotationRecord

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator


@dataclass(frozen=True, slots=True)
class MotionConfig:
    """Configuration parameters for motion activity detection."""

    sensitivity: int = 25  # pixel delta threshold (1..255)
    min_area_pixels: int = 400  # min changed pixels to count as motion
    roi: tuple[int, int, int, int] | None = None  # (x, y, width, height)
    min_event_frames: int = 2  # min consecutive frames to declare an event
    max_gap_frames: int = 5  # gap between motion bursts to merge into one segment


@dataclass(frozen=True, slots=True)
class MotionFrameResult:
    """Per-frame motion evaluation result."""

    frame_no: int
    pts_ms: int
    has_motion: bool
    changed_pixels: int
    score: float
    bbox: list[int] | None  # [x, y, w, h]


@dataclass(frozen=True, slots=True)
class MotionSegment:
    """A contiguous period of motion activity in a recording."""

    start_frame: int
    end_frame: int
    start_pts_ms: int
    end_pts_ms: int
    peak_score: float
    motion_frame_count: int

    @property
    def duration_ms(self) -> int:
        return max(0, self.end_pts_ms - self.start_pts_ms)


def diff_frames_pure(
    prev_gray: bytes,
    curr_gray: bytes,
    width: int,
    height: int,
    config: MotionConfig,
) -> tuple[int, list[int] | None]:
    """Pure-Python frame differencing fallback when OpenCV is unavailable.

    Returns:
        (changed_pixel_count, bounding_box_as_[x, y, w, h] or None)
    """
    total_pixels = width * height
    if len(prev_gray) != total_pixels or len(curr_gray) != total_pixels:
        raise ValueError(
            f"Buffer size {len(curr_gray)} does not match frame geometry {width}x{height}."
        )

    x0, y0, w_roi, h_roi = (
        config.roi if config.roi is not None else (0, 0, width, height)
    )
    x1 = min(width, x0 + w_roi)
    y1 = min(height, y0 + h_roi)

    thresh = config.sensitivity
    changed_count = 0
    min_x, min_y = width, height
    max_x, max_y = -1, -1

    for y in range(y0, y1):
        row_offset = y * width
        for x in range(x0, x1):
            idx = row_offset + x
            diff = abs(curr_gray[idx] - prev_gray[idx])
            if diff >= thresh:
                changed_count += 1
                if x < min_x:
                    min_x = x
                if x > max_x:
                    max_x = x
                if y < min_y:
                    min_y = y
                if y > max_y:
                    max_y = y

    if changed_count < config.min_area_pixels or max_x < min_x:
        return changed_count, None

    bbox = [min_x, min_y, max_x - min_x + 1, max_y - min_y + 1]
    return changed_count, bbox


def evaluate_frame_diff(
    prev_gray: bytes,
    curr_gray: bytes,
    width: int,
    height: int,
    config: MotionConfig,
) -> tuple[int, list[int] | None]:
    """Evaluate frame difference using OpenCV if available, otherwise pure Python."""
    try:
        import cv2
        import numpy as np

        prev_arr = np.frombuffer(prev_gray, dtype=np.uint8).reshape((height, width))
        curr_arr = np.frombuffer(curr_gray, dtype=np.uint8).reshape((height, width))

        if config.roi is not None:
            rx, ry, rw, rh = config.roi
            prev_arr = prev_arr[ry : ry + rh, rx : rx + rw]
            curr_arr = curr_arr[ry : ry + rh, rx : rx + rw]
        else:
            rx, ry = 0, 0

        diff = cv2.absdiff(prev_arr, curr_arr)
        _, thresh = cv2.threshold(diff, config.sensitivity, 255, cv2.THRESH_BINARY)
        contours, _ = cv2.findContours(
            thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )

        changed_count = int(cv2.countNonZero(thresh))
        if changed_count < config.min_area_pixels or not contours:
            return changed_count, None

        # Compute union bounding box
        boxes = [cv2.boundingRect(c) for c in contours if cv2.contourArea(c) > 50]
        if not boxes:
            return changed_count, None

        bx0 = min(b[0] for b in boxes) + rx
        by0 = min(b[1] for b in boxes) + ry
        bx1 = max(b[0] + b[2] for b in boxes) + rx
        by1 = max(b[1] + b[3] for b in boxes) + ry
        return changed_count, [bx0, by0, bx1 - bx0, by1 - by0]

    except ImportError:
        return diff_frames_pure(prev_gray, curr_gray, width, height, config)


class MotionDetector:
    """Gating detector that processes sequences of video frames into motion segments."""

    def __init__(self, config: MotionConfig | None = None) -> None:
        self.config = config if config is not None else MotionConfig()

    def process_frames(
        self,
        frames: Iterable[tuple[int, int, bytes, int, int]],
    ) -> Iterator[MotionFrameResult]:
        """Evaluate motion frame by frame.

        Input frames: iterable of (frame_no, pts_ms, gray_bytes, width, height).
        """
        prev_gray: bytes | None = None

        for frame_no, pts_ms, gray_bytes, width, height in frames:
            if prev_gray is None:
                prev_gray = gray_bytes
                yield MotionFrameResult(
                    frame_no=frame_no,
                    pts_ms=pts_ms,
                    has_motion=False,
                    changed_pixels=0,
                    score=0.0,
                    bbox=None,
                )
                continue

            changed, bbox = evaluate_frame_diff(
                prev_gray, gray_bytes, width, height, self.config
            )
            has_motion = bbox is not None
            total_area = width * height
            score = (
                min(1.0, changed / (total_area * 0.15)) if has_motion else 0.0
            )

            yield MotionFrameResult(
                frame_no=frame_no,
                pts_ms=pts_ms,
                has_motion=has_motion,
                changed_pixels=changed,
                score=round(score, 4),
                bbox=bbox,
            )
            prev_gray = gray_bytes

    def cluster_segments(
        self,
        frame_results: Iterable[MotionFrameResult],
    ) -> list[MotionSegment]:
        """Group per-frame detections into contiguous motion activity segments."""
        segments: list[MotionSegment] = []
        in_segment = False
        seg_start_frame = 0
        seg_start_pts = 0
        seg_end_frame = 0
        seg_end_pts = 0
        peak_score = 0.0
        motion_count = 0
        gap_count = 0

        for r in frame_results:
            if r.has_motion:
                if not in_segment:
                    in_segment = True
                    seg_start_frame = r.frame_no
                    seg_start_pts = r.pts_ms
                    peak_score = r.score
                    motion_count = 1
                    gap_count = 0
                else:
                    motion_count += 1
                    gap_count = 0
                    if r.score > peak_score:
                        peak_score = r.score
                seg_end_frame = r.frame_no
                seg_end_pts = r.pts_ms
            elif in_segment:
                gap_count += 1
                if gap_count > self.config.max_gap_frames:
                    if motion_count >= self.config.min_event_frames:
                        segments.append(
                            MotionSegment(
                                start_frame=seg_start_frame,
                                end_frame=seg_end_frame,
                                start_pts_ms=seg_start_pts,
                                end_pts_ms=seg_end_pts,
                                peak_score=peak_score,
                                motion_frame_count=motion_count,
                            )
                        )
                    in_segment = False
                    peak_score = 0.0
                    motion_count = 0

        if in_segment and motion_count >= self.config.min_event_frames:
            segments.append(
                MotionSegment(
                    start_frame=seg_start_frame,
                    end_frame=seg_end_frame,
                    start_pts_ms=seg_start_pts,
                    end_pts_ms=seg_end_pts,
                    peak_score=peak_score,
                    motion_frame_count=motion_count,
                )
            )

        return segments

    def to_annotations(
        self,
        recording_id: str,
        frame_results: Iterable[MotionFrameResult],
    ) -> list[AnnotationRecord]:
        """Convert positive motion frame detections into AnnotationRecords."""
        records: list[AnnotationRecord] = []
        for r in frame_results:
            if r.has_motion and r.bbox is not None:
                records.append(
                    AnnotationRecord.create_motion(
                        recording_id=recording_id,
                        frame_no=r.frame_no,
                        bbox=r.bbox,
                        score=r.score,
                        spec=BUILTIN_MOTION_SPEC,
                        extra_note=f"changed_pixels={r.changed_pixels}",
                    )
                )
        return records
