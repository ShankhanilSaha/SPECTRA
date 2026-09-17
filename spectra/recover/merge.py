"""Merge and deduplicate results across tiers (doc 3 §7, FR-44).

The tiers overlap by design: T3 carving re-finds footage the index already described, and
an orphan block often shows up again in the carve. Reporting the same recording three
times inflates every total in the report, and silently dropping the duplicates loses the
fact that more than one method found it — which is corroboration worth keeping.

So: group by extent overlap, keep the highest tier, and record on the survivor that the
duplicates existed and at which tiers. Tier order is T1 > T2 > T3 > T4, matching the
coverage precedence `parsed > carved` for the same reason — the index-backed claim carries
metadata the carve cannot.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from spectra.core.models import Extent, Recording, RecoveryTier

TIER_RANK: dict[RecoveryTier, int] = {"T1": 0, "T2": 1, "T3": 2, "T4": 3}


@dataclass(frozen=True, slots=True)
class MergeStats:
    """What merging did, for the report and for AC-06's yield arithmetic."""

    kept: int
    dropped: int
    groups_with_duplicates: int

    def to_json(self) -> dict[str, int]:
        return {
            "kept": self.kept,
            "dropped": self.dropped,
            "groups_with_duplicates": self.groups_with_duplicates,
        }


def _span(rec: Recording) -> tuple[int, int]:
    """The outer span of a recording's extents. Fragmented recordings still group."""
    lo = min(e.offset for e in rec.extents)
    hi = max(e.end for e in rec.extents)
    return lo, hi


def _overlaps(a: Recording, b: Recording) -> bool:
    """True if any extent of `a` intersects any extent of `b`."""
    for ea in a.extents:
        for eb in b.extents:
            if ea.offset < eb.end and eb.offset < ea.end:
                return True
    return False


def _sort_key(rec: Recording) -> tuple[int, int, int, str]:
    lo, hi = _span(rec)
    return (TIER_RANK.get(rec.recovery_tier, 9), lo, -(hi - lo), rec.source_note)


def merge(*groups: Iterable[Recording]) -> tuple[list[Recording], MergeStats]:
    """Combine recordings from any number of tiers into one deduplicated, ordered list.

    Deterministic: input order does not affect the result (NFR-08). Grouping is transitive
    over overlap, so a long T1 recording absorbs several short carves that each touch it.
    """
    everything: list[Recording] = [rec for group in groups for rec in group]
    if not everything:
        return [], MergeStats(0, 0, 0)

    # Highest tier first so the survivor of each group is the first one seen.
    ordered = sorted(everything, key=_sort_key)

    clusters: list[list[Recording]] = []
    for rec in ordered:
        for cluster in clusters:
            if any(_overlaps(rec, member) for member in cluster):
                cluster.append(rec)
                break
        else:
            clusters.append([rec])

    kept: list[Recording] = []
    dropped = 0
    with_dupes = 0
    for cluster in clusters:
        survivor, *rest = cluster
        if not rest:
            kept.append(survivor)
            continue
        with_dupes += 1
        dropped += len(rest)
        tiers = sorted({r.recovery_tier for r in rest}, key=lambda t: TIER_RANK.get(t, 9))
        kept.append(
            Recording(
                channel=survivor.channel,
                stream=survivor.stream,
                t_start=survivor.t_start,
                t_end=survivor.t_end,
                extents=survivor.extents,
                codec=survivor.codec,
                resolution=survivor.resolution,
                fps=survivor.fps,
                size_bytes=survivor.size_bytes,
                recovery_tier=survivor.recovery_tier,
                confidence=survivor.confidence,
                source_note=survivor.source_note,
                frame_count=survivor.frame_count,
                notes=(
                    *survivor.notes,
                    f"{len(rest)} overlapping result(s) at {', '.join(tiers)} were merged "
                    f"into this one; the highest tier was kept (FR-44)",
                ),
            )
        )

    kept.sort(key=lambda r: (_span(r)[0], r.recovery_tier))
    return kept, MergeStats(len(kept), dropped, with_dupes)


def recording_extents(recordings: Sequence[Recording]) -> list[Extent]:
    """Every extent across a set of recordings — the input to the coverage map."""
    return sorted(e for rec in recordings for e in rec.extents)
