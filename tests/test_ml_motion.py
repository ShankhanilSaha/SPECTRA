"""Tests for Stage 1 Motion & Activity Gating Engine (spectra/ml/motion.py).

Traces to FR-90, FR-96, and FR-97.
"""

from __future__ import annotations

import importlib.util
import random

import pytest

from spectra.ml.motion import (
    MotionConfig,
    MotionDetector,
    diff_frames_opencv,
    diff_frames_pure,
)

HAVE_OPENCV = all(importlib.util.find_spec(m) is not None for m in ("cv2", "numpy"))
OPENCV_MISSING = "OpenCV not installed (pip install -e '.[ml]'); only the reference ran"
IMPLEMENTATIONS = [
    pytest.param(diff_frames_pure, id="pure"),
    pytest.param(diff_frames_opencv, id="opencv",
                 marks=pytest.mark.skipif(not HAVE_OPENCV, reason=OPENCV_MISSING)),
]


def make_frame(width: int, height: int, fill: int = 0) -> bytes:
    return bytes([fill] * (width * height))


def make_frame_with_rect(
    width: int, height: int, rx: int, ry: int, rw: int, rh: int, val: int = 255, base: int = 0
) -> bytes:
    buf = bytearray([base] * (width * height))
    for y in range(ry, min(height, ry + rh)):
        for x in range(rx, min(width, rx + rw)):
            buf[y * width + x] = val
    return bytes(buf)


def test_static_frames_yield_zero_motion():
    width, height = 100, 100
    detector = MotionDetector(MotionConfig(sensitivity=25, min_area_pixels=100))
    frames = [
        (i, i * 40, make_frame(width, height, 50), width, height)
        for i in range(10)
    ]
    results = list(detector.process_frames(frames))
    assert len(results) == 10
    assert not any(r.has_motion for r in results)

    segments = detector.cluster_segments(results)
    assert len(segments) == 0


def test_moving_object_triggers_motion_and_bounding_box():
    width, height = 100, 100
    config = MotionConfig(sensitivity=25, min_area_pixels=200, min_event_frames=2)
    detector = MotionDetector(config)

    frames = []
    # 3 static frames
    for i in range(3):
        frames.append((i, i * 40, make_frame(width, height, 0), width, height))
    # 4 frames with moving 20x20 block (400 pixels > min_area_pixels)
    for i in range(3, 7):
        offset_x = (i - 3) * 5
        frame_data = make_frame_with_rect(width, height, 20 + offset_x, 20, 20, 20, 200)
        frames.append((i, i * 40, frame_data, width, height))
    # 3 static frames again
    for i in range(7, 10):
        frames.append((i, i * 40, make_frame(width, height, 0), width, height))

    results = list(detector.process_frames(frames))
    motion_frames = [r for r in results if r.has_motion]
    assert len(motion_frames) >= 3

    # Check bounding box on motion
    for r in motion_frames:
        assert r.bbox is not None
        bx, by, bw, bh = r.bbox
        assert bw >= 20 and bh >= 20

    segments = detector.cluster_segments(results)
    assert len(segments) == 1
    seg = segments[0]
    assert seg.start_frame == 3
    assert seg.end_frame >= 6
    assert seg.motion_frame_count >= 3
    assert seg.duration_ms > 0


def test_motion_sensitivity_threshold():
    """Each frame is compared with the one before it, not with the first frame."""
    width, height = 50, 50
    config = MotionConfig(sensitivity=50, min_area_pixels=100)
    detector = MotionDetector(config)

    base = make_frame(width, height, 100)
    subtle = make_frame_with_rect(width, height, 10, 10, 20, 20, val=130, base=100)
    strong = make_frame_with_rect(width, height, 10, 10, 20, 20, val=180, base=100)

    frames = [
        (0, 0, base, width, height),
        (1, 40, subtle, width, height),  # 130 against 100: delta 30 < 50
        (2, 80, base, width, height),  # 100 against 130: delta 30 < 50
        (3, 120, strong, width, height),  # 180 against 100: delta 80 > 50
    ]
    results = list(detector.process_frames(frames))
    assert [r.has_motion for r in results] == [False, False, False, True]


