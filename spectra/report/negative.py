"""Section 7 — negative findings, generated rather than written (FR-81, doc 3 §10).

The section that separates a forensic report from marketing output. It says what the tool
could **not** do: bytes it could not explain, sectors it could not read, evidence it was
never allowed to parse, footage whose time it could not establish, and limitations that
follow from how the evidence arrived.

## Why it is generated

The examiner may add to this section and cannot remove from it. That is not enforced by a
permission check — it is enforced by construction. There is no table of suppressed
findings and no "dismiss" path anywhere in this module: every call re-derives the whole
list from the case database, so a finding disappears only when the underlying fact
changes. Delete a row and it comes back on the next run.

The tool knows what it failed to read; the examiner may not. A report listing only
successes is not a forensic report, and the defence expert reads this section first.

## What it does not do

It does not grade the case, and it does not soften. Severity here describes *how much a
reader should worry about the gap*, never how likely a prosecution is to succeed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal

from spectra.core.casestore import CaseStore

Severity = Literal["info", "attention", "serious"]

_SEVERITY_ORDER: dict[Severity, int] = {"serious": 0, "attention": 1, "info": 2}

#: Unexplained fraction above which silence stops being reasonable. A DVR disk is mostly
#: pre-allocated, so a high figure alone is not alarming — it is a prompt to run recovery.
_UNACCOUNTED_ATTENTION = 0.25


@dataclass(frozen=True, slots=True)
class Finding:
    """One thing the tool could not do, with the numbers behind it."""

    code: str
    severity: Severity
    title: str
    detail: str
    evidence_id: str | None = None
    numbers: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "title": self.title,
            "detail": self.detail,
            "evidence_id": self.evidence_id,
            "numbers": dict(sorted(self.numbers.items())),
        }


def _rows(store: CaseStore, sql: str, args: tuple = ()) -> list[dict[str, Any]]:
    cursor = store.conn.execute(sql, args)
    names = [c[0] for c in cursor.description]
    return [dict(zip(names, row, strict=True)) for row in cursor]


def _pct(part: int, whole: int) -> float:
    return round(100.0 * part / whole, 2) if whole else 0.0


def negative_findings(store: CaseStore) -> list[Finding]:
    """Derive every negative finding in the case, deterministically ordered.

    Sorted by severity then code then evidence, so two runs on the same case produce the
    same list in the same order (NFR-08, AC-11).
    """
    out: list[Finding] = []
    out += _coverage_findings(store)
    out += _acquisition_findings(store)
    out += _identification_findings(store)
    out += _recording_findings(store)
    out += _process_findings(store)
    out.sort(key=lambda f: (_SEVERITY_ORDER[f.severity], f.code, f.evidence_id or ""))
    return out


# --- what the bytes say ----------------------------------------------------------------


def _coverage_findings(store: CaseStore) -> list[Finding]:
    out: list[Finding] = []
    totals = _rows(
        store,
        "SELECT evidence_id, bucket, SUM(length) AS bytes FROM coverage"
        " GROUP BY evidence_id, bucket",
    )
    by_evidence: dict[str, dict[str, int]] = {}
    for row in totals:
        by_evidence.setdefault(row["evidence_id"], {})[row["bucket"]] = row["bytes"] or 0

    for evidence_id, buckets in sorted(by_evidence.items()):
        total = sum(buckets.values())
        unaccounted = buckets.get("unaccounted", 0)
        unreadable = buckets.get("unreadable", 0)

        if unaccounted:
            share = unaccounted / total if total else 0.0
            out.append(
                Finding(
                    code="NF-COVERAGE-UNACCOUNTED",
                    severity="attention" if share >= _UNACCOUNTED_ATTENTION else "info",
                    title="Bytes the tool could not explain",
                    detail=(
                        f"{unaccounted:,} of {total:,} bytes ({_pct(unaccounted, total)}%) "
                        "are accounted for by no parsed recording, no carved run, no known "
                        "structure and no read error. Unexplained space of high entropy may "
                        "hold recordings that were not recovered; running further recovery "
                        "tiers is the first response."
                    ),
                    evidence_id=evidence_id,
                    numbers={
                        "unaccounted_bytes": unaccounted,
                        "image_bytes": total,
                        "percent": _pct(unaccounted, total),
                    },
                )
            )

        if unreadable:
            out.append(
                Finding(
                    code="NF-COVERAGE-UNREADABLE",
                    severity="serious",
                    title="Sectors that could not be read",
                    detail=(
                        f"{unreadable:,} bytes ({_pct(unreadable, total)}%) failed to read "
                        "and were zero-filled with the gap recorded. Any recording crossing "
                        "these ranges is incomplete, and footage held there is unrecoverable "
                        "by software alone."
                    ),
                    evidence_id=evidence_id,
                    numbers={"unreadable_bytes": unreadable, "percent": _pct(unreadable, total)},
                )
            )
    return out


# --- how the evidence arrived ----------------------------------------------------------


_PROVENANCE_LIMIT = {
    "B": (
        "attention",
        "Physical image taken without a hardware write blocker. Write protection was by "
        "another method, recorded with the operator's justification.",
    ),
    "C": (
        "attention",
        "Live logical acquisition from a running recorder. The source could not be "
        "independently verified at acquisition time, and completeness cannot be asserted.",
    ),
    "D": (
        "serious",
        "Third-party export files. The original storage was never examined, so the "
        "completeness of this export cannot be verified and footage may exist on the "
        "source device that was not exported. Seizing and imaging the recorder is "
        "recommended.",
    ),
}


def _acquisition_findings(store: CaseStore) -> list[Finding]:
    out: list[Finding] = []
    for row in _rows(
        store,
        "SELECT id, provenance_class, gaps_json, notes, source_format"
        " FROM evidence ORDER BY id",
    ):
        cls = row["provenance_class"]
        if cls in _PROVENANCE_LIMIT:
            severity, detail = _PROVENANCE_LIMIT[cls]
            out.append(
                Finding(
                    code=f"NF-PROVENANCE-{cls}",
                    severity=severity,  # type: ignore[arg-type]
                    title=f"Provenance class {cls} limits what can be claimed",
                    detail=detail,
                    evidence_id=row["id"],
                    numbers={"provenance_class": cls},
                )
            )

        gaps = json.loads(row["gaps_json"] or "[]")
        if gaps:
            span = sum(length for _, length in gaps)
            out.append(
                Finding(
                    code="NF-BAD-SECTORS",
                    severity="serious",
                    title="Read errors during examination",
                    detail=(
                        f"{len(gaps)} unreadable range(s) totalling {span:,} bytes were "
                        "encountered and recorded. The bad-sector map is part of this report."
                    ),
                    evidence_id=row["id"],
                    numbers={"ranges": len(gaps), "bytes": span},
                )
            )

        note = (row["notes"] or "").strip()
        if "MISMATCH" in note.upper():
            out.append(
                Finding(
                    code="NF-EMBEDDED-HASH",
                    severity="serious",
                    title="Stored image hash did not match the media",
                    detail=(
                        "The hash stored inside the acquired image does not match the media "
                        f"read back from it. Recorded verbatim: {note}"
                    ),
                    evidence_id=row["id"],
                )
            )
    return out


# --- what we could and could not parse -------------------------------------------------


def _identification_findings(store: CaseStore) -> list[Finding]:
    out: list[Finding] = []
    known = {r["id"] for r in _rows(store, "SELECT id FROM evidence")}
    identified = {r["evidence_id"] for r in _rows(store, "SELECT evidence_id FROM identification")}

    for evidence_id in sorted(known - identified):
        out.append(
            Finding(
                code="NF-NOT-IDENTIFIED",
                severity="attention",
                title="Evidence was never identified",
                detail=(
                    "No identification was run against this item, so no format family was "
                    "determined and nothing was parsed or recovered from it."
                ),
                evidence_id=evidence_id,
            )
        )

    for row in _rows(
        store,
        "SELECT evidence_id, family, layout_version, parse_supported, status, confidence"
        " FROM identification ORDER BY evidence_id",
    ):
        if row["status"] == "unknown" or row["family"] is None:
            out.append(
                Finding(
                    code="NF-UNKNOWN-FORMAT",
                    severity="attention",
                    title="Format family not recognised",
                    detail=(
                        "No registered plugin claimed this evidence. Only signature carving "
                        "applies; an unrecognised format is not necessarily an empty disk."
                    ),
                    evidence_id=row["evidence_id"],
                )
            )
        elif not row["parse_supported"]:
            out.append(
                Finding(
                    code="NF-CARVE-ONLY",
                    severity="attention",
                    title="Supported at carve level only",
                    detail=(
                        f"The {row['family']} family was recognised but this on-disk layout "
                        "is not one the plugin can walk, so no index was read. Recordings "
                        "come from signature carving alone and carry no index metadata "
                        "(known limitation L2)."
                    ),
                    evidence_id=row["evidence_id"],
                    numbers={"family": row["family"], "confidence": row["confidence"]},
                )
            )
        if row["status"] == "ambiguous":
            out.append(
                Finding(
                    code="NF-AMBIGUOUS",
                    severity="attention",
                    title="More than one format family matched",
                    detail=(
                        "Several plugins claimed this evidence. Nothing was auto-selected; "
                        "the family used was chosen by the examiner and that choice is in "
                        "the audit log."
                    ),
                    evidence_id=row["evidence_id"],
                )
            )
    return out


# --- what the recordings do and do not carry -------------------------------------------


def _recording_findings(store: CaseStore) -> list[Finding]:
    out: list[Finding] = []
    for row in _rows(
        store,
        "SELECT evidence_id,"
        " SUM(CASE WHEN t_local_start IS NULL THEN 1 ELSE 0 END) AS no_time,"
        " SUM(CASE WHEN channel IS NULL THEN 1 ELSE 0 END) AS no_channel,"
        " SUM(CASE WHEN t_method = 'none' OR t_method IS NULL THEN 1 ELSE 0 END) AS no_offset,"
        " COUNT(*) AS total"
        " FROM recording GROUP BY evidence_id ORDER BY evidence_id",
    ):
        total = row["total"] or 0
        if row["no_time"]:
            out.append(
                Finding(
                    code="NF-TIME-UNKNOWN",
                    severity="attention",
                    title="Recordings with no recoverable timestamp",
                    detail=(
                        f"{row['no_time']} of {total} recordings carry no timestamp from the "
                        "container. They are reported in physical order on the image and no "
                        "time has been inferred for them (FR-45)."
                    ),
                    evidence_id=row["evidence_id"],
                    numbers={"without_time": row["no_time"], "recordings": total},
                )
            )
        if row["no_channel"]:
            out.append(
                Finding(
                    code="NF-CHANNEL-UNKNOWN",
                    severity="info",
                    title="Recordings with no recoverable channel",
                    detail=(
                        f"{row['no_channel']} of {total} recordings could not be attributed "
                        "to a camera. Carved fragments of some formats legitimately carry no "
                        "channel, and none has been guessed."
                    ),
                    evidence_id=row["evidence_id"],
                    numbers={"without_channel": row["no_channel"], "recordings": total},
                )
            )
        if total and row["no_offset"] == total:
            out.append(
                Finding(
                    code="NF-NO-CLOCK-OFFSET",
                    severity="serious",
                    title="Absolute time not established",
                    detail=(
                        "No clock offset was established for this evidence, so every time in "
                        "this report is the recorder's own wall clock and may be wrong by an "
                        "unknown amount. No absolute time is asserted anywhere (FR-53). "
                        "Establishing an offset requires evidence recorded at seizure."
                    ),
                    evidence_id=row["evidence_id"],
                    numbers={"recordings": total},
                )
            )
    return out


# --- what was never run ----------------------------------------------------------------


def _process_findings(store: CaseStore) -> list[Finding]:
    out: list[Finding] = []
    with_recordings = {
        r["evidence_id"] for r in _rows(store, "SELECT DISTINCT evidence_id FROM recording")
    }
    with_coverage = {
        r["evidence_id"] for r in _rows(store, "SELECT DISTINCT evidence_id FROM coverage")
    }
    for row in _rows(store, "SELECT id FROM evidence ORDER BY id"):
        evidence_id = row["id"]
        if evidence_id in with_recordings and evidence_id not in with_coverage:
            out.append(
                Finding(
                    code="NF-NO-RECOVERY",
                    severity="attention",
                    title="Recovery was not run",
                    detail=(
                        "This evidence was parsed but no recovery tier was run, so deleted "
                        "and unindexed footage has not been looked for and no coverage map "
                        "exists. Absence of recovered footage here is not evidence of "
                        "absence."
                    ),
                    evidence_id=evidence_id,
                )
            )
    return out


def summarise(findings: list[Finding]) -> dict[str, Any]:
    """Counts by severity, for the report header and `findings.json`."""
    counts: dict[str, int] = {"serious": 0, "attention": 0, "info": 0}
    for finding in findings:
        counts[finding.severity] += 1
    return {"total": len(findings), "by_severity": counts}
