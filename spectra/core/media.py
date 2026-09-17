"""`MediaTool` — the FFmpeg wrapper (doc 3 §3.6, FR-31, FR-33, FR-84).

`remux()` produces the **evidence copy**: stream copy only (`-c copy`), never a re-encode.
Per-frame device timestamps are carried as presentation timestamps (FR-33) by first
packing the verbatim access units into an MPEG transport stream that SPECTRA writes
itself — one PES per access unit, PTS from the frame index — and letting FFmpeg re-container
that to MP4. The payload is never touched by SPECTRA or by an FFmpeg codec. The result is
then **verified**: the ES is extracted back out of the MP4 and its VCL NAL digest must
equal the source's (see `spectra.core.es`), otherwise the export fails.

`derive()` (labelled derivative transcodes, FR-36) arrives in finals.

FFmpeg must eventually be a pinned, bundled LGPL build (NFR-16). Until packaging exists it
is located via `SPECTRA_FFMPEG` or PATH, and its version string, configuration licence
flags and binary SHA-256 are recorded with every invocation so nothing is hidden.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from spectra.core import es
from spectra.core.hashing import hash_case_file

STREAM_TYPES = {"h264": 0x1B, "h265": 0x24}
ANNEXB_BSF = {"h264": ("h264_mp4toannexb", "h264"), "h265": ("hevc_mp4toannexb", "hevc")}
TS_PACKET = 188
VIDEO_PID = 0x100
PMT_PID = 0x1000
PTS_ORIGIN = 90_000  # 1 s head-room so PCR can lead PTS without going negative


class MediaError(Exception):
    """FFmpeg is unavailable or failed."""


class RemuxRefused(MediaError):
    """The input cannot be remuxed faithfully; the ES export still stands."""


@dataclass(frozen=True, slots=True)
class FrameIndexEntry:
    """One access unit in an exported ES: its size, PTS and where it came from."""

    size: int
    pts_ms: int | None
    key: bool

    @staticmethod
    def from_json(line: str) -> FrameIndexEntry:
        data = json.loads(line)
        pts = data["pts_ms"]
        return FrameIndexEntry(int(data["size"]), None if pts is None else int(pts),
                               bool(data["key"]))


@dataclass(frozen=True, slots=True)
class ToolInfo:
    path: str
    version: str
    sha256: str
    gpl_or_nonfree: bool

    def to_json(self) -> dict[str, object]:
        return {
            "ffmpeg_path": self.path,
            "ffmpeg_version": self.version,
            "ffmpeg_sha256": self.sha256,
            "ffmpeg_gpl_or_nonfree_build": self.gpl_or_nonfree,
        }


@dataclass(frozen=True, slots=True)
class RemuxResult:
    mp4_path: Path
    logs: tuple[Path, ...]
    source_vcl: es.NalSummary
    roundtrip_vcl: es.NalSummary
    tool: ToolInfo


@dataclass(frozen=True, slots=True)
class DecodedGray:
    """A file of raw 8-bit greyscale frames, all the same size (doc 3 §9).

    Analytics decode to frames for analysis only and never write beside the evidence. This
    lands in the case's own working directory, is deleted by the caller, and is a
    derivative in the strict sense: it must never be exported or presented as the evidence
    copy.
    """

    path: Path
    width: int
    height: int
    fps: float
    frame_bytes: int
    count: int
    log: Path

    def pts_ms(self, index: int) -> int:
        """Presentation time of a sampled frame, from the sampling rate the caller set."""
        return int(round(index * 1000.0 / self.fps))


class DecodeRefused(MediaError):
    """The stream cannot be decoded, so no analysis may claim to have looked at it."""


def iter_gray_frames(decoded: DecodedGray) -> Iterator[tuple[int, bytes]]:
    """Yield (index, frame bytes) one frame at a time.

    Streamed rather than loaded: a long recording at 2 fps is still hundreds of megabytes
    of raw frames, and the memory ceiling is 4 GB regardless of input size (NFR-04, D4).
    """
    with open(decoded.path, "rb") as handle:
        for position in range(decoded.count):
            block = handle.read(decoded.frame_bytes)
            if len(block) != decoded.frame_bytes:
                break
            yield position, block


def locate_ffmpeg(explicit: str | Path | None = None) -> Path | None:
    for candidate in (explicit, os.environ.get("SPECTRA_FFMPEG")):
        if candidate:
            path = Path(candidate)
            return path if path.is_file() else None
    found = shutil.which("ffmpeg")
    return Path(found) if found else None


class MediaTool:
    def __init__(self, ffmpeg: Path, log_dir: Path) -> None:
        if not ffmpeg.is_file():
            raise MediaError(f"FFmpeg binary not found: {ffmpeg}")
        self.ffmpeg = ffmpeg
        self.log_dir = log_dir
        self._info: ToolInfo | None = None

    def info(self) -> ToolInfo:
        if self._info is None:
            proc = self._exec([str(self.ffmpeg), "-hide_banner", "-version"])
            if proc.returncode != 0:
                raise MediaError(f"FFmpeg -version failed: {proc.stderr.strip()}")
            lines = proc.stdout.splitlines()
            config = next((ln for ln in lines if ln.startswith("configuration:")), "")
            self._info = ToolInfo(
                path=str(self.ffmpeg),
                version=lines[0] if lines else "unknown",
                sha256=hash_case_file(self.ffmpeg).sha256,
                gpl_or_nonfree=("--enable-gpl" in config or "--enable-nonfree" in config),
            )
        return self._info

    @staticmethod
    def _exec(command: list[str]) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(command, capture_output=True, text=True, check=False)
        except OSError as exc:  # not executable, wrong architecture, removed mid-case …
            raise MediaError(f"cannot run FFmpeg {command[0]}: {exc}") from exc

    def _run(self, args: list[str], log_name: str) -> Path:
        command = [str(self.ffmpeg), "-hide_banner", "-nostdin", *args]
        proc = self._exec(command)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        log = self.log_dir / log_name
        log.write_text(
            "command: " + json.dumps(command) + "\n"
            f"exit_code: {proc.returncode}\n--- stdout ---\n{proc.stdout}\n"
            f"--- stderr ---\n{proc.stderr}",
            encoding="utf-8",
        )
        if proc.returncode != 0:
            raise MediaError(f"FFmpeg failed (exit {proc.returncode}); transcript at {log}")
        return log

    def remux(
        self,
        es_path: Path,
        index: Iterable[FrameIndexEntry],
        codec: str,
        out_mp4: Path,
        work_dir: Path,
        log_prefix: str,
    ) -> RemuxResult:
        """Evidence-copy remux of `es_path` to `out_mp4` with per-frame PTS, verified.
        `index` is consumed once, streaming, so memory does not grow with duration (D4)."""
        if codec not in STREAM_TYPES:
            raise RemuxRefused(f"codec {codec!r} has no Annex-B remux path; ES export only")
        if out_mp4.exists():
            raise MediaError(f"refusing to overwrite {out_mp4}")

        work_dir.mkdir(parents=True, exist_ok=True)
        ts_path = work_dir / f"{log_prefix}.ts"
        try:
            with open(es_path, "rb") as es_in, open(ts_path, "wb") as ts_out:
                units = write_transport_stream(_read_units(es_in, index), codec, ts_out)
                if es_in.read(1):
                    raise RemuxRefused("frame index sizes do not add up to the ES length")
            if units == 0:
                raise RemuxRefused("no access units to remux")
        except BaseException:
            ts_path.unlink(missing_ok=True)
            raise

        logs = [
            self._run(
                [
                    "-f", "mpegts", "-i", str(ts_path),
                    "-map", "0:v:0", "-c", "copy",
                    "-map_metadata", "-1", "-fflags", "+bitexact", "-flags:v", "+bitexact",
                    "-video_track_timescale", "90000",
                    "-f", "mp4", str(out_mp4),
                ],
                f"{log_prefix}-remux.log",
            )
        ]
        bsf, raw_format = ANNEXB_BSF[codec]
        roundtrip_es = work_dir / f"{log_prefix}-roundtrip.es"
        logs.append(
            self._run(
                [
                    "-i", str(out_mp4), "-map", "0:v:0", "-c", "copy",
                    "-bsf:v", bsf, "-f", raw_format, str(roundtrip_es),
                ],
                f"{log_prefix}-verify.log",
            )
        )
        source_vcl = es.summarise_file(es_path, codec)
        roundtrip_vcl = es.summarise_file(roundtrip_es, codec)
        roundtrip_es.unlink()
        ts_path.unlink()
        if source_vcl.vcl_digest != roundtrip_vcl.vcl_digest:
            raise MediaError(
                "remux verification failed: VCL NAL units in the MP4 differ from the source "
                f"ES ({source_vcl.vcl_count} vs {roundtrip_vcl.vcl_count} units)"
            )
        return RemuxResult(out_mp4, tuple(logs), source_vcl, roundtrip_vcl, self.info())

    def probe_pts(self, mp4: Path) -> list[float]:
        """Presentation times (seconds) of the video packets, via FFmpeg (for tests)."""
        proc = self._exec(
            [str(self.ffmpeg), "-hide_banner", "-nostdin", "-i", str(mp4), "-map", "0:v:0",
             "-c", "copy", "-f", "framecrc", "-"]
        )
        if proc.returncode != 0:
            raise MediaError(proc.stderr)
        times: list[float] = []
        timebase = None
        for line in proc.stdout.splitlines():
            if line.startswith("#tb 0:"):
                num, den = line.split(":", 1)[1].strip().split("/")
                timebase = int(num) / int(den)
            elif line and not line.startswith("#") and timebase is not None:
                fields = [f.strip() for f in line.split(",")]
                times.append(int(fields[2]) * timebase)
        return times


    def decode_gray(
        self,
        es_path: Path,
        index: Iterable[FrameIndexEntry],
        codec: str,
        work_dir: Path,
        log_prefix: str,
        *,
        width: int = 320,
        height: int = 240,
        fps: float = 2.0,
    ) -> DecodedGray:
        """Decode video to raw greyscale frames for analysis (FR-90, doc 3 §9).

        Motion gating needs *pixels*. Differencing the coded bitstream instead measures
        entropy-coded byte churn, which tracks bitrate — and because an encoder spends more
        bits on a moving scene, such a result looks plausible while being a proxy for the
        wrong quantity. Nothing downstream can recover from that, so the decode is not
        optional: this refuses rather than approximating.

        Frames are sampled at `fps` and scaled to `width` x `height`. Motion gating needs
        neither full rate nor full resolution, and that reduction is what makes Stage 1
        cheap enough to run before the expensive stages.
        """
        if codec not in STREAM_TYPES:
            raise DecodeRefused(
                f"codec {codec!r} has no Annex-B path, so it cannot be decoded for analysis"
            )
        work_dir.mkdir(parents=True, exist_ok=True)
        ts_path = work_dir / f"{log_prefix}-decode.ts"
        raw_path = work_dir / f"{log_prefix}-gray.raw"
        try:
            with open(es_path, "rb") as es_in, open(ts_path, "wb") as ts_out:
                units = write_transport_stream(_read_units(es_in, index), codec, ts_out)
            if units == 0:
                raise DecodeRefused("no access units to decode")
            log = self._run(
                [
                    "-f", "mpegts", "-i", str(ts_path),
                    "-map", "0:v:0",
                    "-vf", f"fps={fps},scale={width}:{height}",
                    "-pix_fmt", "gray", "-f", "rawvideo", str(raw_path),
                ],
                f"{log_prefix}-decode.log",
            )
        except BaseException:
            raw_path.unlink(missing_ok=True)
            raise
        finally:
            ts_path.unlink(missing_ok=True)

        frame_bytes = width * height
        size = raw_path.stat().st_size
        if size == 0 or size % frame_bytes != 0:
            raw_path.unlink(missing_ok=True)
            raise DecodeRefused(
                f"the decoder produced {size} bytes, which is not a whole number of "
                f"{width}x{height} greyscale frames; the stream did not decode"
            )
        return DecodedGray(raw_path, width, height, fps, frame_bytes,
                           size // frame_bytes, log)


def _read_units(
    handle: BinaryIO, index: Iterable[FrameIndexEntry]
) -> Iterator[tuple[bytes, int, bool]]:
    previous: int | None = None
    for position, entry in enumerate(index):
        if entry.pts_ms is None:
            raise RemuxRefused(f"access unit {position} has no device timing; ES export only")
        if previous is not None and entry.pts_ms <= previous:
            raise RemuxRefused(
                f"device timestamps are not strictly increasing at access unit {position} "
                f"({previous} ms → {entry.pts_ms} ms); a single evidence MP4 would "
                "misrepresent them"
            )
        previous = entry.pts_ms
        data = handle.read(entry.size)
        if len(data) != entry.size:
            raise RemuxRefused("ES ended before the frame index did")
        yield data, entry.pts_ms, entry.key


# -- minimal MPEG-TS writer (ISO/IEC 13818-1) -----------------------------------------------

def _crc32_mpeg2(data: bytes) -> int:
    crc = 0xFFFFFFFF
    for byte in data:
        crc ^= byte << 24
        for _ in range(8):
            crc = ((crc << 1) ^ 0x04C11DB7) if crc & 0x80000000 else (crc << 1)
            crc &= 0xFFFFFFFF
    return crc


def _section_packet(pid: int, section: bytes) -> bytes:
    body = section + _crc32_mpeg2(section).to_bytes(4, "big")
    header = bytes([0x47, 0x40 | (pid >> 8), pid & 0xFF, 0x10])
    packet = header + b"\x00" + body
    return packet + b"\xff" * (TS_PACKET - len(packet))


def _pat() -> bytes:
    section = bytes([0x00, 0xB0, 13, 0x00, 0x01, 0xC1, 0x00, 0x00,
                     0x00, 0x01, 0xE0 | (PMT_PID >> 8), PMT_PID & 0xFF])
    return _section_packet(0, section)


def _pmt(codec: str) -> bytes:
    section = bytes([
        0x02, 0xB0, 18, 0x00, 0x01, 0xC1, 0x00, 0x00,
        0xE0 | (VIDEO_PID >> 8), VIDEO_PID & 0xFF, 0xF0, 0x00,
        STREAM_TYPES[codec], 0xE0 | (VIDEO_PID >> 8), VIDEO_PID & 0xFF, 0xF0, 0x00,
    ])
    return _section_packet(PMT_PID, section)


def _pts_field(pts: int) -> bytes:
    return bytes([
        0x21 | ((pts >> 29) & 0x0E),
        (pts >> 22) & 0xFF,
        ((pts >> 14) & 0xFE) | 0x01,
        (pts >> 7) & 0xFF,
        ((pts << 1) & 0xFE) | 0x01,
    ])


def _pcr_field(base: int) -> bytes:
    return bytes([
        (base >> 25) & 0xFF, (base >> 17) & 0xFF, (base >> 9) & 0xFF, (base >> 1) & 0xFF,
        ((base << 7) & 0x80) | 0x7E, 0x00,
    ])


def write_transport_stream(units: Iterator[tuple[bytes, int, bool]], codec: str, out) -> int:
    """Write one PES per access unit, PTS = PTS_ORIGIN + (pts_ms - first) × 90."""
    out.write(_pat())
    out.write(_pmt(codec))
    counter = 0
    first_pts_ms: int | None = None
    count = 0
    for data, pts_ms, key in units:
        if first_pts_ms is None:
            first_pts_ms = pts_ms
        pts = (PTS_ORIGIN + (pts_ms - first_pts_ms) * 90) & ((1 << 33) - 1)
        # PES: no packet length (allowed for video in TS), data_alignment, PTS only.
        pes = b"\x00\x00\x01\xe0\x00\x00\x84\x80\x05" + _pts_field(pts) + bytes(data)
        position = 0
        while position < len(pes):
            first = position == 0
            # Adaptation field *content* (after its length byte).
            content = b""
            if first:
                flags = 0x10 | (0x40 if key else 0x00)  # PCR present, random access indicator
                content = bytes([flags]) + _pcr_field(pts - PTS_ORIGIN // 2)
            has_field = first
            room = TS_PACKET - 4 - (1 + len(content) if has_field else 0)
            remaining = len(pes) - position
            if remaining < room:  # last packet of the PES: stuff to exactly 188 bytes
                shortfall = room - remaining
                if has_field:
                    content += b"\xff" * shortfall
                else:
                    has_field = True
                    # shortfall 1 → a lone length byte of 0; otherwise flags byte + stuffing
                    content = b"" if shortfall == 1 else b"\x00" + b"\xff" * (shortfall - 2)
                room = remaining
            header = bytes([
                0x47,
                (0x40 if first else 0x00) | (VIDEO_PID >> 8),
                VIDEO_PID & 0xFF,
                (0x30 if has_field else 0x10) | counter,
            ])
            field = bytes([len(content)]) + content if has_field else b""
            packet = header + field + pes[position : position + room]
            if len(packet) != TS_PACKET:
                raise AssertionError(f"TS packetisation error: {len(packet)} bytes")
            out.write(packet)
            position += room
            counter = (counter + 1) & 0x0F
        count += 1
    return count
