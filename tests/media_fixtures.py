"""Test-only helpers: real H.264/H.265 bitstreams from FFmpeg, split into access units.

These are codec fixtures, not Tier S corpus images (P6 owns `tools/make_corpus.py`).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from spectra.core.media import locate_ffmpeg


def require_ffmpeg() -> Path:
    path = locate_ffmpeg()
    if path is None:
        pytest.skip("FFmpeg not available (set SPECTRA_FFMPEG or put ffmpeg on PATH)")
    return path


def fake_ffmpeg(tmp: Path, stdout: str, exit_code: int = 0, stderr: str = "") -> Path:
    """A stand-in executable that prints fixed output — for `MediaTool` paths that must not
    depend on a real FFmpeg (version parsing, licence flags, failures)."""
    if os.name == "nt":
        script = tmp / "fake_ffmpeg.cmd"
        lines = ["@echo off"]
        lines += [f"echo {line}" if line else "echo." for line in stdout.splitlines()]
        lines += [f"echo {line} 1>&2" for line in stderr.splitlines()]
        lines.append(f"exit /b {exit_code}")
        script.write_text("\r\n".join(lines) + "\r\n", encoding="ascii")
    else:
        script = tmp / "fake_ffmpeg"
        body = "".join(f"echo '{line}'\n" for line in stdout.splitlines())
        body += "".join(f"echo '{line}' >&2\n" for line in stderr.splitlines())
        script.write_text(f"#!/bin/sh\n{body}exit {exit_code}\n", encoding="ascii")
        script.chmod(0o755)
    return script


#: A motionless scene. Differencing decoded pixels finds nothing here; differencing the
#: compressed bitstream still finds "motion", because coded bytes vary frame to frame
#: regardless of content. That difference is what the motion tests turn on.
STATIC_SOURCE = "color=c=gray:size=320x240:rate=25"

#: The default: an animated test pattern, so there is real motion to find.
MOVING_SOURCE = "testsrc=size=320x240:rate=25"


def encode_test_stream(tmp: Path, codec: str = "h264", frames: int = 50,
                       gop: int = 25, source: str = MOVING_SOURCE) -> bytes:
    ffmpeg = require_ffmpeg()
    out = tmp / f"reference.{codec}"
    gop_params = f"keyint={gop}:min-keyint={gop}:scenecut=0"
    encoder = {
        "h264": ["-c:v", "libx264", "-bf", "0", "-x264-params", gop_params],
        "h265": ["-c:v", "libx265", "-x265-params", f"bframes=0:{gop_params}:log-level=error"],
    }[codec]
    raw_format = {"h264": "h264", "h265": "hevc"}[codec]
    subprocess.run(
        [str(ffmpeg), "-hide_banner", "-nostdin", "-loglevel", "error",
         "-f", "lavfi", "-i", source,
         "-frames:v", str(frames), "-pix_fmt", "yuv420p", *encoder,
         "-f", raw_format, str(out)],
        check=True,
    )
    return out.read_bytes()


def _start_codes(data: bytes) -> list[tuple[int, int]]:
    """(start_code_offset, nal_offset) pairs; a 4-byte code includes its zero_byte."""
    found = []
    i = data.find(b"\x00\x00\x01")
    while i >= 0:
        start = i - 1 if i > 0 and data[i - 1] == 0 else i
        found.append((start, i + 3))
        i = data.find(b"\x00\x00\x01", i + 3)
    return found


def split_access_units(data: bytes, codec: str) -> list[tuple[bytes, bool]]:
    """Split Annex-B into access units, byte-exact (start codes kept). Returns (au, is_key)."""
    codes = _start_codes(data)
    units: list[tuple[bytes, bool]] = []
    au_start = 0
    seen_vcl = False
    key = False
    for index, (sc, nal) in enumerate(codes):
        header = data[nal : nal + 3]
        if codec == "h264":
            kind = header[0] & 0x1F
            vcl = 1 <= kind <= 5
            first_in_pic = vcl and bool(header[1] & 0x80)
            prefix_nal = kind in (6, 7, 8, 9)
            idr = kind == 5
        else:
            kind = (header[0] >> 1) & 0x3F
            vcl = kind <= 31
            first_in_pic = vcl and bool(header[2] & 0x80)
            prefix_nal = kind in (32, 33, 34, 35, 39)
            idr = 16 <= kind <= 21
        if index > 0 and seen_vcl and (prefix_nal or first_in_pic):
            units.append((data[au_start:sc], key))
            au_start, seen_vcl, key = sc, False, False
        seen_vcl = seen_vcl or vcl
        key = key or idr
    units.append((data[au_start:], key))
    return units
