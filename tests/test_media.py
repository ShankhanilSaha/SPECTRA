"""ES utilities and MediaTool remux (FR-31, FR-32, FR-33, AC-05, TC-PS-05, TC-PS-06)."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from spectra.core import es
from spectra.core.media import (
    TS_PACKET,
    FrameIndexEntry,
    MediaError,
    MediaTool,
    RemuxRefused,
    _crc32_mpeg2,
    locate_ffmpeg,
    write_transport_stream,
)
from tests.media_fixtures import (
    encode_test_stream,
    fake_ffmpeg,
    require_ffmpeg,
    split_access_units,
)


def test_crc32_mpeg2_check_value():
    assert _crc32_mpeg2(b"123456789") == 0x0376E6E7


def test_nal_splitting_is_independent_of_chunk_boundaries():
    stream = (b"\x00\x00\x00\x01\x67\x42\x00" + b"\x00\x00\x01\x68\xce\x00\x00"
              + b"\x00\x00\x00\x01\x65\x88\x84\x00\x00\x03\x01")
    expected = [b"\x67\x42", b"\x68\xce", b"\x65\x88\x84\x00\x00\x03\x01"]
    for size in range(1, 12):
        chunks = [stream[i : i + size] for i in range(0, len(stream), size)]
        assert list(es.iter_nal_units(chunks)) == expected
    summary = es.summarise([stream], "h264")
    assert (summary.vcl_count, summary.non_vcl_count) == (1, 2)


def test_transport_stream_packets_are_well_formed():
    units = [(b"\x00\x00\x00\x01\x65" + bytes([i % 251]) * n, 40 * i, i == 0)
             for i, n in enumerate([1, 170, 171, 175, 176, 177, 182, 183, 184, 5000])]
    out = io.BytesIO()
    assert write_transport_stream(iter(units), "h264", out) == len(units)
    data = out.getvalue()
    assert len(data) % TS_PACKET == 0
    assert all(data[i] == 0x47 for i in range(0, len(data), TS_PACKET))


def _write_es(tmp_path, units, pts_step=40):
    es_path = tmp_path / "clip.es"
    es_path.write_bytes(b"".join(u for u, _ in units))
    index = [FrameIndexEntry(len(u), 1_000_000 + i * pts_step, k) for i, (u, k) in enumerate(units)]
    return es_path, index


@pytest.mark.ffmpeg
@pytest.mark.parametrize("codec", ["h264", "h265"])
def test_remux_is_verified_and_carries_per_frame_pts(tmp_path, codec):
    tool = MediaTool(require_ffmpeg(), tmp_path / "logs")
    units = split_access_units(encode_test_stream(tmp_path, codec), codec)
    assert len(units) == 50 and units[0][1]
    es_path, index = _write_es(tmp_path, units)
    # Irregular frame timing, as a real recorder produces: PTS must follow it exactly.
    index = [FrameIndexEntry(e.size, 1_000_000 + i * 40 + (i // 10) * 13, e.key)
             for i, e in enumerate(index)]
    result = tool.remux(es_path, index, codec, tmp_path / "clip.mp4", tmp_path / "work", "t")
    # Identity is over VCL NAL units; parameter sets may be re-placed (hevc_mp4toannexb
    # repeats VPS/SPS/PPS before each IRAP), so non-VCL counts are reported, not compared.
    assert result.source_vcl.vcl_digest == result.roundtrip_vcl.vcl_digest
    assert result.source_vcl.vcl_count == result.roundtrip_vcl.vcl_count == 50
    pts = tool.probe_pts(result.mp4_path)
    deltas_ms = [round((b - a) * 1000) for a, b in zip(pts, pts[1:], strict=False)]
    assert deltas_ms == [index[i + 1].pts_ms - index[i].pts_ms for i in range(49)]


@pytest.mark.ffmpeg
def test_remux_refuses_backwards_device_time(tmp_path):
    tool = MediaTool(require_ffmpeg(), tmp_path / "logs")
    units = split_access_units(encode_test_stream(tmp_path, "h264", frames=10), "h264")
    es_path, index = _write_es(tmp_path, units)
    index[5] = FrameIndexEntry(index[5].size, index[3].pts_ms, index[5].key)
    with pytest.raises(RemuxRefused, match="not strictly increasing"):
        tool.remux(es_path, index, "h264", tmp_path / "clip.mp4", tmp_path / "work", "t")
    assert not (tmp_path / "clip.mp4").exists()


def test_nal_helpers_reject_unknown_codecs_and_skip_empty_units():
    with pytest.raises(ValueError, match="not an Annex-B codec"):
        es.nal_type(b"\x65", "mjpeg")
    stream = b"\x00\x00\x01\x00\x00\x01\x65\x88"  # an empty NAL unit between two start codes
    summary = es.summarise([stream], "h264")
    assert (summary.vcl_count, summary.non_vcl_count) == (1, 0)
    assert es.nal_type(b"\x26\x01", "h265") == 19  # IDR_W_RADL


# -- refusals that never reach FFmpeg (a stand-in binary is enough) --------------------------

@pytest.fixture
def offline_tool(tmp_path):
    return MediaTool(fake_ffmpeg(tmp_path, "ffmpeg version test"), tmp_path / "logs")


def _es(tmp_path, sizes=(10, 20, 30)):
    path = tmp_path / "clip.es"
    path.write_bytes(b"".join(bytes([i]) * n for i, n in enumerate(sizes)))
    return path, [FrameIndexEntry(n, 40 * i, i == 0) for i, n in enumerate(sizes)]


@pytest.mark.parametrize(
    ("mutate", "exception", "message"),
    [
        (lambda es_path, idx: (es_path, idx, "mjpeg"), RemuxRefused, "no Annex-B remux path"),
        (lambda es_path, idx: (es_path, [], "h264"), RemuxRefused, "do not add up"),
        (lambda es_path, idx: (es_path, idx[:-1], "h264"), RemuxRefused, "do not add up"),
        (lambda es_path, idx: (es_path, [*idx, FrameIndexEntry(5, 999, False)], "h264"),
         RemuxRefused, "ES ended before"),
        (lambda es_path, idx: (es_path, [FrameIndexEntry(10, None, True), *idx[1:]], "h264"),
         RemuxRefused, "no device timing"),
    ],
    ids=["codec", "empty-index", "index-short", "es-short", "no-timing"],
)
def test_remux_refuses_unfaithful_input_and_cleans_up(tmp_path, offline_tool, mutate, exception,
                                                       message):
    es_path, index = _es(tmp_path)
    es_path, index, codec = mutate(es_path, index)
    with pytest.raises(exception, match=message):
        offline_tool.remux(es_path, index, codec, tmp_path / "clip.mp4", tmp_path / "work", "t")
    assert not (tmp_path / "clip.mp4").exists()
    work = tmp_path / "work"
    assert not work.exists() or list(work.iterdir()) == []  # no stray transport stream


def test_remux_refuses_an_empty_recording(tmp_path, offline_tool):
    es_path, _ = _es(tmp_path, sizes=())
    with pytest.raises(RemuxRefused, match="no access units"):
        offline_tool.remux(es_path, [], "h264", tmp_path / "clip.mp4", tmp_path / "work", "t")
    assert list((tmp_path / "work").iterdir()) == []


def test_remux_refuses_to_overwrite(tmp_path, offline_tool):
    es_path, index = _es(tmp_path)
    existing = tmp_path / "clip.mp4"
    existing.write_bytes(b"previous export")
    with pytest.raises(MediaError, match="refusing to overwrite"):
        offline_tool.remux(es_path, index, "h264", existing, tmp_path / "work", "t")
    assert existing.read_bytes() == b"previous export"


def test_tool_info_records_version_hash_and_licence_flags(tmp_path):
    lgpl = MediaTool(fake_ffmpeg(tmp_path, "ffmpeg version 7.1-lgpl\nconfiguration: "
                                 "--disable-programs --enable-shared"), tmp_path)
    info = lgpl.info()
    assert info.version == "ffmpeg version 7.1-lgpl"
    assert not info.gpl_or_nonfree and len(info.sha256) == 64
    assert lgpl.info() is info  # cached: one -version call per tool
    (tmp_path / "gpl").mkdir()
    gpl = MediaTool(fake_ffmpeg(tmp_path / "gpl", "ffmpeg version 7.1\nconfiguration: "
                                "--enable-gpl --enable-libx264"), tmp_path)
    assert gpl.info().gpl_or_nonfree
    assert gpl.info().to_json()["ffmpeg_gpl_or_nonfree_build"] is True


def test_tool_failures_are_media_errors_not_tracebacks(tmp_path):
    with pytest.raises(MediaError, match="not found"):
        MediaTool(tmp_path / "nope.exe", tmp_path)
    broken = MediaTool(fake_ffmpeg(tmp_path, "", exit_code=3, stderr="bad install"), tmp_path)
    with pytest.raises(MediaError, match="-version failed"):
        broken.info()
    not_executable = tmp_path / "ffmpeg.txt"
    not_executable.write_text("just text")
    with pytest.raises(MediaError, match="cannot run FFmpeg"):
        MediaTool(not_executable, tmp_path).info()


def test_locate_ffmpeg_order(tmp_path, monkeypatch):
    binary = fake_ffmpeg(tmp_path, "x")
    monkeypatch.delenv("SPECTRA_FFMPEG", raising=False)
    monkeypatch.setattr("spectra.core.media.shutil.which", lambda name: str(binary))
    assert locate_ffmpeg() == binary  # PATH
    monkeypatch.setenv("SPECTRA_FFMPEG", str(tmp_path / "missing"))
    assert locate_ffmpeg() is None  # an explicit but wrong setting is not silently ignored
    assert locate_ffmpeg(binary) == binary  # explicit argument wins
    monkeypatch.setattr("spectra.core.media.shutil.which", lambda name: None)
    monkeypatch.delenv("SPECTRA_FFMPEG")
    assert locate_ffmpeg() is None


# -- real FFmpeg failure paths ---------------------------------------------------------------

@pytest.mark.ffmpeg
def test_ffmpeg_failure_raises_and_keeps_a_transcript(tmp_path):
    tool = MediaTool(require_ffmpeg(), tmp_path / "logs")
    with pytest.raises(MediaError, match="exit"):
        tool._run(["-i", str(tmp_path / "missing.mp4"), "-f", "null", "-"], "fail.log")
    transcript = (tmp_path / "logs" / "fail.log").read_text("utf-8")
    assert "missing.mp4" in transcript and "exit_code: " in transcript
    with pytest.raises(MediaError):
        tool.probe_pts(tmp_path / "missing.mp4")


class TamperingTool(MediaTool):
    """Flips one byte of the ES re-extracted from the MP4 — as if the container had altered
    the picture data — to prove the verification step actually fails."""

    def _run(self, args, log_name):
        log = super()._run(args, log_name)
        if log_name.endswith("-verify.log"):
            roundtrip = Path(args[args.index("-f") + 2])
            data = bytearray(roundtrip.read_bytes())
            data[len(data) // 2] ^= 0x01
            roundtrip.write_bytes(bytes(data))
        return log


@pytest.mark.ffmpeg
def test_remux_verification_fails_when_picture_data_differs(tmp_path):
    tool = TamperingTool(require_ffmpeg(), tmp_path / "logs")
    units = split_access_units(encode_test_stream(tmp_path, "h264", frames=10), "h264")
    es_path, index = _write_es(tmp_path, units)
    with pytest.raises(MediaError, match="verification failed"):
        tool.remux(es_path, index, "h264", tmp_path / "clip.mp4", tmp_path / "work", "t")


@pytest.mark.ffmpeg
def test_remux_output_is_deterministic(tmp_path):
    tool = MediaTool(require_ffmpeg(), tmp_path / "logs")
    units = split_access_units(encode_test_stream(tmp_path, "h264", frames=20), "h264")
    es_path, index = _write_es(tmp_path, units)
    a = tool.remux(es_path, index, "h264", tmp_path / "a.mp4", tmp_path / "work", "a")
    b = tool.remux(es_path, index, "h264", tmp_path / "b.mp4", tmp_path / "work", "b")
    assert a.mp4_path.read_bytes() == b.mp4_path.read_bytes()
