"""Dahua plugin: DHAV framing, frames(), carving validator, probe, enumerate (FR-21, FR-30,
FR-42, TC-PS-11, TC-RB-04, TC-RB-05, TC-RB-07). Fixtures are spec-conformance only — see
`tests/dhavgen.py`."""

from __future__ import annotations

import dataclasses
import os
import random
import time
from datetime import datetime

import pytest

from spectra.core.models import Extent
from spectra.core.source import FileSetSource, RawImageSource
from spectra.identify.engine import build_plan
from spectra.plugins.base import LayoutNotSupported
from spectra.plugins.dahua import (
    LAYOUT_DAV_EXPORT,
    MAX_FRAME_LEN,
    DahuaPlugin,
    iter_frames,
    validate_frame,
)
from tests import dhavgen

UNITS = [(b"\x00\x00\x00\x01\x65" + bytes([i]) * (50 + i), i % 10 == 0) for i in range(30)]


def raw_source(tmp_path, data: bytes, name="disk.img") -> RawImageSource:
    path = tmp_path / name
    path.write_bytes(data)
    return RawImageSource(path)


def export_dir(tmp_path, files: dict[str, bytes]):
    root = tmp_path / "usb"
    root.mkdir()
    for name, data in files.items():
        (root / name).write_bytes(data)
    return root


# -- framing ----------------------------------------------------------------------------------

def test_valid_frame_is_accepted_and_fields_decoded(tmp_path):
    data = dhavgen.frame(b"PAYLOAD", channel=3, seq=77, date=0x68CAE80A, tick=1234,
                         ext=dhavgen.video_ext(dhavgen.CODEC_H265, 20, 1920, 1080))
    src = raw_source(tmp_path, data)
    header = validate_frame(src, 0)
    assert header is not None
    assert (header.channel, header.sequence, header.tick, header.length) == (3, 77, 1234, len(data))
    assert (header.width, header.height, header.codec_id, header.fps) == (1920, 1080, 0xC, 20)
    assert header.kind == "I"


def test_trailer_mismatch_and_bad_lengths_are_rejected(tmp_path):
    good = dhavgen.frame(b"x" * 100)
    cases = {
        "trailer length differs": dhavgen.frame(b"x" * 100, trailer_length_override=999),
        "4 GB length (TC-RB-05)": dhavgen.frame(b"x" * 100, length_override=0xFFFFFFF0),
        "just over the bound": dhavgen.frame(b"x" * 100, length_override=MAX_FRAME_LEN + 1),
        "below minimum": dhavgen.frame(b"x" * 100, length_override=10),
        "points past end": dhavgen.frame(b"x" * 100, length_override=len(good) + 4),
        "no end magic": good[:-8] + b"DHAV" + good[-4:],
    }
    for name, data in cases.items():
        assert validate_frame(raw_source(tmp_path, data, f"{abs(hash(name))}.img"), 0) is None, name


