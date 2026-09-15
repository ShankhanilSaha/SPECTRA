"""Tests for Stage 1 Motion & Activity Gating Engine (spectra/ml/motion.py).

Traces to FR-90, FR-96, and FR-97.
"""

from __future__ import annotations

from spectra.ml.motion import MotionConfig, MotionDetector


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
    width, height = 50, 50
    # Sensitivity threshold = 50
    config = MotionConfig(sensitivity=50, min_area_pixels=100)
    detector = MotionDetector(config)

    prev = make_frame(width, height, 100)
    # Delta of 30 < 50: should not trigger
    subtle = make_frame_with_rect(width, height, 10, 10, 20, 20, val=130, base=100)
    # Delta of 80 > 50: should trigger
    strong = make_frame_with_rect(width, height, 10, 10, 20, 20, val=180, base=100)

    frames = [
        (0, 0, prev, width, height),
        (1, 40, subtle, width, height),
        (2, 80, strong, width, height),
    ]
    results = list(detector.process_frames(frames))
    assert not results[1].has_motion
    assert results[2].has_motion


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