@pytest.mark.parametrize("implementation", IMPLEMENTATIONS)
@pytest.mark.parametrize(("delta", "expected"), [(49, False), (50, True), (51, True)])
def test_a_change_equal_to_the_sensitivity_counts(implementation, delta, expected):
    """The rule is inclusive. OpenCV's THRESH_BINARY is exclusive, and until the rule was
    pinned the two implementations disagreed on exactly this pixel (NFR-08)."""
    width, height = 50, 50
    config = MotionConfig(sensitivity=50, min_area_pixels=100)
    prev = make_frame(width, height, 100)
    curr = make_frame_with_rect(width, height, 10, 10, 20, 20, val=100 + delta, base=100)
    changed, bbox = implementation(prev, curr, width, height, config)
    assert (bbox is not None) is expected
    assert changed == (400 if expected else 0)


@pytest.mark.skipif(not HAVE_OPENCV, reason=OPENCV_MISSING)
def test_opencv_and_pure_python_give_identical_results():
    """Same frames, same numbers, whichever implementation is installed (NFR-08, AC-11).

    Covers what used to diverge: deltas exactly at the threshold, scattered single-pixel
    changes (OpenCV used to drop small contours from the box and could then report no
    motion at all), and ROIs that are empty or run off the frame.
    """
    rng = random.Random(90)
    width, height = 37, 23
    rois = [None, (5, 3, 20, 10), (30, 18, 50, 50), (-4, -2, 12, 9), (10, 10, 0, 5)]
    for case in range(300):
        prev = bytes(rng.randrange(256) for _ in range(width * height))
        curr = bytearray(prev)
        sensitivity = rng.choice([1, 25, 50, 200, 255])
        for _ in range(rng.choice([0, 1, 5, 60, 400])):
            i = rng.randrange(width * height)
            step = rng.choice([sensitivity - 1, sensitivity, sensitivity + 1, 255])
            curr[i] = max(0, min(255, prev[i] + rng.choice([-1, 1]) * step))
        config = MotionConfig(
            sensitivity=sensitivity,
            min_area_pixels=rng.choice([0, 1, 3, 50]),
            roi=rng.choice(rois),
        )
        expected = diff_frames_pure(prev, bytes(curr), width, height, config)
        got = diff_frames_opencv(prev, bytes(curr), width, height, config)
        assert got == expected, f"case {case}: {config}"


def test_roi_masking_ignores_motion_outside_region():
    width, height = 100, 100
    # ROI restricted to left half: x=0..50
    config = MotionConfig(sensitivity=25, min_area_pixels=200, roi=(0, 0, 50, 100))
    detector = MotionDetector(config)

    base = make_frame(width, height, 0)
    # Motion on right half: x=60..80 (outside ROI)
    outside = make_frame_with_rect(width, height, 60, 20, 20, 20, 255)
    # Motion on left half: x=10..30 (inside ROI)
    inside = make_frame_with_rect(width, height, 10, 20, 20, 20, 255)

    frames = [
        (0, 0, base, width, height),
        (1, 40, outside, width, height),
        (2, 80, inside, width, height),
    ]
    results = list(detector.process_frames(frames))
    assert not results[1].has_motion
    assert results[2].has_motion


def test_to_annotations_stamps_statutory_disclaimer():
    width, height = 50, 50
    detector = MotionDetector(MotionConfig(sensitivity=25, min_area_pixels=50))
    frames = [
        (0, 0, make_frame(width, height, 0), width, height),
        (1, 40, make_frame_with_rect(width, height, 10, 10, 15, 15, 200), width, height),
    ]
    results = list(detector.process_frames(frames))
    annotations = detector.to_annotations("rec-xyz", results)

    assert len(annotations) == 1
    ann = annotations[0]
    assert ann.recording_id == "rec-xyz"
    assert ann.frame_no == 1
    assert ann.source == "motion"
    assert ann.label == "motion"
    assert ann.bbox is not None
    # FR-96 non-negotiable statutory disclaimer verification
    assert "Machine-generated detection. Confidence" in ann.note
    assert "Requires human verification against the source frame." in ann.note
    assert "Not an identification." in ann.note
