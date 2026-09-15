"""Test-only DHAV frame builder, written from the [R] field layout in doc 4 §3.2.

SCOPE WARNING: this is a *spec-conformance* fixture written by the parser author. It proves
the parser implements the documented layout; it cannot prove the layout is what real
recorders write. That needs P6's independently written Tier S corpus and Tier E `.dav`
files from real units (doc 6 §3; CLAUDE.md §15.1 "Corpus independence").
"""

from __future__ import annotations

import struct
from datetime import datetime, timedelta

I_FRAME, P_FRAME, AUDIO, AUX = 0xFD, 0xFC, 0xF0, 0xF1
CODEC_H264, CODEC_H265 = 0x4, 0xC


def pack_date(moment: datetime) -> int:
    return (
        ((moment.year - 2000) << 26) | (moment.month << 22) | (moment.day << 17)
        | (moment.hour << 12) | (moment.minute << 6) | moment.second
    )


def video_ext(
    codec_id: int = CODEC_H264, fps: int = 25, width: int = 704, height: int = 576
) -> bytes:
    return bytes([0x80, 0, width // 8, height // 8, 0x81, 0, codec_id, fps])


def frame(
    payload: bytes,
    *,
    frame_type: int = I_FRAME,
    channel: int = 0,
    seq: int = 0,
    date: int = 0,
    tick: int = 0,
    ext: bytes = b"",
    length_override: int | None = None,
    trailer_length_override: int | None = None,
) -> bytes:
    length = 24 + len(ext) + len(payload) + 8
    header = struct.pack(
        "<4sBBBBIIIHBB", b"DHAV", frame_type, 0, channel, 0, seq,
        length if length_override is None else length_override, date, tick, len(ext), 0,
    )
    trailer_len = length if trailer_length_override is None else trailer_length_override
    return header + ext + payload + b"dhav" + struct.pack("<I", trailer_len)


def stream(
    units: list[tuple[bytes, bool]],
    *,
    start: datetime = datetime(2026, 3, 5, 14, 32, 10),
    channel: int = 0,
    frame_ms: int = 40,
    first_tick: int = 64_000,
    codec_id: int = CODEC_H264,
    jitter_every: int = 0,
) -> tuple[bytes, list[int]]:
    """A DHAV frame stream for access units; returns (bytes, expected relative PTS in ms).
    The tick starts near the 16-bit wrap so every multi-second test exercises unwrapping."""
    out = bytearray()
    elapsed = 0
    pts = []
    for i, (unit, key) in enumerate(units):
        if i:
            elapsed += frame_ms + (7 if jitter_every and i % jitter_every == 0 else 0)
        moment = start + timedelta(milliseconds=elapsed)
        out += frame(
            unit,
            frame_type=I_FRAME if key else P_FRAME,
            channel=channel,
            seq=i,
            date=pack_date(moment),
            tick=(first_tick + elapsed) % 0x10000,
            ext=video_ext(codec_id) if key else b"",
        )
        pts.append(elapsed)
    return bytes(out), pts
