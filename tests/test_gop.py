"""T3 reassembly — runs end at discontinuities, start at keyframes, never invent a time.

FR-42, FR-45, and the doc 3 §7.2 confidence ladder.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import tests.dhavgen as dhavgen
from spectra.core.models import DeviceTime, Extent, Frame
from spectra.core.source import RawImageSource
from spectra.plugins.dahua import DahuaPlugin
from spectra.recover.carver import CarveHit, scan
from spectra.recover.gop import (
    CONF_CHANNEL_AND_TIME,
    CONF_FRAGMENT,
    CONF_NO_TIME,
    CONF_TIME_ONLY,
    group_hits,
    reassemble,
)

KIB = 1024


def raw_source(tmp_path: Path, data: bytes, name: str = "ev.raw") -> RawImageSource:
    path = tmp_path / name
    path.write_bytes(data)
    return RawImageSource(path)


def dhav_stream(frames: int = 6, channel: int = 0, gop: int = 3) -> bytes:
    """`gop` frames per keyframe, as real footage is encoded."""
    units = [
        (b"\x00\x00\x00\x01\x65" + bytes(200), i % gop == 0) for i in range(frames)
    ]
    data, _ = dhavgen.stream(
        units, start=datetime(2026, 3, 5, 14, 32, 10), channel=channel
    )
    return data


def frame(
    offset: int,
    length: int = 100,
    *,
    kind: str = "P",
    channel: int | None = 1,
    when: datetime | None = None,
    sequence: int | None = None,
) -> Frame:
    return Frame(
        kind=kind,  # type: ignore[arg-type]
        channel=channel,
        t_device=(
            DeviceTime(raw=0, encoding="test", local=when) if when is not None else None
        ),
        payload=memoryview(b""),
        codec_hint="h264",
        extent=Extent(offset, length),
        sequence=sequence,
    )


class FakePlugin:
    """A plugin that replays a fixed frame list — lets us drive reassembly precisely."""

    family = "fake"

    def __init__(self, frames: list[Frame]) -> None:
        self._frames = frames

    def frames(self, src: object, extent: Extent) -> list[Frame]:  # noqa: ARG002
        return [f for f in self._frames if extent.offset <= f.extent.offset < extent.end]


# --- grouping hits --------------------------------------------------------------------


def test_adjacent_hits_group_into_one_span() -> None:
    hits = [CarveHit(0, 100, "s"), CarveHit(100, 100, "s"), CarveHit(200, 50, "s")]
    assert list(group_hits(hits)) == [Extent(0, 250)]


def test_a_gap_starts_a_new_span() -> None:
    hits = [CarveHit(0, 100, "s"), CarveHit(5000, 100, "s")]
    assert list(group_hits(hits)) == [Extent(0, 100), Extent(5000, 100)]


def test_max_gap_tolerance_bridges_small_holes() -> None:
    hits = [CarveHit(0, 100, "s"), CarveHit(120, 100, "s")]
    assert list(group_hits(hits, max_gap=32)) == [Extent(0, 220)]


def test_no_hits_yields_no_spans() -> None:
    assert list(group_hits([])) == []


# --- runs end at discontinuities ------------------------------------------------------


def test_a_channel_change_ends_the_run() -> None:
    frames = [
        frame(0, kind="I", channel=1),
        frame(100, channel=1),
        frame(200, kind="I", channel=2),
        frame(300, channel=2),
    ]
    out = list(reassemble(FakePlugin(frames), None, [CarveHit(0, 400, "s")]))  # type: ignore[arg-type]
    assert len(out) == 2
    assert [r.channel for r in out] == [1, 2]
    assert any("channel changed 1 -> 2" in n for n in out[0].notes)


def test_time_running_backwards_ends_the_run() -> None:
    frames = [
        frame(0, kind="I", when=datetime(2026, 3, 5, 14, 0, 0)),
        frame(100, when=datetime(2026, 3, 5, 14, 0, 1)),
        frame(200, kind="I", when=datetime(2026, 3, 5, 12, 0, 0)),
    ]
    out = list(reassemble(FakePlugin(frames), None, [CarveHit(0, 300, "s")]))  # type: ignore[arg-type]
    assert len(out) == 2
    assert any("timestamp ran backwards" in n for n in out[0].notes)


def test_a_byte_gap_ends_the_run() -> None:
    frames = [frame(0, kind="I"), frame(100), frame(9000, kind="I")]
    out = list(reassemble(FakePlugin(frames), None, [CarveHit(0, 9100, "s")]))  # type: ignore[arg-type]
    assert len(out) == 2
    assert any("byte gap of" in n for n in out[0].notes)


def test_a_clean_chain_is_one_run() -> None:
    frames = [frame(i * 100, kind="I" if i == 0 else "P") for i in range(6)]
    out = list(reassemble(FakePlugin(frames), None, [CarveHit(0, 600, "s")]))  # type: ignore[arg-type]
    assert len(out) == 1
    assert out[0].frame_count == 6
    assert any("end of span" in n for n in out[0].notes)


# --- runs start at a keyframe ---------------------------------------------------------


def test_frames_before_the_first_keyframe_are_trimmed() -> None:
    """They reference data that is gone, so they cannot decode and must not be reported."""
    frames = [frame(0), frame(100), frame(200, kind="I"), frame(300)]
    out = list(reassemble(FakePlugin(frames), None, [CarveHit(0, 400, "s")]))  # type: ignore[arg-type]
    assert len(out) == 1
    assert out[0].extents[0].offset == 200
    assert out[0].frame_count == 2
    assert any("200 bytes before the first keyframe were trimmed" in n for n in out[0].notes)


def test_a_run_with_no_keyframe_at_all_is_dropped() -> None:
    frames = [frame(0), frame(100), frame(200)]
    assert list(reassemble(FakePlugin(frames), None, [CarveHit(0, 300, "s")])) == []  # type: ignore[arg-type]


# --- the confidence ladder (doc 3 §7.2) -----------------------------------------------


def test_channel_and_time_and_two_gops_scores_highest() -> None:
    when = datetime(2026, 3, 5, 14, 0, 0)
    frames = [
        frame(0, kind="I", channel=1, when=when),
        frame(100, channel=1, when=when),
        frame(200, kind="I", channel=1, when=when),
    ]
    out = list(reassemble(FakePlugin(frames), None, [CarveHit(0, 300, "s")]))  # type: ignore[arg-type]
    assert out[0].confidence == CONF_CHANNEL_AND_TIME


def test_time_without_channel_scores_lower() -> None:
    when = datetime(2026, 3, 5, 14, 0, 0)
    frames = [
        frame(0, kind="I", channel=None, when=when),
        frame(100, channel=None, when=when),
        frame(200, kind="I", channel=None, when=when),
    ]
    out = list(reassemble(FakePlugin(frames), None, [CarveHit(0, 300, "s")]))  # type: ignore[arg-type]
    assert out[0].confidence == CONF_TIME_ONLY
    assert any("channel unknown" in n for n in out[0].notes)


def test_fr_45_no_time_scores_lowest_and_is_labelled_time_unknown() -> None:
    frames = [frame(0, kind="I"), frame(100), frame(200, kind="I")]
    out = list(reassemble(FakePlugin(frames), None, [CarveHit(0, 300, "s")]))  # type: ignore[arg-type]
    assert out[0].confidence == CONF_NO_TIME
    assert out[0].t_start is None and out[0].t_end is None
    assert any("time unknown" in n for n in out[0].notes)
    assert any("physical position 1" in n for n in out[0].notes)


def test_a_lone_fragment_scores_lowest_of_all() -> None:
    out = list(reassemble(FakePlugin([frame(0, kind="I")]), None, [CarveHit(0, 100, "s")]))  # type: ignore[arg-type]
    assert out[0].confidence == CONF_FRAGMENT


def test_physical_position_increments_across_runs() -> None:
    frames = [
        frame(0, kind="I", channel=1),
        frame(100, kind="I", channel=2),
        frame(200, kind="I", channel=3),
    ]
    out = list(reassemble(FakePlugin(frames), None, [CarveHit(0, 300, "s")]))  # type: ignore[arg-type]
    positions = [
        n for r in out for n in r.notes if "physical position" in n
    ]
    assert len(positions) == 3
    assert "position 1" in positions[0] and "position 3" in positions[2]


# --- end to end against a real container ----------------------------------------------


def test_carve_then_reassemble_on_a_real_dhav_stream(tmp_path: Path) -> None:
    """The whole T3 path: scan finds frames, reassembly turns them into one recording."""
    stream = dhav_stream(frames=6, channel=3)
    src = raw_source(tmp_path, bytes(2 * KIB) + stream)
    plugin = DahuaPlugin()

    hits = list(scan(src, plugin.carve_signatures(), chunk=1 * KIB, overlap=512))
    assert len(hits) == 6

    out = list(reassemble(plugin, src, hits))
    assert len(out) == 1
    rec = out[0]
    assert rec.recovery_tier == "T3"
    assert rec.channel == 3, "DHAV carries channel per frame, so carving recovers it"
    assert rec.t_start is not None, "and a timestamp too"
    assert rec.confidence == CONF_CHANNEL_AND_TIME
    assert any("2 keyframes" in n for n in rec.notes)
    assert rec.extents[0].offset >= 2 * KIB


def test_a_single_gop_run_scores_below_full_and_says_why(tmp_path: Path) -> None:
    """Channel and time recovered, but one keyframe is not a demonstrated chain."""
    src = raw_source(tmp_path, dhav_stream(frames=3, channel=1, gop=99))
    plugin = DahuaPlugin()
    hits = list(scan(src, plugin.carve_signatures(), chunk=1 * KIB, overlap=512))
    rec = next(iter(reassemble(plugin, src, hits)))
    assert rec.channel == 1 and rec.t_start is not None
    assert rec.confidence == CONF_TIME_ONLY
    assert any("only 1 keyframe(s)" in n for n in rec.notes)


def test_two_separate_recordings_on_the_image_stay_separate(tmp_path: Path) -> None:
    a = dhav_stream(frames=4, channel=1)
    b = dhav_stream(frames=4, channel=2)
    src = raw_source(tmp_path, a + bytes(4 * KIB) + b)
    plugin = DahuaPlugin()
    hits = list(scan(src, plugin.carve_signatures(), chunk=1 * KIB, overlap=512))
    out = list(reassemble(plugin, src, hits))
    assert len(out) == 2
    assert sorted(r.channel for r in out if r.channel is not None) == [1, 2]
