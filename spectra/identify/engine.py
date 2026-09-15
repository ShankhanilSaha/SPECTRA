"""Identification engine — probe dispatch, confidence, ambiguity (doc 3 §4.1, FR-01..FR-04).

Policy, chosen to make a false-confident misidentification impossible (AC-01, TC-ID-02):
- no plugin matches → `unknown` (carving only; the dossier, FR-09, arrives in finals);
- exactly one matches → `identified`;
- more than one matches → `ambiguous`: every candidate is reported with its confidence and
  matched bytes, and **nothing is selected automatically**. The operator selects, and the
  selection is an audit record (`IdentifyService.select`). Doc 3 §4.1 sketches picking the
  highest confidence; the leading candidate is still listed first, but acting on it is the
  examiner's decision, not the tool's (doc 7 §4 step 4).

A plugin that raises during `probe` is contained and reported, never treated as a match.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from spectra.core.models import Extent, ProbePlan, ProbeResult, SignatureMatch
from spectra.core.source import MIB, SECTOR_SIZE, EvidenceSource
from spectra.plugins.base import VendorPlugin

DEFAULT_SWEEP_WINDOWS = 32
DEFAULT_WINDOW = MIB

Status = Literal["identified", "ambiguous", "unknown"]
Support = Literal["parse", "carve_only", "none", "pending_selection"]


class IdentifyError(Exception):
    """Identification cannot run on this evidence (e.g. zero-length)."""


@dataclass(frozen=True, slots=True)
class ProbeFailure:
    family: str
    plugin_version: str
    error_type: str
    message: str


@dataclass(frozen=True, slots=True)
class IdentificationResult:
    status: Status
    candidates: tuple[ProbeResult, ...]
    selected: ProbeResult | None
    plan: ProbePlan
    observations: tuple[SignatureMatch, ...]
    errors: tuple[ProbeFailure, ...]

    @property
    def support(self) -> Support:
        if self.status == "ambiguous":
            return "pending_selection"
        if self.selected is None:
            return "none"
        return "parse" if self.selected.parse_supported else "carve_only"

    def to_json(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "support": self.support,
            "selected_family": self.selected.family if self.selected else None,
            "candidates": [probe_to_json(c) for c in self.candidates],
            "observations": [match_to_json(m) for m in self.observations],
            "errors": [
                {"family": e.family, "plugin_version": e.plugin_version,
                 "error_type": e.error_type, "message": e.message}
                for e in self.errors
            ],
            "plan": plan_to_json(self.plan),
        }


def match_to_json(match: SignatureMatch) -> dict[str, Any]:
    return {"offset": match.offset, "hex": match.data.hex(), "description": match.description}


def probe_to_json(result: ProbeResult) -> dict[str, Any]:
    return {
        "family": result.family,
        "layout_version": result.layout_version,
        "confidence": result.confidence,
        "parse_supported": result.parse_supported,
        "plugin_version": result.plugin_version,
        "matches": [match_to_json(m) for m in result.matches],
        "note": result.note,
    }


def plan_to_json(plan: ProbePlan) -> dict[str, Any]:
    return {
        "lba0": plan.lba0.to_json(),
        "head": plan.head.to_json(),
        "tail": plan.tail.to_json(),
        "sweep": [e.to_json() for e in plan.sweep],
    }


def build_plan(
    size: int, sweep_windows: int = DEFAULT_SWEEP_WINDOWS, window: int = DEFAULT_WINDOW
) -> ProbePlan:
    """FR-01: LBA 0, first 1 MiB, last 1 MiB, and `sweep_windows` evenly spaced windows
    across the middle of the evidence."""
    if sweep_windows < 0 or window <= 0:
        raise ValueError("sweep_windows must be ≥ 0 and window > 0")
    head = Extent(0, min(window, size))
    tail_start = max(0, size - window)
    tail = Extent(tail_start, size - tail_start)
    sweep: list[Extent] = []
    middle_start, middle_end = head.end, tail.offset
    span = middle_end - middle_start
    if sweep_windows and span > 0:
        step = span / sweep_windows
        for i in range(sweep_windows):
            start = middle_start + int(i * step)
            length = min(window, middle_end - start)
            if length > 0 and (not sweep or start >= sweep[-1].end):
                sweep.append(Extent(start, length))
    return ProbePlan(
        lba0=Extent(0, min(SECTOR_SIZE, size)), head=head, tail=tail, sweep=tuple(sweep)
    )


# Standard-filesystem and partition signatures. Not a classification (generic_fs is a
# finals plugin) — an observation that explains an "unknown" result (TC-RB-02).
_STANDARD_SIGNATURES: tuple[tuple[int, bytes, str], ...] = (
    (510, b"\x55\xaa", "MBR / boot-sector signature"),
    (512, b"EFI PART", "GPT partition table header"),
    (3, b"NTFS    ", "NTFS boot sector"),
    (3, b"EXFAT   ", "exFAT boot sector"),
    (82, b"FAT32   ", "FAT32 boot sector"),
    (54, b"FAT1", "FAT12/FAT16 boot sector"),
    (1080, b"\x53\xef", "ext2/3/4 superblock magic"),
    (0, b"XFSB", "XFS superblock"),
)


def standard_observations(src: EvidenceSource) -> tuple[SignatureMatch, ...]:
    head = src.read(0, 4096)
    found = []
    for offset, magic, description in _STANDARD_SIGNATURES:
        if head[offset : offset + len(magic)] == magic:
            found.append(SignatureMatch(offset, magic, description))
    return tuple(found)


def identify(
    src: EvidenceSource,
    plugins: Sequence[type[VendorPlugin]] | None = None,
    plan: ProbePlan | None = None,
) -> IdentificationResult:
    if src.size == 0:
        raise IdentifyError("evidence is zero-length: there is nothing to identify")
    if plugins is None:
        from spectra.plugins.registry import REGISTRY

        plugins = REGISTRY
    plan = plan or build_plan(src.size)
    candidates: list[ProbeResult] = []
    errors: list[ProbeFailure] = []
    for plugin in plugins:
        try:
            result = plugin.probe(src, plan)
        except Exception as exc:  # noqa: BLE001 — a crashing probe must not end identification
            errors.append(ProbeFailure(plugin.family, plugin.plugin_version,
                                       type(exc).__name__, str(exc)))
            continue
        if result is None:
            continue
        if result.family != plugin.family:
            errors.append(ProbeFailure(plugin.family, plugin.plugin_version, "ContractViolation",
                                       f"probe returned family {result.family!r}"))
            continue
        candidates.append(result)
    candidates.sort(key=lambda r: (-r.confidence, r.family))
    if not candidates:
        status: Status = "unknown"
        selected = None
    elif len(candidates) == 1:
        status, selected = "identified", candidates[0]
    else:
        status, selected = "ambiguous", None
    return IdentificationResult(
        status=status,
        candidates=tuple(candidates),
        selected=selected,
        plan=plan,
        observations=standard_observations(src),
        errors=tuple(errors),
    )
