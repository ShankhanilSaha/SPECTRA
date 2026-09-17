"""`Hasher` — MD5 + SHA-1 + SHA-256 in one pass (doc 3 §3.2, FR-11, FR-32, FR-82).

SHA-256 is the integrity assertion. MD5 and SHA-1 are computed because the BSA s. 63(4)
certificate schedule names exactly those three algorithms, and a certificate cannot tick
a box for a digest the tool never calculated. Reports say in words which one is relied on.

Note the two sources differ and the union satisfies both: the problem statement asks for
"MD5 and SHA-256"; the statute's schedule lists SHA1, SHA256 and MD5 (CLAUDE.md §17 item 1).
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from spectra.core.source import MIB, EvidenceSource


@dataclass(frozen=True, slots=True)
class Digests:
    md5: str
    sha1: str
    sha256: str
    size: int

    def to_json(self) -> dict[str, object]:
        return {"md5": self.md5, "sha1": self.sha1, "sha256": self.sha256,
                "size": self.size}


class Hasher:
    """MD5, SHA-1 and SHA-256 over a single stream of updates.

    SHA-256 is the integrity assertion. MD5 is kept because Indian departmental records
    still cite it. SHA-1 is kept because the BSA s. 63(4) certificate schedule names it
    alongside the other two, and a certificate cannot tick a box we never computed —
    the problem statement asks for MD5 and SHA-256, the statute asks for all three, and
    computing the third costs one more pass over the same bytes (CLAUDE.md §17 item 1).
    """

    def __init__(self) -> None:
        self._md5 = hashlib.md5(usedforsecurity=False)
        self._sha1 = hashlib.sha1(usedforsecurity=False)
        self._sha256 = hashlib.sha256()
        self._size = 0

    def update(self, data: bytes | bytearray | memoryview) -> None:
        self._md5.update(data)
        self._sha1.update(data)
        self._sha256.update(data)
        self._size += len(data)

    def digests(self) -> Digests:
        return Digests(
            self._md5.hexdigest(),
            self._sha1.hexdigest(),
            self._sha256.hexdigest(),
            self._size,
        )


def hash_source(
    src: EvidenceSource,
    chunk: int = MIB,
    progress: Callable[[int, int], None] | None = None,
) -> Digests:
    """Hash evidence as addressed through its source (FR-17, FR-18 hash-on-ingest).

    For E01 this is the decompressed media, not the `.E01` segment files. Zero-filled
    gaps are hashed as zeros; the caller must report `src.gaps()` alongside the digest.
    """
    hasher = Hasher()
    offset = 0
    while offset < src.size:
        data = src.read(offset, chunk)
        hasher.update(data)
        offset += len(data)
        if progress is not None:
            progress(offset, src.size)
    return hasher.digests()


def hash_case_file(path: Path, chunk: int = MIB) -> Digests:
    """Hash a file the case owns (artefact, attachment, log). Never an evidence path —
    evidence is hashed through `hash_source` so it is only ever opened in `source.py`."""
    hasher = Hasher()
    with open(path, "rb") as handle:
        while block := handle.read(chunk):
            hasher.update(block)
    return hasher.digests()