def test_frames_yield_views_and_resync_over_garbage(tmp_path):
    body, _ = dhavgen.stream(UNITS[:5])
    aux = dhavgen.frame(b"\xAA" * 12, frame_type=dhavgen.AUX)
    data = b"\x13" * 99 + body[: len(body) // 2] + os.urandom(333) + aux + body
    src = raw_source(tmp_path, data)
    frames = list(DahuaPlugin().frames(src, Extent(0, src.size)))
    video = [f for f in frames if f.kind in ("I", "P")]
    assert [bytes(f.payload) for f in video[-5:]] == [u for u, _ in UNITS[:5]]
    assert all(isinstance(f.payload, memoryview) and f.payload.readonly for f in frames)
    assert any(f.kind == "unknown" for f in frames)  # aux frame kept in the chain
    assert frames[0].extent.offset == 99


def test_pts_unwraps_the_16_bit_tick_and_counts_wraps_across_long_pauses(tmp_path):
    body, expected = dhavgen.stream(UNITS, first_tick=65_400, jitter_every=4)
    # A 100 s pause (1.5 wraps of the tick) — only the 1 s date can say how many wraps.
    late = datetime(2026, 3, 5, 14, 33, 51)  # 101 s after the stream start + ~1.2 s of frames
    elapsed_ms = int((late - datetime(2026, 3, 5, 14, 32, 10)).total_seconds() * 1000)
    tail = dhavgen.frame(b"\x00\x00\x01\x65late", frame_type=dhavgen.P_FRAME, seq=99,
                         date=dhavgen.pack_date(late), tick=(65_400 + elapsed_ms) % 0x10000)
    src = raw_source(tmp_path, body + tail)
    pts = [f.pts_ms for f in DahuaPlugin().frames(src, Extent(0, src.size))]
    relative = [p - pts[0] for p in pts]
    assert relative[:-1] == expected
    assert relative[-1] == elapsed_ms


def test_carve_signature_validator(tmp_path):
    body, _ = dhavgen.stream(UNITS[:3])
    src = raw_source(tmp_path, bytes(1000) + body)
    (signature,) = DahuaPlugin().carve_signatures()
    assert signature.pattern == b"DHAV"
    first_len = validate_frame(src, 1000).length
    assert signature.validate(src, 1000) == first_len
    assert signature.validate(src, 1001) is None
    assert signature.validate(src, 0) is None


def test_carve_validator_rejects_frame_spanning_two_export_files(tmp_path):
    body = dhavgen.frame(b"y" * 500)
    root = export_dir(tmp_path, {"a.dav": body[:200], "b.dav": body[200:]})
    src = FileSetSource(root)
    (signature,) = DahuaPlugin().carve_signatures()
    assert signature.validate(src, 0) is None


# -- probe --------------------------------------------------------------------------------------

def test_probe_export_file_set_is_parseable(tmp_path):
    a, _ = dhavgen.stream(UNITS[:10], channel=0)
    b, _ = dhavgen.stream(UNITS[:10], channel=1)
    src = FileSetSource(export_dir(tmp_path, {"ch1.dav": a, "ch2.dav": b"DAHUA\x00\x00" + b}))
    result = DahuaPlugin.probe(src, build_plan(src.size))
    assert result.layout_version == LAYOUT_DAV_EXPORT
    assert result.parse_supported and result.confidence == 0.95
    assert result.matches[0].data[:4] == b"DHAV" and result.matches[1].data[:4] == b"dhav"


def test_probe_export_with_foreign_files_says_so(tmp_path):
    a, _ = dhavgen.stream(UNITS[:10])
    src = FileSetSource(export_dir(tmp_path, {"clip.dav": a, "player.exe": b"MZ" + bytes(500)}))
    result = DahuaPlugin.probe(src, build_plan(src.size))
    assert result.confidence == 0.9
    assert "player.exe" in result.note


def test_probe_raw_disk_with_dhav_frames_is_carve_only(tmp_path):
    body, _ = dhavgen.stream(UNITS)
    size = 8 * 1024 * 1024
    image = bytearray(size)
    for offset in (1_500_000, 4_000_000, 7_000_000):
        image[offset : offset + len(body)] = body
    src = raw_source(tmp_path, bytes(image))
    result = DahuaPlugin.probe(src, build_plan(src.size, sweep_windows=8))
    assert result is not None
    assert result.layout_version is None and not result.parse_supported
    assert 0.5 <= result.confidence <= 0.8
    assert "carve-only" in result.note


def test_probe_rejects_random_and_zero_images(tmp_path):
    rng = random.Random(7)
    for name, data in {"zero": bytes(2 << 20), "rand": rng.randbytes(2 << 20),
                       "magic-only": (b"DHAV" + rng.randbytes(60)) * 30000}.items():
        src = raw_source(tmp_path, data, f"{name}.img")
        assert DahuaPlugin.probe(src, build_plan(src.size)) is None, name


# -- enumerate (Tier E path) -------------------------------------------------------------------

def test_enumerate_export_files_per_channel_with_times_and_notes(tmp_path):
    a, _ = dhavgen.stream(UNITS[:20], channel=2)
    interleaved = b"".join(
        dhavgen.frame(u, frame_type=dhavgen.I_FRAME if k else dhavgen.P_FRAME, channel=i % 2,
                      seq=i, date=dhavgen.pack_date(datetime(2026, 3, 5, 9, 0, i)),
                      ext=dhavgen.video_ext() if k else b"")
        for i, (u, k) in enumerate(UNITS[:10])
    )
    root = export_dir(tmp_path, {"a.dav": a, "b.dav": interleaved + b"\x00" * 50})
    src = FileSetSource(root)
    plugin = DahuaPlugin()
    layout = plugin.superblock(src)
    recordings = list(plugin.enumerate(src, layout, include_orphans=False))
    assert [(r.channel, r.frame_count) for r in recordings] == [(2, 20), (0, 5), (1, 5)]
    first = recordings[0]
    assert first.t_start.local == datetime(2026, 3, 5, 14, 32, 10)
    assert first.t_end.local == datetime(2026, 3, 5, 14, 32, 10)  # 20 frames × 40 ms
    assert (first.codec, first.resolution, first.fps) == ("h264", (704, 576), 25.0)
    assert first.recovery_tier == "T1" and first.stream == "unknown"
    assert first.extents == (Extent(0, len(a)),)
    assert len(recordings[1].extents) == 5  # interleaved channel: one extent per frame
    assert any("50 byte(s)" in n for n in recordings[2].notes)


def test_superblock_refuses_raw_disk_without_verified_layout(tmp_path):
    body, _ = dhavgen.stream(UNITS)
    src = raw_source(tmp_path, body)
    with pytest.raises(LayoutNotSupported, match="no verified Dahua on-disk superblock"):
        DahuaPlugin().superblock(src)


def test_u16_resolution_record_and_unknown_extension_types(tmp_path):
    ext_82 = bytes([0x82, 0, 0, 0]) + (2560).to_bytes(2, "little") + (1440).to_bytes(2, "little")
    header = validate_frame(raw_source(tmp_path, dhavgen.frame(b"x", ext=ext_82), "a.img"), 0)
    assert (header.width, header.height) == (2560, 1440)

    unknown = bytes([0x77, 1, 2, 3]) + dhavgen.video_ext()  # records after an unknown one
    header = validate_frame(raw_source(tmp_path, dhavgen.frame(b"x", ext=unknown), "b.img"), 0)
    assert header.unknown_ext_types == (0x77,)
    assert header.width is None  # interpretation stops; nothing past it is guessed
    assert header.payload_length == 1  # but ext_length still bounds the payload

    truncated = bytes([0x81, 0])  # a known record cut short by ext_length
    header = validate_frame(raw_source(tmp_path, dhavgen.frame(b"x", ext=truncated), "c.img"), 0)
    assert header.unknown_ext_types == (0x81,) and header.codec_id is None


def test_short_or_out_of_bounds_reads_are_not_frames(tmp_path):
    body = dhavgen.frame(b"x" * 10)
    src = raw_source(tmp_path, body)
    assert validate_frame(src, len(body) - 10) is None
    assert validate_frame(src, len(body) + 100) is None
    (signature,) = DahuaPlugin().carve_signatures()
    assert signature.validate(src, src.size) is None


def test_audio_frames_keep_their_own_clock_and_are_not_video(tmp_path):
    start = datetime(2026, 3, 5, 14, 32, 10)
    parts = []
    for i, (unit, key) in enumerate(UNITS[:6]):
        parts.append(dhavgen.frame(unit, frame_type=dhavgen.I_FRAME if key else dhavgen.P_FRAME,
                                   seq=i, date=dhavgen.pack_date(start), tick=1000 + 40 * i,
                                   ext=dhavgen.video_ext() if key else b""))
        parts.append(dhavgen.frame(b"\x11" * 32, frame_type=dhavgen.AUDIO, seq=i,
                                   date=dhavgen.pack_date(start), tick=50_000 + 20 * i))
    src = raw_source(tmp_path, b"".join(parts))
    frames = list(DahuaPlugin().frames(src, Extent(0, src.size)))
    video = [f for f in frames if f.kind in ("I", "P")]
    audio = [f for f in frames if f.kind == "audio"]
    assert [f.pts_ms - video[0].pts_ms for f in video] == [0, 40, 80, 120, 160, 200]
    assert [f.pts_ms - audio[0].pts_ms for f in audio] == [0, 20, 40, 60, 80, 100]
    assert all(f.codec_hint is None for f in audio) and video[1].codec_hint == "h264"

    root = export_dir(tmp_path, {"av.dav": b"".join(parts)})
    fs = FileSetSource(root)
    plugin = DahuaPlugin()
    (rec,) = plugin.enumerate(fs, plugin.superblock(fs), include_orphans=False)
    assert rec.frame_count == 6
    assert rec.extents == (Extent(0, fs.size),)  # audio frames lie inside the extent


def test_enumerate_notes_header_bytes_time_unknown_and_codec_problems(tmp_path):
    undated = b"".join(dhavgen.frame(u, frame_type=dhavgen.I_FRAME if k else dhavgen.P_FRAME,
                                     seq=i, date=0, ext=dhavgen.video_ext() if k else b"")
                       for i, (u, k) in enumerate(UNITS[:4]))
    mpeg4 = dhavgen.frame(b"\x00\x00\x01\xb6", date=0x68CAE80A,
                          ext=dhavgen.video_ext(codec_id=0x1))
    p_only = dhavgen.frame(b"x", frame_type=dhavgen.P_FRAME, date=0x68CAE80A)
    root = export_dir(tmp_path, {"a.dav": b"DAHUA\x00\x00" + undated, "b.dav": mpeg4,
                                 "c.dav": p_only, "d.dav": b""})
    src = FileSetSource(root)
    plugin = DahuaPlugin()
    a, b, c = plugin.enumerate(src, plugin.superblock(src), include_orphans=False)
    assert any("7 byte(s) before the first frame" in n for n in a.notes)
    assert a.t_start is None and a.t_end is None
    assert any("time unknown" in n for n in a.notes)
    assert b.codec == "unknown" and any("indicates mpeg4" in n for n in b.notes)
    assert c.codec == "unknown" and any("codec not stated" in n for n in c.notes)


def test_export_probe_states_when_the_file_check_was_capped(tmp_path, monkeypatch):
    import spectra.plugins.dahua as dahua

    monkeypatch.setattr(dahua, "EXPORT_MEMBER_CHECK_LIMIT", 2)
    clip, _ = dhavgen.stream(UNITS[:3])
    src = FileSetSource(export_dir(tmp_path, {f"{i}.dav": clip for i in range(3)}))
    result = DahuaPlugin.probe(src, build_plan(src.size))
    assert result.confidence == 0.9
    assert "only the first 2 files were checked" in result.note


def test_layout_refusals(tmp_path):
    plugin = DahuaPlugin()
    src = FileSetSource(export_dir(tmp_path, {"readme.txt": b"no frames here"}))
    with pytest.raises(LayoutNotSupported, match="no export file begins"):
        plugin.superblock(src)
    clip, _ = dhavgen.stream(UNITS[:3])
    (tmp_path / "usb2").mkdir()
    (tmp_path / "usb2" / "a.dav").write_bytes(clip)
    good = FileSetSource(tmp_path / "usb2")
    layout = plugin.superblock(good)
    foreign = dataclasses.replace(layout, layout_version="dhfs_v9")
    with pytest.raises(LayoutNotSupported, match="dhfs_v9"):
        list(plugin.enumerate(good, foreign, include_orphans=False))


def test_frame_walk_without_resync_stops_at_the_first_invalid_byte(tmp_path):
    clip, _ = dhavgen.stream(UNITS[:4])
    first = int.from_bytes(clip[12:16], "little")
    data = clip[:first] + b"\xee" * 40 + clip[first:]
    src = raw_source(tmp_path, data)
    assert len(list(iter_frames(src, Extent(0, src.size), resync=False))) == 1
    assert len(list(iter_frames(src, Extent(0, src.size), resync=True))) == 4


# -- robustness ---------------------------------------------------------------------------------

def test_fuzzed_streams_never_crash_hang_or_emit_invalid_frames(tmp_path):
    """TC-RB-04/07 (reduced size for CI): mutate a valid stream; every frame the parser
    yields must still pass full validation, and every recording must stay inside the file."""
    rng = random.Random(0xD4A7)
    base, _ = dhavgen.stream(UNITS)
    plugin = DahuaPlugin()
    deadline = time.monotonic() + 60
    for trial in range(400):
        data = bytearray(base)
        for _ in range(rng.randint(1, 12)):
            op = rng.random()
            pos = rng.randrange(len(data))
            if op < 0.6:
                data[pos] = rng.randrange(256)
            elif op < 0.8:
                data[pos : pos + 4] = rng.randbytes(4)
            elif op < 0.9:
                del data[pos : pos + rng.randint(1, 64)]
            else:
                data[pos:pos] = rng.choice([b"DHAV", b"dhav", rng.randbytes(16)])
        root = tmp_path / f"f{trial}"
        root.mkdir()
        (root / "x.dav").write_bytes(bytes(data))
        with FileSetSource(root) as src:
            for frame in plugin.frames(src, Extent(0, src.size)):
                assert validate_frame(src, frame.extent.offset) is not None
                assert frame.extent.end <= src.size
            result = plugin.probe(src, build_plan(src.size))
            if result is not None and result.parse_supported:
                try:
                    layout = plugin.superblock(src)
                except LayoutNotSupported:
                    continue
                for rec in plugin.enumerate(src, layout, include_orphans=False):
                    assert all(e.end <= src.size for e in rec.extents)
                    assert rec.frame_count and rec.frame_count > 0
        assert time.monotonic() < deadline, "fuzz loop too slow — possible hang"
