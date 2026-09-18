"""T4 — bad-sector tolerant carving (FR-43, TC-RC-06, doc 6 §3.1 S-06).

The tier's whole point: one long clip with an invisible hole in the middle is worse than
several short ones that each cover bytes we actually read.
"""

from __future__ import annotations

import contextlib
from datetime import datetime
from pathlib import Path

from spectra.core.models import Extent
from spectra.core.source import RawImageSource, SourceUnavailable
from spectra.plugins.dahua import DahuaPlugin
from spectra.recover.carver import readable_regions, scan
from spectra.recover.gop import Span, _subtract, reassemble
from tests import dhavgen

KIB = 1024
SECTOR = 512


class HolePunchedSource(RawImageSource):
    """A source whose sectors in `bad` raise I/O errors, as a failing disk does.

    `_raw_read` is the hook `core/source.py` retries around, so the zero-fill and the
    gap record both happen exactly as they would on real hardware.
    """

    bad: tuple[int, ...] = ()

    def _raw_read(self, offset: int, length: int) -> bytes:
        for start in self.bad:
            if offset < start + SECTOR and start < offset + length:
                raise OSError(5, "I/O error")
        return super()._raw_read(offset, length)


def holed(tmp_path: Path, data: bytes, bad: tuple[int, ...], name: str = "bad.raw"):
    path = tmp_path / name
    path.write_bytes(data)
    src = HolePunchedSource(path)
    src.bad = bad
    return src


def dhav_stream(frames: int = 6, channel: int = 0, gop: int = 3) -> bytes:
    units = [
        (b"\x00\x00\x00\x01\x65" + bytes(200), i % gop == 0) for i in range(frames)
    ]
    data, _ = dhavgen.stream(
        units, start=datetime(2026, 3, 5, 14, 32, 10), channel=channel
    )
    return data


# --- the span arithmetic ---------------------------------------------------------------


def test_a_hole_cuts_a_span_into_two_survivable_pieces() -> None:
    out = list(_subtract([Extent(0, 1000)], [Extent(400, 100)]))
    assert [(s.extent.offset, s.extent.length) for s in out] == [(0, 400), (500, 500)]
    assert all(s.cut_by == (Extent(400, 100),) for s in out)


def test_a_span_with_no_holes_survives_whole() -> None:
    out = list(_subtract([Extent(0, 1000)], []))
    assert out == [Span(Extent(0, 1000))]
    assert out[0].cut_by == ()


def test_several_holes_produce_several_pieces() -> None:
    out = list(_subtract([Extent(0, 1000)], [Extent(200, 50), Extent(600, 50)]))
    assert [(s.extent.offset, s.extent.length) for s in out] == [
        (0, 200),
        (250, 350),
        (650, 350),
    ]


def test_a_hole_covering_the_whole_span_leaves_nothing() -> None:
    assert list(_subtract([Extent(100, 100)], [Extent(0, 1000)])) == []


def test_holes_outside_the_span_are_ignored() -> None:
    out = list(_subtract([Extent(500, 100)], [Extent(0, 100), Extent(900, 100)]))
    assert [(s.extent.offset, s.extent.length) for s in out] == [(500, 100)]


def test_a_hole_at_the_head_shifts_the_start() -> None:
    out = list(_subtract([Extent(0, 1000)], [Extent(0, 100)]))
    assert [(s.extent.offset, s.extent.length) for s in out] == [(100, 900)]


# --- against a real failing source ------------------------------------------------------


def test_bad_sectors_become_gaps_and_readable_ranges_exclude_them(
    tmp_path: Path,
) -> None:
    src = holed(tmp_path, bytes(8 * KIB), bad=(2048, 4096))
    src.read(0, 8 * KIB)  # touching the sectors is what records them
    assert src.gaps() == (Extent(2048, SECTOR), Extent(4096, SECTOR))
    regions = readable_regions(src)
    assert (2048, SECTOR) not in regions
    assert sum(length for _, length in regions) == 8 * KIB - 2 * SECTOR


def test_tc_rc_06_each_survivable_span_is_its_own_clip(tmp_path: Path) -> None:
    """Two recordings either side of a bad-sector run must not merge into one."""
    stream = dhav_stream(frames=4, channel=1)
    # Lay a stream, a bad sector, then another stream.
    head = bytes(SECTOR) + stream
    pad = bytes(SECTOR * 2 - (len(head) % SECTOR))
    image = head + pad + stream
    bad_at = ((len(head) + len(pad)) // SECTOR) * SECTOR - SECTOR

    src = holed(tmp_path, image, bad=(bad_at,))
    src.read(0, src.size)
    assert src.gaps(), "the fixture must actually produce a hole"

    plugin = DahuaPlugin()
    hits = list(scan(src, plugin.carve_signatures(), chunk=1 * KIB, overlap=512,
                     regions=readable_regions(src)))
    out = list(reassemble(plugin, src, hits, unreadable=src.gaps()))

    assert out, "there is recoverable footage either side of the hole"
    assert all(r.recovery_tier == "T4" for r in out)
    for rec in out:
        for extent in rec.extents:
            for gap in src.gaps():
                assert not (extent.offset < gap.end and gap.offset < extent.end), (
                    "a T4 clip must never span unreadable bytes"
                )


def test_a_clip_bounded_by_a_hole_says_so(tmp_path: Path) -> None:
    """The common case: the carve stops at a hole, and the clip records why."""
    stream = dhav_stream(frames=6, channel=2)
    image = bytes(SECTOR) + stream + bytes(4 * KIB)
    bad_at = SECTOR * 3  # inside the stream, so a clip really does abut it
    src = holed(tmp_path, image, bad=(bad_at,))
    src.read(0, src.size)

    plugin = DahuaPlugin()
    hits = list(scan(src, plugin.carve_signatures(), chunk=1 * KIB, overlap=512,
                     regions=readable_regions(src)))
    out = list(reassemble(plugin, src, hits, unreadable=src.gaps()))
    assert out, "footage survives either side of the hole"
    bounded = [r for r in out if any("unreadable sector run" in n for n in r.notes)]
    assert bounded, "at least one clip abuts the hole and must say so"
    assert any("FR-43" in n for r in bounded for n in r.notes)


def test_without_unreadable_the_tier_is_t3_not_t4(tmp_path: Path) -> None:
    src = holed(tmp_path, bytes(SECTOR) + dhav_stream(frames=4), bad=())
    plugin = DahuaPlugin()
    hits = list(scan(src, plugin.carve_signatures(), chunk=1 * KIB, overlap=512))
    out = list(reassemble(plugin, src, hits))
    assert out and all(r.recovery_tier == "T3" for r in out)
    assert not any("unreadable sector run" in n for r in out for n in r.notes)


def test_a_fully_unreadable_image_recovers_nothing_and_does_not_crash(
    tmp_path: Path,
) -> None:
    stream = dhav_stream(frames=4)
    src = holed(tmp_path, stream, bad=tuple(range(0, len(stream), SECTOR)))
    with contextlib.suppress(SourceUnavailable):
        src.read(0, src.size)  # a wholly dead source refuses outright, which is correct
    plugin = DahuaPlugin()
    out = list(reassemble(plugin, src, [], unreadable=src.gaps()))
    assert out == []
