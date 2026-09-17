"""T3 — index-independent signature carving (doc 3 §7.2, FR-42).

The tier that finds footage no index describes: recordings whose entries were freed and
reused, blocks left behind after a format, and the tail of a partially overwritten block.
It reads nothing but the image and the plugin's own `carve_signatures()`, so it works on a
disk whose metadata is entirely gone — and it is vendor-free, because the signature and
its validator both come from the plugin (doc 8 §3.4).

Two details separate a carver that works from one that quietly lies:

- **Overlap.** Windows are read with `overlap` bytes of the next window appended, so a
  pattern straddling a chunk boundary is still found. Without it a carver silently misses
  roughly one hit per boundary, and nothing in the output says so. Hits are emitted in
  strictly increasing offset order, which also deduplicates the rescanned prefix.
- **Validation before acceptance.** A bare pattern match is not a frame. `Signature.validate`
  re-reads through `EvidenceSource` and returns a length only if the structure holds; the
  conformance suite plants a plugin's own pattern in noise and asserts nothing validates
  (`tests/test_plugins.py`). Random data must yield zero recordings (TC-RC-05).
"""

from __future__ import annotations

import time
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass

from spectra.core.models import Signature
from spectra.core.source import MIB, EvidenceSource, windows

#: Default window. Large enough to amortise mapping, small enough for the NFR-04 ceiling.
CHUNK = 64 * MIB

#: Default overlap. Must exceed the longest header any registered signature needs in order
#: to validate, or a hit near a boundary fails validation instead of being found.
MAX_FRAME_HEADER = 64 * 1024


class CarveBudgetExceeded(RuntimeError):
    """The scan ran past its wall-clock budget. Raised rather than silently truncating."""


@dataclass(frozen=True, slots=True, order=True)
class CarveHit:
    """One validated structure found without reference to any index."""

    offset: int
    length: int
    signature: str

    @property
    def end(self) -> int:
        return self.offset + self.length


def _find_all(haystack: memoryview, needle: bytes, base: int) -> Iterator[int]:
    """Absolute offsets of every occurrence, including overlapping ones."""
    if not needle:
        return
    data = bytes(haystack)
    start = 0
    while True:
        found = data.find(needle, start)
        if found < 0:
            return
        yield base + found
        start = found + 1


def scan(
    src: EvidenceSource,
    signatures: Sequence[Signature],
    *,
    chunk: int = CHUNK,
    overlap: int = MAX_FRAME_HEADER,
    budget_s: float | None = None,
    regions: Iterable[tuple[int, int]] | None = None,
) -> Iterator[CarveHit]:
    """Yield validated signature hits in increasing offset order.

    `regions` restricts the scan to `(offset, length)` spans — pass `src.readable_ranges()`
    to skip known-bad sectors, which is what T4 does. Overlapping hits from different
    signatures are all reported; deciding between them is the caller's job.

    `budget_s` bounds wall-clock so a hostile image cannot hang the tool (TC-RB-07). It
    raises rather than returning a short result, because a truncated carve that looks
    complete is exactly the confident-wrong answer the release gate forbids.
    """
    if chunk <= 0:
        raise ValueError(f"chunk must be positive, got {chunk}")
    if overlap < 0:
        raise ValueError(f"overlap must not be negative, got {overlap}")
    if not signatures:
        return

    started = time.monotonic()
    last_emitted = -1

    for window_offset, view in windows(src, chunk=chunk, overlap=overlap):
        if budget_s is not None and time.monotonic() - started > budget_s:
            raise CarveBudgetExceeded(
                f"carve exceeded {budget_s}s at offset {window_offset}"
            )
        if regions is not None and not _intersects(window_offset, len(view), regions):
            continue

        candidates: list[CarveHit] = []
        for sig in signatures:
            for hit_offset in _find_all(view, sig.pattern, window_offset):
                if hit_offset <= last_emitted:
                    continue
                length = sig.validate(src, hit_offset)
                if length is None or length <= 0:
                    continue
                candidates.append(CarveHit(hit_offset, length, sig.name))

        for hit in sorted(candidates):
            if hit.offset <= last_emitted:
                continue
            last_emitted = hit.offset
            yield hit


def _intersects(offset: int, length: int, regions: Iterable[tuple[int, int]]) -> bool:
    end = offset + length
    return any(offset < r_off + r_len and r_off < end for r_off, r_len in regions)


def readable_regions(src: EvidenceSource) -> list[tuple[int, int]]:
    """`src.readable_ranges()` as `(offset, length)` pairs — the T4 scan restriction."""
    return [(e.offset, e.length) for e in src.readable_ranges()]
