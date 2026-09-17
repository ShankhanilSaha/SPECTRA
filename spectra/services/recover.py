"""RecoverService — tiers T2/T3/T4, merge, and the coverage map (FR-29, FR-41..FR-46).

The orchestration `recover/` deliberately does not do: open the evidence, decide which
tiers apply, persist recordings, and write the coverage map. The tier engines stay
vendor-free and side-effect-free; this is where the side effects live.

Two behaviours worth knowing:

**Recovery does not require a successful parse.** A carve-only image — unknown layout
version, damaged superblock, a family we identify but cannot walk — is exactly the case
T3 exists for. `parse` refuses those (FR-03) and recovery must not. Tiers that need an
index are skipped with a recorded reason rather than failing the run.

**Existing T1 recordings take part in the merge.** A carve re-finds footage the index
already described; without the T1 rows in the comparison every recovered file would be
reported twice and every total inflated (FR-44).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from spectra.core.casestore import CaseStore
from spectra.core.models import DeviceTime, Extent, Recording
from spectra.plugins.base import LayoutNotSupported
from spectra.recover import carver, gop, orphans
from spectra.recover.coverage import Bucket, Claim, CoverageMap, claim, resolve

# Imported from the submodule, not the package: `spectra.recover` re-exports a
# function named `merge`, which shadows the module of the same name.
from spectra.recover.merge import merge as merge_recordings
from spectra.services import ServiceError
from spectra.services.evidence import open_evidence
from spectra.services.parse import _insert_recording, selected_plugin

DEFAULT_TIERS = ("T2", "T3")
VALID_TIERS = ("T2", "T3", "T4")


@dataclass(frozen=True, slots=True)
class RecoverSummary:
    """What recovery found, and honestly what it could not measure."""

    evidence_id: str
    tiers_requested: tuple[str, ...]
    tiers_run: tuple[str, ...]
    skipped: tuple[str, ...]
    added: int
    by_tier: dict[str, int]
    duplicates_merged: int
    coverage: dict[str, int]
    t1_minutes: float
    recovered_minutes: float
    items_without_time: int
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def gain_pct(self) -> float | None:
        """AC-06: extra recording-minutes over T1 alone, or None if not measurable."""
        if self.t1_minutes <= 0:
            return None
        return round(100.0 * self.recovered_minutes / self.t1_minutes, 1)

    def to_json(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "tiers_requested": list(self.tiers_requested),
            "tiers_run": list(self.tiers_run),
            "skipped": list(self.skipped),
            "added": self.added,
            "by_tier": dict(sorted(self.by_tier.items())),
            "duplicates_merged": self.duplicates_merged,
            "coverage": dict(sorted(self.coverage.items())),
            "t1_minutes": round(self.t1_minutes, 2),
            "recovered_minutes": round(self.recovered_minutes, 2),
            "items_without_time": self.items_without_time,
            "gain_pct": self.gain_pct,
            "notes": list(self.notes),
        }


def _minutes(rec: Recording) -> float:
    """Duration in minutes, or 0 where no time was recovered. Never inferred."""
    if rec.t_start is None or rec.t_end is None:
        return 0.0
    a, b = rec.t_start.local, rec.t_end.local
    if a is None or b is None or b < a:
        return 0.0
    return (b - a).total_seconds() / 60.0


def _existing(store: CaseStore, evidence_id: str) -> list[Recording]:
    """DB rows as `Recording`s, with the fields merge and the AC-06 arithmetic need."""
    cursor = store.conn.execute(
        "SELECT * FROM recording WHERE evidence_id = ? ORDER BY id", (evidence_id,)
    )
    names = [c[0] for c in cursor.description]
    out: list[Recording] = []
    for row in cursor:
        data = dict(zip(names, row, strict=True))
        extents = tuple(Extent(o, n) for o, n in json.loads(data["extents_json"] or "[]"))
        if not extents:
            continue
        out.append(
            Recording(
                channel=data["channel"],
                stream=data["stream"] or "unknown",
                t_start=_device_time(data, "t_local_start"),
                t_end=_device_time(data, "t_local_end"),
                extents=extents,
                codec=data["codec"] or "unknown",
                resolution=None,
                fps=data["fps"],
                size_bytes=data["size_bytes"] or 0,
                recovery_tier=data["recovery_tier"] or "T1",
                confidence=data["confidence"] if data["confidence"] is not None else 0.5,
                source_note=data["source_note"] or "",
                frame_count=data["frame_count"],
            )
        )
    return out


def _device_time(row: dict[str, Any], key: str) -> DeviceTime | None:
    """Rebuild just enough `DeviceTime` for duration arithmetic; raw stays authoritative."""
    from datetime import datetime

    value = row.get(key)
    if not value:
        return None
    try:
        local = datetime.fromisoformat(value)
    except ValueError:
        return None
    return DeviceTime(raw=0, encoding=row.get("t_device_encoding") or "unknown", local=local)


def _structural_claims(layout: Any) -> list[Claim]:
    out: list[Claim] = []
    for extent in getattr(layout, "index_extents", ()) or ():
        out.append(Claim(extent, "structural", "index"))
    for extent in getattr(layout, "log_extents", ()) or ():
        out.append(Claim(extent, "structural", "system log"))
    return out


def _bucket_for(tier: str) -> Bucket:
    return "parsed" if tier in ("T1", "T2") else "carved"


def recover(
    store: CaseStore,
    evidence_id: str,
    tiers: tuple[str, ...] = DEFAULT_TIERS,
    *,
    budget_s: float | None = None,
) -> RecoverSummary:
    """Run the requested tiers, merge with what is already known, write the coverage map."""
    unknown = [t for t in tiers if t not in VALID_TIERS]
    if unknown:
        raise ServiceError(
            f"unknown tier(s) {', '.join(unknown)}; valid tiers are {', '.join(VALID_TIERS)}"
        )
    if not tiers:
        raise ServiceError("no tiers requested")

    plugin, row = selected_plugin(store, evidence_id)
    parse_supported = bool(row["parse_supported"])
    params = {
        "family": plugin.family,
        "plugin_version": plugin.plugin_version,
        "tiers": list(tiers),
        "parse_supported": parse_supported,
    }

    with store.audit.operation("recover", target=evidence_id, params=params) as op:
        with open_evidence(store, evidence_id) as src:
            existing = _existing(store, evidence_id)
            layout = None
            skipped: list[str] = []
            notes: list[str] = []

            if parse_supported:
                try:
                    layout = plugin.superblock(src)
                except LayoutNotSupported as exc:
                    notes.append(f"superblock unavailable at recovery time: {exc}")
            else:
                notes.append(
                    "evidence is carve-only (FR-03): index-backed tiers are skipped, "
                    "signature carving is unaffected"
                )

            found: list[Recording] = []
            ran: list[str] = []

            if "T2" in tiers:
                if layout is None:
                    skipped.append("T2 (no parseable index)")
                else:
                    found.extend(orphans.recover(plugin, src, layout))
                    ran.append("T2")

            if "T3" in tiers or "T4" in tiers:
                regions = None
                if "T4" in tiers:
                    regions = carver.readable_regions(src)
                    ran.append("T4")
                    notes.append(
                        f"T4: scan restricted to {len(regions)} readable range(s); "
                        "unreadable spans are hard boundaries"
                    )
                if "T3" in tiers:
                    ran.append("T3")
                hits = carver.scan(
                    src,
                    plugin.carve_signatures(),
                    budget_s=budget_s,
                    regions=regions,
                )
                found.extend(gop.reassemble(plugin, src, hits))

            kept, stats = merge_recordings(existing, found)

            known = {r.extents for r in existing}
            fresh = [r for r in kept if r.extents not in known]

            store.conn.execute("BEGIN IMMEDIATE")
            try:
                first = store.conn.execute("SELECT COUNT(*) FROM recording").fetchone()[0]
                for i, rec in enumerate(fresh, start=1):
                    _insert_recording(store, f"REC-{first + i:04d}", evidence_id, rec)

                coverage = _write_coverage(store, evidence_id, src, layout, kept)
                store.conn.commit()
            except BaseException:
                store.conn.rollback()
                raise

        by_tier: dict[str, int] = {}
        for rec in fresh:
            by_tier[rec.recovery_tier] = by_tier.get(rec.recovery_tier, 0) + 1

        t1_minutes = sum(_minutes(r) for r in existing if r.recovery_tier == "T1")
        recovered_minutes = sum(_minutes(r) for r in fresh)
        without_time = sum(1 for r in fresh if r.t_start is None or r.t_end is None)
        if without_time:
            notes.append(
                f"{without_time} recovered item(s) carry no recoverable time, so their "
                "duration is not counted in the yield figure (FR-45)"
            )

        summary = RecoverSummary(
            evidence_id=evidence_id,
            tiers_requested=tuple(tiers),
            tiers_run=tuple(ran),
            skipped=tuple(skipped),
            added=len(fresh),
            by_tier=by_tier,
            duplicates_merged=stats.dropped,
            coverage=coverage,
            t1_minutes=t1_minutes,
            recovered_minutes=recovered_minutes,
            items_without_time=without_time,
            notes=tuple(notes),
        )
        op.result_params = summary.to_json()

    store.write_manifest()
    return summary


def _write_coverage(
    store: CaseStore,
    evidence_id: str,
    src: Any,
    layout: Any,
    recordings: list[Recording],
) -> dict[str, int]:
    """Resolve every claim into one tiling and persist it. The first writer of this table."""
    claims: list[Claim] = []
    if layout is not None:
        claims.extend(_structural_claims(layout))
    for rec in recordings:
        bucket = _bucket_for(rec.recovery_tier)
        for extent in rec.extents:
            claims.append(Claim(extent, bucket, rec.source_note[:60]))
    for gap in src.gaps():
        claims.append(claim(gap.offset, gap.length, "unreadable", "bad sector"))

    cov: CoverageMap = resolve(src.size, claims)
    store.conn.execute("DELETE FROM coverage WHERE evidence_id = ?", (evidence_id,))
    store.conn.executemany(
        "INSERT INTO coverage (evidence_id, offset, length, bucket) VALUES (?,?,?,?)",
        [(evidence_id, r.offset, r.length, r.bucket) for r in cov.runs],
    )
    return cov.totals()


def coverage_rows(store: CaseStore, evidence_id: str) -> list[dict[str, Any]]:
    cursor = store.conn.execute(
        "SELECT * FROM coverage WHERE evidence_id = ? ORDER BY offset", (evidence_id,)
    )
    names = [c[0] for c in cursor.description]
    return [dict(zip(names, row, strict=True)) for row in cursor]
