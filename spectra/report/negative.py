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
from spectra.services import custody as custody_service

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
    out += _custody_findings(store)
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


# --- what the paper chain says ----------------------------------------------------------


#: What a case of each provenance class would normally be accompanied by (FR-74).
#:
#: The expectation follows what actually happened, not what would be nice to have:
#:
#: **A and B** are physical images of a seized recorder. A seizure produces a memo under
#: BNSS s. 103, is recorded audio-visually under s. 105, and rests on an authorisation
#: under s. 94 or s. 185. All three should exist.
#:
#: **C** is a live logical acquisition: data taken from premises, usually without the
#: recorder being carried away. A memo and an authorisation still apply. The s. 105
#: recording is *not* asserted, because whether the obligation attaches turns on whether
#: this was a search — a question the tool cannot answer from the case file, and the wrong
#: place to guess.
#:
#: **D** is files handed over by a third party. Nothing was seized by the team, so only
#: the authorisation to receive and examine them is expected. Class D's real weakness —
#: that the original storage was never examined — is already a separate finding.
#:
#: An unrecognised class expects nothing. The cost of over-asking is not neutral: a
#: section that routinely demands documents a case could not have is a section examiners
#: learn to skim, and rule 11's whole value is that this section gets read.
_EXPECTED_DOCUMENTS: dict[str, tuple[str, ...]] = {
    "A": ("panchnama", "seizure_video", "authorisation"),
    "B": ("panchnama", "seizure_video", "authorisation"),
    "C": ("panchnama", "authorisation"),
    "D": ("authorisation",),
}


def _expected_documents(provenance_class: str) -> tuple[str, ...]:
    """Which attachment kinds a case of this provenance class should normally carry.

    Returns kinds from `services.custody.ATTACHMENT_KINDS`. An empty tuple means the tool
    makes no expectation for that class and raises no finding about missing documents.
    """
    return _EXPECTED_DOCUMENTS.get(provenance_class.strip().upper(), ())


def _custody_findings(store: CaseStore) -> list[Finding]:
    """Gaps in the chain that happened before and around SPECTRA (FR-73, FR-74).

    Everything here is about the paper trail, not the bytes. It is in the negative-findings
    section for the same reason the coverage map is: the examiner may not notice an absent
    document, and the defence expert certainly will.
    """
    out: list[Finding] = []
    evidence = _rows(
        store, "SELECT id, provenance_class, label FROM evidence ORDER BY id"
    )
    transfers = _rows(store, "SELECT * FROM custody ORDER BY seq")
    by_evidence: dict[str, list[dict[str, Any]]] = {}
    for row in transfers:
        by_evidence.setdefault(str(row["evidence_id"]), []).append(row)

    attachments = _rows(store, "SELECT evidence_id, kind FROM attachment ORDER BY id")
    kinds_for: dict[str, set[str]] = {}
    for row in attachments:
        # An attachment with no evidence_id belongs to the case as a whole, so it counts
        # for every item: one authorisation commonly covers a whole seizure.
        key = str(row["evidence_id"]) if row["evidence_id"] else "*"
        kinds_for.setdefault(key, set()).add(str(row["kind"]))
    case_wide = kinds_for.get("*", set())

    for row in evidence:
        evidence_id = str(row["id"])
        entries = by_evidence.get(evidence_id, [])

        if not entries:
            out.append(
                Finding(
                    code="NF-NO-CUSTODY",
                    severity="serious",
                    title="No chain-of-custody record",
                    detail=(
                        "No physical custody transfer has been recorded for this item, so "
                        "the case cannot show who held it between seizure and examination. "
                        "The analysis below is unaffected, but the chain of custody is not "
                        "established by this case file (FR-73, SOP Form F-2)."
                    ),
                    evidence_id=evidence_id,
                )
            )
        else:
            broken = [e for e in entries if e["seal_intact"] == 0]
            if broken:
                out.append(
                    Finding(
                        code="NF-SEAL-BROKEN",
                        severity="serious",
                        title="A seal was recorded as not intact",
                        detail=(
                            "A custody transfer records the seal as broken on receipt. "
                            "Everything after that transfer rests on a container that was "
                            "open to interference, whatever the cause."
                        ),
                        evidence_id=evidence_id,
                        numbers={"transfers": len(entries), "broken_seals": len(broken)},
                    )
                )
            unsealed = [e for e in entries if e["seal_intact"] is None]
            if unsealed:
                out.append(
                    Finding(
                        code="NF-NO-SEAL",
                        severity="attention",
                        title="Custody transfers with no seal recorded",
                        detail=(
                            "One or more transfers record no seal. An unsealed transfer is "
                            "not necessarily improper, but it cannot be shown to have "
                            "preserved the item, so it is reported rather than assumed."
                        ),
                        evidence_id=evidence_id,
                        numbers={"transfers": len(entries), "without_seal": len(unsealed)},
                    )
                )
            breaks = custody_service.chain_breaks(store, evidence_id)
            if breaks:
                out.append(
                    Finding(
                        code="NF-CUSTODY-GAP",
                        severity="serious",
                        title="The custody chain does not join up",
                        detail=(
                            "A transfer is released by someone other than the person the "
                            "previous transfer left the item with, so at least one movement "
                            "is unrecorded: " + "; ".join(breaks)
                        ),
                        evidence_id=evidence_id,
                        numbers={"gaps": len(breaks)},
                    )
                )

        held = kinds_for.get(evidence_id, set()) | case_wide
        expected = _expected_documents(str(row["provenance_class"] or "")) or ()
        missing = sorted(set(expected) - held)
        if missing:
            out.append(
                Finding(
                    code="NF-MISSING-DOCUMENTS",
                    severity="attention",
                    title="Expected case documents are not attached",
                    detail=(
                        "A case of provenance class "
                        f"{row['provenance_class'] or 'unknown'} would normally be "
                        "accompanied by: " + ", ".join(missing) + ". They are not in this "
                        "case file. They may exist on paper; this section reports only "
                        "what the case can show (FR-74)."
                    ),
                    evidence_id=evidence_id,
                    numbers={"missing": len(missing)},
                )
            )
    return out


def summarise(findings: list[Finding]) -> dict[str, Any]:
    """Counts by severity, for the report header and `findings.json`."""
    counts: dict[str, int] = {"serious": 0, "attention": 0, "info": 0}
    for finding in findings:
        counts[finding.severity] += 1
    return {"total": len(findings), "by_severity": counts}
