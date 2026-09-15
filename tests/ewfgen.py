"""Test-only minimal EWF-E01 writer (single segment), so E01 ingest can be tested without
`ewfacquire` — the PyPI pyewf wheel is read-only in practice (built without zlib compression).

Layout per the libewf "Expert Witness Compression Format" specification: file header, then
`header` (zlib text), `volume`, `sectors`, `table`, `table2`, `hash`, `done` sections, each
behind a 76-byte descriptor with an Adler-32 checksum. All integers little-endian.
Not product code: SPECTRA does not write evidence images (acquisition is `acquire/`, finals).
"""

from __future__ import annotations

import hashlib
import struct
import zlib
from pathlib import Path

SIGNATURE = b"EVF\x09\x0d\x0a\xff\x00"
DESCRIPTOR = 76
BYTES_PER_SECTOR = 512
SECTORS_PER_CHUNK = 64
CHUNK = BYTES_PER_SECTOR * SECTORS_PER_CHUNK


def _adler(data: bytes) -> bytes:
    return struct.pack("<I", zlib.adler32(data) & 0xFFFFFFFF)


def _descriptor(kind: bytes, offset: int, size: int, next_offset: int | None = None) -> bytes:
    body = kind.ljust(16, b"\x00") + struct.pack(
        "<QQ", offset + size if next_offset is None else next_offset, size
    ) + bytes(40)
    return body + _adler(body)


def write_e01(path: Path, media: bytes, *, compress: bool = True, stored_md5: str | None = "auto",
              case_number: str = "CASE-T-1") -> Path:
    if len(media) % BYTES_PER_SECTOR:
        raise ValueError("media size must be a whole number of sectors")
    out = bytearray(SIGNATURE + b"\x01" + struct.pack("<H", 1) + b"\x00\x00")

    def section(kind: bytes, data: bytes, next_offset: int | None = None) -> int:
        offset = len(out)
        size = DESCRIPTOR + len(data)
        out.extend(_descriptor(kind, offset, size, next_offset) + data)
        return offset

    header_text = (
        "1\nmain\nc\tn\ta\te\tt\tav\tov\tm\tu\tp\tr\n"
        f"{case_number}\tEV-1\ttest image\texaminer\tsynthetic\t6.0\tWindows\t"
        "2026 9 15 10 0 0\t2026 9 15 10 0 0\t0\tf\n\n"
    )
    section(b"header", zlib.compress(header_text.encode("ascii")))

    chunks = [media[i : i + CHUNK] for i in range(0, len(media), CHUNK)]
    volume = bytearray(1048)
    volume[0] = 0x01  # fixed disk
    struct.pack_into("<III Q", volume, 4, len(chunks), SECTORS_PER_CHUNK, BYTES_PER_SECTOR,
                     len(media) // BYTES_PER_SECTOR)
    volume[0x24] = 0x01  # media flags: image
    volume[0x34] = 0x01 if compress else 0x00
    struct.pack_into("<I", volume, 0x38, SECTORS_PER_CHUNK)
    volume[0x40:0x50] = hashlib.md5(media, usedforsecurity=False).digest()  # set identifier
    section(b"volume", bytes(volume) + _adler(bytes(volume)))

    sectors_offset = len(out)
    entries: list[int] = []
    payload = bytearray()
    data_start = sectors_offset + DESCRIPTOR
    for chunk in chunks:
        position = data_start + len(payload)
        packed = zlib.compress(chunk) if compress else b""
        if compress and len(packed) < len(chunk):  # incompressible chunks are stored raw
            payload += packed
            entries.append(position | 0x80000000)
        else:
            payload += chunk + _adler(chunk)
            entries.append(position)
    section(b"sectors", bytes(payload))

    table_header = struct.pack("<IIQI", len(entries), 0, 0, 0)
    offsets = b"".join(struct.pack("<I", e) for e in entries)
    table = table_header + _adler(table_header) + offsets + _adler(offsets)
    section(b"table", table)
    section(b"table2", table)

    if stored_md5 is not None:
        digest = hashlib.md5(media, usedforsecurity=False).digest() if stored_md5 == "auto" \
            else bytes.fromhex(stored_md5)
        hash_data = digest + bytes(16)
        section(b"hash", hash_data + _adler(hash_data))

    done_offset = len(out)
    out.extend(_descriptor(b"done", done_offset, DESCRIPTOR, next_offset=done_offset))
    path.write_bytes(bytes(out))
    return path
