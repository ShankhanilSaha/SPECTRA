"""T2 — orphaned index entries, validated before they are trusted (doc 3 §7.1, FR-41).

A recorder frees an index entry long before it overwrites the block the entry pointed at.
In that window the video is intact and invisible to the vendor's own software, and on a
disk pulled from service the window never closes. T2 is reading those entries.

The danger is the mirror image of the opportunity: a freed entry may equally point at a
block that *has* since been rewritten, and reporting that as recovered footage with the
old entry's channel and timestamps is precisely the confident-wrong answer the release
gate forbids (§9.3). So every orphan is checked against what the block actually contains
before it is kept.

### Checkable, failed, and inapplicable

Three checks, from doc 3 §7.1: the block still carries the family's structure; the frame
timestamps fall inside the entry's claimed window; the frame channel matches the entry's.

The distinction that matters is between a check that **fails** and one that **cannot be
run**. Hikvision's program-stream payload carries no dependable per-frame channel or
timestamp at all, so those two checks are *inapplicable* there, not failed — downgrading
every Hikvision orphan because its container does not carry a field would discard good
evidence and misreport why. Only a genuine contradiction downgrades; an inapplicable check
is recorded as such and the verdict rests on the ones that could be run.

A downgraded orphan is not discarded either. It becomes a carve candidate: the bytes may
still hold recoverable video, they simply cannot be described by that entry's metadata.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Literal, Protocol

from spectra.core.models import DiskLayout, Extent, Recording
from spectra.core.source import EvidenceSource

CheckResult = Literal["pass", "fail", "inapplicable"]

#: Frames read from the head of a block before deciding. Enough to see a channel and a
#: couple of timestamps; small enough that validating thousands of entries stays cheap.
SAMPLE_FRAMES = 8


class _Plugin(Protocol):
    """The slice of `VendorPlugin` this module uses. Vendor-free by construction."""

    family: str

    def enumerate(
        self, src: EvidenceSource, layout: DiskLayout, include_orphans: bool
    ) -> Iterator[Recording]: ...

    def frames(self, src: EvidenceSource, extent: Extent) -> Iterator[object]: ...


@dataclass(frozen=True, slots=True)
class Check:
    name: str
    result: CheckResult
    detail: str = ""


@dataclass(frozen=True, slots=True)
class OrphanVerdict:
    """What we concluded about one freed entry, and why."""

    recording: Recording
    checks: tuple[Check, ...] = field(default_factory=tuple)

    @property
    def failed(self) -> tuple[Check, ...]:
        return tuple(c for c in self.checks if c.result == "fail")

    @property
    def ran(self) -> tuple[Check, ...]:
        return tuple(c for c in self.checks if c.result != "inapplicable")

    @property
    def trusted(self) -> bool:
        """Kept at T2 only if something was actually checked and nothing contradicted it."""
        return bool(self.ran) and not self.failed

    def explain(self) -> str:
        return "; ".join(
            f"{c.name}: {c.result}" + (f" ({c.detail})" if c.detail else "")
            for c in self.checks
        )


def _sample(plugin: _Plugin, src: EvidenceSource, extent: Extent) -> list[object]:
    out: list[object] = []
    try:
        for frame in plugin.frames(src, extent):
            out.append(frame)
            if len(out) >= SAMPLE_FRAMES:
                break
    except (ValueError, OSError):
        return out
    return out


def validate(
    plugin: _Plugin, src: EvidenceSource, rec: Recording
) -> OrphanVerdict:
    """Run the three checks against the block the entry points at."""
    if not rec.extents:
        return OrphanVerdict(rec, (Check("structure", "fail", "entry has no extent"),))

    frames = _sample(plugin, src, rec.extents[0])
    checks: list[Check] = []

    # 1. Structure — does the block still parse as this family's container at all?
    if frames:
        checks.append(Check("structure", "pass", f"{len(frames)} frames at block head"))
    else:
        checks.append(
            Check("structure", "fail", "no parseable frames: block likely overwritten")
        )
        return OrphanVerdict(rec, tuple(checks))

    # 2. Channel — only decidable if the container carries one.
    channels = {getattr(f, "channel", None) for f in frames}
    channels.discard(None)
    if not channels:
        checks.append(
            Check("channel", "inapplicable", "container carries no per-frame channel")
        )
    elif rec.channel is None:
        checks.append(Check("channel", "inapplicable", "entry declares no channel"))
    elif channels == {rec.channel}:
        checks.append(Check("channel", "pass", f"frames report channel {rec.channel}"))
    else:
        found = ",".join(str(c) for c in sorted(channels))
        checks.append(
            Check("channel", "fail", f"entry says {rec.channel}, frames say {found}")
        )

    # 3. Time — only decidable if the container carries per-frame time and the entry a window.
    times = [
        t.local
        for t in (getattr(f, "t_device", None) for f in frames)
        if t is not None and getattr(t, "local", None) is not None
    ]
    window = (
        rec.t_start.local if rec.t_start else None,
        rec.t_end.local if rec.t_end else None,
    )
    if not times:
        checks.append(
            Check("time", "inapplicable", "container carries no per-frame timestamp")
        )
    elif window[0] is None and window[1] is None:
        checks.append(Check("time", "inapplicable", "entry declares no time window"))
    else:
        lo, hi = window
        outside = [
            t for t in times if (lo is not None and t < lo) or (hi is not None and t > hi)
        ]
        if outside:
            checks.append(
                Check(
                    "time",
                    "fail",
                    f"{len(outside)} of {len(times)} frame times fall outside the entry window",
                )
            )
        else:
            checks.append(Check("time", "pass", f"{len(times)} frame times inside window"))

    return OrphanVerdict(rec, tuple(checks))


def recover(
    plugin: _Plugin,
    src: EvidenceSource,
    layout: DiskLayout,
) -> Iterator[Recording]:
    """Yield validated T2 recordings; downgrade the rest to carve candidates.

    Downgraded entries keep their extents and lose their metadata: the bytes may still be
    recoverable, but nothing the freed entry said about them survived checking, so
    asserting its channel or its timestamps would be fabrication (FR-45).
    """
    for rec in plugin.enumerate(src, layout, include_orphans=True):
        if rec.recovery_tier != "T2":
            continue  # T1 entries are not this tier's business
        verdict = validate(plugin, src, rec)
        if verdict.trusted:
            yield Recording(
                channel=rec.channel,
                stream=rec.stream,
                t_start=rec.t_start,
                t_end=rec.t_end,
                extents=rec.extents,
                codec=rec.codec,
                resolution=rec.resolution,
                fps=rec.fps,
                size_bytes=rec.size_bytes,
                recovery_tier="T2",
                confidence=min(0.9, rec.confidence + 0.2),
                source_note="orphaned index entry, validated against block content",
                frame_count=rec.frame_count,
                notes=(*rec.notes, f"T2 validation — {verdict.explain()}"),
            )
        else:
            yield Recording(
                channel=None,
                stream="unknown",
                t_start=None,
                t_end=None,
                extents=rec.extents,
                codec="unknown",
                resolution=None,
                fps=None,
                size_bytes=rec.size_bytes,
                recovery_tier="T3",
                confidence=0.2,
                source_note="orphaned entry failed validation; carve candidate only",
                notes=(
                    *rec.notes,
                    f"T2 validation failed — {verdict.explain()}",
                    "entry metadata discarded: the block did not corroborate it, so its "
                    "channel and timestamps are not asserted (FR-45)",
                ),
            )
