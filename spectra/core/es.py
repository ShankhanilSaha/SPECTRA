"""Codec-level elementary-stream utilities (Annex-B H.264/H.265). Vendor-free.

Defines what "bit-identical" means for an evidence MP4 (FR-31, AC-05; CLAUDE.md §17
item 5). FFmpeg's MP4 muxer rewrites Annex-B start codes as length prefixes and moves
parameter sets into `avcC`/`hvcC`, so a whole-file hash of the MP4 payload can never equal
the ES hash. Identity is therefore asserted over the **ordered sequence of VCL NAL units**
(the coded picture data), each compared byte for byte: `vcl_digest` of the extracted ES
must equal `vcl_digest` of the ES recovered from the MP4 with `*_mp4toannexb`.
Parameter-set placement and access-unit delimiters are container plumbing and are
reported separately, not silently ignored.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

VCL_CODECS = ("h264", "h265")


def iter_nal_units(chunks: Iterable[bytes | memoryview]) -> Iterator[bytes]:
    """Split an Annex-B byte stream into NAL units (start codes and trailing zero bytes
    removed). Streams: memory is bounded by the largest single NAL unit."""
    buffer = bytearray()
    started = False
    for chunk in chunks:
        buffer += chunk
        search_from = 0
        while True:
            index = buffer.find(b"\x00\x00\x01", search_from)
            if index < 0:
                break
            if started:
                yield _strip_trailing_zeros(buffer[:index])
            started = True
            del buffer[: index + 3]
            search_from = 0
        # keep a possible partial start code at the tail for the next chunk
        if not started:
            del buffer[: max(0, len(buffer) - 2)]
    if started and buffer:
        nal = _strip_trailing_zeros(buffer)
        if nal:
            yield nal


def _strip_trailing_zeros(data: bytearray) -> bytes:
    end = len(data)
    while end and data[end - 1] == 0:
        end -= 1
    return bytes(data[:end])


def nal_type(nal: bytes, codec: str) -> int:
    if codec == "h264":
        return nal[0] & 0x1F
    if codec == "h265":
        return (nal[0] >> 1) & 0x3F
    raise ValueError(f"not an Annex-B codec: {codec}")


def is_vcl(nal: bytes, codec: str) -> bool:
    kind = nal_type(nal, codec)
    return 1 <= kind <= 5 if codec == "h264" else kind <= 31


@dataclass(frozen=True, slots=True)
class NalSummary:
    vcl_digest: str
    vcl_count: int
    non_vcl_count: int


def summarise(chunks: Iterable[bytes | memoryview], codec: str) -> NalSummary:
    """VCL digest = SHA-256 over each VCL NAL unit, each prefixed with its 4-byte length."""
    digest = hashlib.sha256()
    vcl = other = 0
    for nal in iter_nal_units(chunks):
        if not nal:
            continue
        if is_vcl(nal, codec):
            digest.update(len(nal).to_bytes(4, "big"))
            digest.update(nal)
            vcl += 1
        else:
            other += 1
    return NalSummary(digest.hexdigest(), vcl, other)


def summarise_file(path: Path, codec: str, chunk: int = 1 << 20) -> NalSummary:
    def read() -> Iterator[bytes]:
        with open(path, "rb") as handle:
            while block := handle.read(chunk):
                yield block

    return summarise(read(), codec)
