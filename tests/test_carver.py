"""T3 signature carving — finds frames with no index, and refuses to invent them.

TC-RC-04 (chunk-boundary straddle), TC-RC-05 (zero false positives on noise), FR-42.
"""

from __future__ import annotations

import random
from datetime import datetime
from pathlib import Path

import pytest

import tests.dhavgen as dhavgen
from spectra.core.source import RawImageSource
from spectra.plugins.dahua import DahuaPlugin
from spectra.recover.carver import CarveBudgetExceeded, CarveHit, scan

KIB = 1024


def raw_source(tmp_path: Path, data: bytes, name: str = "ev.raw") -> RawImageSource:
    path = tmp_path / name
    path.write_bytes(data)
    return RawImageSource(path)


def dhav_stream(frames: int = 6, channel: int = 0) -> bytes:
    units = [(b"\x00\x00\x00\x01\x65" + bytes(200), i == 0) for i in range(frames)]
    data, _ = dhavgen.stream(
        units, start=datetime(2026, 3, 5, 14, 32, 10), channel=channel
    )
    return data


def sigs() -> list:
    return DahuaPlugin().carve_signatures()


# --- it finds what is there -----------------------------------------------------------


def test_finds_every_validated_frame_in_a_clean_stream(tmp_path: Path) -> None:
    stream = dhav_stream(frames=6)
    src = raw_source(tmp_path, stream)
    hits = list(scan(src, sigs(), chunk=1 * KIB, overlap=512))
    assert len(hits) == 6
    assert [h.offset for h in hits] == sorted(h.offset for h in hits)
    assert all(h.signature == "dahua.dhav_frame" for h in hits)
    assert all(h.length > 0 for h in hits)


def test_finds_frames_embedded_in_surrounding_noise(tmp_path: Path) -> None:
    """The realistic case: a freed block still holding video, with junk either side."""
    rng = random.Random(11)
    noise = bytes(rng.randrange(256) for _ in range(3 * KIB))
    stream = dhav_stream(frames=4)
    image = noise + stream + noise
    src = raw_source(tmp_path, image)
    hits = list(scan(src, sigs(), chunk=2 * KIB, overlap=1 * KIB))
    assert len(hits) == 4
    assert all(h.offset >= len(noise) for h in hits)


# --- TC-RC-04: the classic carving bug ------------------------------------------------


def test_tc_rc_04_signature_straddling_a_chunk_boundary_is_still_found(
    tmp_path: Path,
) -> None:
    """Place a frame so `DHAV` spans the last bytes of one window and the first of the next.

    Without overlap this hit is silently lost, and nothing in the output would say so.
    """
    chunk = 4 * KIB
    stream = dhav_stream(frames=1)
    # Two bytes of the magic sit in window 0, the rest in window 1.
    pad = chunk - 2
    image = bytes(pad) + stream + bytes(512)
    src = raw_source(tmp_path, image)

    with_overlap = list(scan(src, sigs(), chunk=chunk, overlap=1 * KIB))
    assert [h.offset for h in with_overlap] == [pad]

    without_overlap = list(scan(src, sigs(), chunk=chunk, overlap=0))
    assert without_overlap == [], "no-overlap scan is expected to miss it — that is the bug"


def test_hits_in_the_rescanned_prefix_are_not_emitted_twice(tmp_path: Path) -> None:
    stream = dhav_stream(frames=8)
    src = raw_source(tmp_path, stream)
    hits = list(scan(src, sigs(), chunk=256, overlap=200))
    offsets = [h.offset for h in hits]
    assert len(offsets) == len(set(offsets)), "overlap must not duplicate hits"


# --- TC-RC-05: it does not invent what is not there -----------------------------------


def test_tc_rc_05_random_data_yields_no_hits(tmp_path: Path) -> None:
    rng = random.Random(20260918)
    noise = bytes(rng.randrange(256) for _ in range(256 * KIB))
    src = raw_source(tmp_path, noise)
    assert list(scan(src, sigs(), chunk=16 * KIB, overlap=1 * KIB)) == []


def test_tc_rc_05_bare_pattern_without_structure_never_validates(tmp_path: Path) -> None:
    """`DHAV` planted every 4096 bytes in noise is not a frame and must not be accepted."""
    rng = random.Random(7)
    buf = bytearray(rng.randrange(256) for _ in range(128 * KIB))
    for offset in range(0, len(buf) - 4, 4096):
        buf[offset : offset + 4] = b"DHAV"
    src = raw_source(tmp_path, bytes(buf))
    assert list(scan(src, sigs(), chunk=32 * KIB, overlap=1 * KIB)) == []


def test_zero_length_and_empty_signature_sets_are_handled(tmp_path: Path) -> None:
    src = raw_source(tmp_path, b"")
    assert list(scan(src, sigs())) == []
    src2 = raw_source(tmp_path, dhav_stream(), name="b.raw")
    assert list(scan(src2, [])) == []


# --- hostile input and bounds ---------------------------------------------------------


def test_rejects_nonsensical_window_parameters(tmp_path: Path) -> None:
    src = raw_source(tmp_path, dhav_stream())
    with pytest.raises(ValueError, match="chunk must be positive"):
        list(scan(src, sigs(), chunk=0))
    with pytest.raises(ValueError, match="overlap must not be negative"):
        list(scan(src, sigs(), overlap=-1))


def test_budget_raises_rather_than_returning_a_short_result(tmp_path: Path) -> None:
    """A truncated carve that looks complete is the disqualifying failure mode (§9.3)."""
    src = raw_source(tmp_path, dhav_stream(frames=40))
    with pytest.raises(CarveBudgetExceeded):
        list(scan(src, sigs(), chunk=64, overlap=32, budget_s=-1.0))


def test_tc_rb_07_fuzzed_images_never_crash_or_emit_invalid_hits(tmp_path: Path) -> None:
    rng = random.Random(4242)
    base = bytearray(dhav_stream(frames=5))
    for trial in range(120):
        buf = bytearray(base)
        for _ in range(rng.randint(1, 12)):
            buf[rng.randrange(len(buf))] = rng.randrange(256)
        src = raw_source(tmp_path, bytes(buf), name=f"f{trial}.raw")
        hits = list(scan(src, sigs(), chunk=512, overlap=256, budget_s=10.0))
        for hit in hits:
            assert isinstance(hit, CarveHit)
            assert 0 <= hit.offset < src.size
            assert hit.length > 0
            assert hit.end <= src.size, "a hit must not claim past the end of the image"


def test_regions_restrict_the_scan_which_is_how_t4_skips_bad_sectors(
    tmp_path: Path,
) -> None:
    stream = dhav_stream(frames=4)
    image = bytes(2 * KIB) + stream
    src = raw_source(tmp_path, image)
    # Restrict to a region that excludes the stream entirely.
    assert list(scan(src, sigs(), chunk=512, overlap=256, regions=[(0, 1024)])) == []
    # And one that includes it.
    full = list(scan(src, sigs(), chunk=512, overlap=256, regions=[(0, src.size)]))
    assert len(full) == 4
