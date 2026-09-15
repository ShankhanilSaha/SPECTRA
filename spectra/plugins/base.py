"""THE CONTRACT — `VendorPlugin` (doc 3 §4, D2, FR-20). Co-owned by P1, P2, P3.

A format family is one module implementing this protocol plus one line in
`registry.py`. Core code never imports a vendor module directly (AC-12).

Draft until the end-of-Phase-1 freeze (CLAUDE.md §14). Differences from doc 3 §4:
- `probe(src, plan)` receives the engine's `ProbePlan` (FR-01 offsets) — see `ProbePlan`.
- Optional `system_log()` / `firmware_facts()` (FR-05, FR-07) arrive in finals and are not
  part of the protocol yet.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import ClassVar, Protocol, runtime_checkable

from spectra.core.models import (
    DeviceTime,
    DiskLayout,
    Extent,
    Frame,
    ProbePlan,
    ProbeResult,
    Recording,
    Signature,
)
from spectra.core.source import EvidenceSource


class LayoutNotSupported(Exception):
    """The plugin cannot parse this layout version; the caller must route to carving (FR-03)."""


@runtime_checkable
class VendorPlugin(Protocol):
    family: ClassVar[str]
    layout_versions: ClassVar[tuple[str, ...]]
    plugin_version: ClassVar[str]

    @classmethod
    def probe(cls, src: EvidenceSource, plan: ProbePlan) -> ProbeResult | None:
        """Q1. Bounded reads only. Return None if not this family. Never guess: an unknown
        layout version returns a result with `parse_supported=False` (FR-03)."""
        ...

    def superblock(self, src: EvidenceSource) -> DiskLayout:
        """Q2. Raises `LayoutNotSupported` rather than mis-parsing."""
        ...

    def enumerate(
        self, src: EvidenceSource, layout: DiskLayout, include_orphans: bool
    ) -> Iterator[Recording]:
        """Q3. T1 when `include_orphans` is False, T1+T2 when True. Streams (D4)."""
        ...

    def frames(self, src: EvidenceSource, extent: Extent) -> Iterator[Frame]:
        """Q4. Container frames inside `extent`; payloads are views, not copies."""
        ...

    def carve_signatures(self) -> list[Signature]:
        """T3 byte patterns with false-positive-rejecting validators (FR-42)."""
        ...

    def decode_time(self, raw: int | bytes, layout: DiskLayout | None) -> DeviceTime:
        """Vendor timestamp → `DeviceTime`, with known-good vectors per layout (FR-54)."""
        ...
