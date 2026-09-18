"""The byte-level coverage map (doc 3 §6.2, FR-29) — what we read, and what we did not.

Every byte of an evidence image lands in exactly one of five buckets, and the five sum to
the image size exactly. That invariant is the point: a tool that cannot say what fraction
of a disk it failed to explain cannot honestly say what it found, and the report's §7
negative findings are generated from this map (FR-81), not written by the examiner.

| bucket | meaning |
|---|---|
| `structural` | superblock, index, logs — metadata we understood |
| `parsed` | claimed by a T1 or T2 recording (index-backed) |
| `carved` | recovered at T3 or T4 (signature-only) |
| `unreadable` | bad sectors; zero-filled and recorded by `EvidenceSource` |
| `unaccounted` | **bytes we could not explain** — derived, never claimed |

### Precedence (this module owns the contract — CLAUDE.md §14, owner P3)

Claims overlap in practice: a carve re-finds footage the index already described, an
index entry runs into a bad-sector run, a plugin's extent brushes the index area. The
resolution rule is ordered by *which label misleads most if it were wrong*:

    unreadable > structural > parsed > carved > unaccounted

- **`unreadable` outranks everything.** The sectors were not read. No interpretation laid
  over them can be more true than that, and a parser will happily emit a recording whose
  extents span a hole the source zero-filled. Physical truth beats any reading of it.
- **`structural` outranks `parsed`.** Metadata must not be silently counted as video. If a
  recording's extent overlaps the index it was read from, that is a parser bug, and it
  should surface as metadata rather than hide inside a recording's byte total.
- **`parsed` outranks `carved`.** Both are true; the index-backed claim is the stronger
  one, and this matches `merge.py` keeping the highest tier for the same footage (FR-44).
- **`unaccounted` is never claimed.** It is the complement of everything else. Passing it
  to `claim()` raises — it is a finding we derive, not an assertion anyone gets to make.

Runs of the same bucket are merged, so a fragmented carve over a multi-terabyte image
costs rows proportional to the number of *transitions*, not to the number of extents
(doc 2 NFR-04 — the case file is a court exhibit, not a scratch database).
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Literal

from spectra.core.models import Extent

Bucket = Literal["parsed", "carved", "structural", "unreadable", "unaccounted"]

#: Highest precedence first. Index position *is* the precedence; see the module docstring.
BUCKET_PRECEDENCE: tuple[Bucket, ...] = (
    "unreadable",
    "structural",
    "parsed",
    "carved",
    "unaccounted",
)

#: The derived bucket. Never claimable.
RESIDUE: Bucket = "unaccounted"

_RANK: dict[Bucket, int] = {name: i for i, name in enumerate(BUCKET_PRECEDENCE)}


@dataclass(frozen=True, slots=True)
class Claim:
    """An assertion that `extent` is explained, and by what.

    `note` travels into the report so a reader can see *why* a range carries its bucket
    ("HIKBTREE primary", "REC-0042 T1", "bad sector run"). Keep it short; it is displayed.
    """

    extent: Extent
    bucket: Bucket
    note: str = ""

    def __post_init__(self) -> None:
        if self.bucket == RESIDUE:
            raise ValueError(
                f"{RESIDUE!r} is derived, not claimed: it is whatever no claim explains"
            )
        if self.bucket not in _RANK:
            raise ValueError(f"unknown coverage bucket {self.bucket!r}")


def claim(offset: int, length: int, bucket: Bucket, note: str = "") -> Claim:
    """Convenience constructor — `claim(0, 512, "structural", "master sector")`."""
    return Claim(Extent(offset, length), bucket, note)


@dataclass(frozen=True, slots=True, order=True)
class CoverageRun:
    """One resolved, contiguous run of a single bucket. Adjacent same-bucket runs merge."""

    offset: int
    length: int
    bucket: Bucket

    @property
    def end(self) -> int:
        return self.offset + self.length

    def to_row(self, evidence_id: str) -> dict[str, object]:
        """A row for the `coverage` table (casestore.py); column names match the schema."""
        return {
            "evidence_id": evidence_id,
            "offset": self.offset,
            "length": self.length,
            "bucket": self.bucket,
        }


@dataclass(frozen=True, slots=True)
class CoverageMap:
    """Resolved coverage for one evidence image. Construct via `resolve()`."""

    size: int
    runs: tuple[CoverageRun, ...]

    def __post_init__(self) -> None:
        self.verify()

    def totals(self) -> dict[Bucket, int]:
        """Bytes per bucket, every bucket present even at zero — a stable report shape."""
        out: dict[Bucket, int] = dict.fromkeys(BUCKET_PRECEDENCE, 0)
        for run in self.runs:
            out[run.bucket] += run.length
        return out

    @property
    def unaccounted_bytes(self) -> int:
        """The §7 negative finding. High entropy here means data we did not recover."""
        return self.totals()[RESIDUE]

    @property
    def explained_fraction(self) -> float:
        """0.0–1.0. An empty image is vacuously fully explained."""
        if self.size == 0:
            return 1.0
        return (self.size - self.unaccounted_bytes) / self.size

    def bucket_at(self, offset: int) -> Bucket | None:
        """The bucket covering `offset`, or None if outside the image. Binary search."""
        lo, hi = 0, len(self.runs) - 1
        while lo <= hi:
            mid = (lo + hi) // 2
            run = self.runs[mid]
            if offset < run.offset:
                hi = mid - 1
            elif offset >= run.end:
                lo = mid + 1
            else:
                return run.bucket
        return None

    def verify(self) -> None:
        """Raise unless the runs tile `[0, size)` exactly, in order, with no gap or overlap.

        This is the invariant the whole map exists to provide, so it is checked on
        construction rather than offered as an optional audit.
        """
        if self.size < 0:
            raise ValueError(f"negative image size {self.size}")
        cursor = 0
        for run in self.runs:
            if run.length <= 0:
                raise ValueError(f"empty coverage run at {run.offset}")
            if run.offset != cursor:
                raise ValueError(
                    f"coverage is not contiguous: expected a run at {cursor}, "
                    f"found one at {run.offset}"
                )
            cursor = run.end
        if cursor != self.size:
            raise ValueError(
                f"coverage runs sum to {cursor} bytes, image is {self.size} "
                f"(difference {self.size - cursor})"
            )

    def to_rows(self, evidence_id: str) -> Iterator[dict[str, object]]:
        for run in self.runs:
            yield run.to_row(evidence_id)

    def to_json(self) -> dict[str, object]:
        """Deterministic summary for `findings.json` (FR-86, NFR-08)."""
        totals = self.totals()
        return {
            "size_bytes": self.size,
            "runs": len(self.runs),
            "totals": {name: totals[name] for name in BUCKET_PRECEDENCE},
            "unaccounted_bytes": self.unaccounted_bytes,
            "explained_fraction": round(self.explained_fraction, 6),
        }


def resolve(size: int, claims: Iterable[Claim]) -> CoverageMap:
    """Resolve overlapping claims into one tiling of `[0, size)`.

    Claims are clamped to the image, empties dropped, overlaps decided by
    `BUCKET_PRECEDENCE`, everything unclaimed becomes `unaccounted`, and adjacent runs of
    the same bucket are merged.

    A sweep over claim boundaries: at each boundary the set of active claims changes, and
    the winner is the highest-precedence bucket still active. O(n log n) in the number of
    claims, and independent of image size — a 48 TB image with four claims costs four.
    """
    if size < 0:
        raise ValueError(f"negative image size {size}")
    if size == 0:
        return CoverageMap(0, ())

    # Clamp to the image and index by bucket rank. Anything wholly outside is dropped
    # rather than raising: a carver legitimately probes past the tail on the last window.
    starts: dict[int, list[int]] = {}
    ends: dict[int, list[int]] = {}
    boundaries: set[int] = {0, size}
    for c in claims:
        lo = max(0, c.extent.offset)
        hi = min(size, c.extent.end)
        if hi <= lo:
            continue
        rank = _RANK[c.bucket]
        starts.setdefault(lo, []).append(rank)
        ends.setdefault(hi, []).append(rank)
        boundaries.add(lo)
        boundaries.add(hi)

    active = [0] * len(BUCKET_PRECEDENCE)
    ordered = sorted(boundaries)
    runs: list[CoverageRun] = []

    for left, right in zip(ordered, ordered[1:], strict=False):
        for rank in ends.get(left, ()):
            active[rank] -= 1
        for rank in starts.get(left, ()):
            active[rank] += 1
        if right <= left:
            continue
        winner = next(
            (BUCKET_PRECEDENCE[r] for r in range(len(active)) if active[r] > 0),
            RESIDUE,
        )
        if runs and runs[-1].bucket == winner and runs[-1].end == left:
            prev = runs[-1]
            runs[-1] = CoverageRun(prev.offset, right - prev.offset, winner)
        else:
            runs.append(CoverageRun(left, right - left, winner))

    return CoverageMap(size, tuple(runs))
