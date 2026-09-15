"""Evidence ingest — existing images (FR-17) and loose export files (FR-18), hashed on ingest.

Physical acquisition (FR-10..FR-16) is Phase 2 (finals, `acquire/`).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from spectra.core.casestore import CaseStore
from spectra.core.hashing import hash_source
from spectra.core.source import EvidenceSource, open_source
from spectra.services import ServiceError

PROVENANCE_CLASSES = ("A", "B", "C", "D")


@dataclass(frozen=True, slots=True)
class IngestResult:
    evidence_id: str
    md5: str
    sha256: str
    size: int
    gaps: tuple[tuple[int, int], ...]
    embedded_hash_check: dict[str, Any]
    source_format: str = ""
    notes: str = ""


def import_image(
    store: CaseStore, path: Path, provenance_class: str, label: str = "", note: str = ""
) -> IngestResult:
    """Ingest a raw / split-raw / E01 image. The provenance class must be stated by the
    examiner: SPECTRA did not acquire this image and cannot know how it was made (FR-19)."""
    if provenance_class not in PROVENANCE_CLASSES:
        raise ServiceError(f"provenance class must be one of {', '.join(PROVENANCE_CLASSES)}")
    if Path(path).is_dir():
        raise ServiceError("that is a directory; use 'import files' for export file sets")
    return _ingest(store, Path(path), "image", provenance_class, label, note, "import.image")


def import_files(
    store: CaseStore, directory: Path, label: str = "", note: str = ""
) -> IngestResult:
    """Ingest owner-provided export files as one file set — provenance class D (FR-18)."""
    if not Path(directory).is_dir():
        raise ServiceError(f"not a directory: {directory}")
    return _ingest(store, Path(directory), "export_files", "D", label, note, "import.files")


def _ingest(
    store: CaseStore, path: Path, kind: str, provenance: str, label: str, note: str, action: str
) -> IngestResult:
    resolved = path.resolve()
    existing = store.conn.execute(
        "SELECT id FROM evidence WHERE source_path = ?", (str(resolved),)
    ).fetchone()
    if existing:
        raise ServiceError(f"already ingested as {existing[0]}: {resolved}")
    evidence_id = store.next_id("EV", "evidence")
    params = {"evidence_id": evidence_id, "path": str(resolved), "kind": kind,
              "provenance_class": provenance, "label": label}
    with store.audit.operation(action, target=evidence_id, params=params) as op:
        with open_source(resolved) as src:
            digests = hash_source(src)
            gaps = tuple((g.offset, g.length) for g in src.gaps())
            identity = src.identity
        embedded = _embedded_hash_check(identity.embedded_hashes, digests.md5)
        members = [{"path": m.path, "offset": m.extent.offset, "length": m.extent.length}
                   for m in identity.members]
        findings = []
        if kind == "export_files":
            findings.append("Third-party export files: the original storage was not examined "
                            "and completeness cannot be verified (provenance class D, L6).")
        if embedded["status"] == "MISMATCH":
            findings.append(
                f"INTEGRITY: the MD5 stored in the image ({embedded['stored_md5']}) does not match "
                f"the MD5 of its media as read ({embedded['computed_md5']}). The image is corrupt "
                "or altered; corrupt E01 chunks are returned as zeros by libewf and cannot be "
                "located."
            )
        elif embedded["status"] == "none_stored" and identity.source_format == "ewf":
            findings.append("The E01 carries no stored MD5, so corruption of compressed chunks "
                            "(returned as zeros by libewf) would not be detectable.")
        notes = " ".join([*findings, note]).strip()
        with store.transaction() as conn:
            conn.execute(
                "INSERT INTO evidence (id, case_id, kind, provenance_class, label, capacity_bytes,"
                " md5, sha256, notes, source_path, source_format, members_json, gaps_json)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    evidence_id, store.case_id, kind, provenance, label, digests.size,
                    digests.md5, digests.sha256, notes, str(resolved), identity.source_format,
                    json.dumps(members), json.dumps(gaps),
                ),
            )
        op.result_params = {
            "md5": digests.md5, "sha256": digests.sha256, "size": digests.size,
            "source_format": identity.source_format, "member_count": len(members),
            "gaps": [list(g) for g in gaps], "embedded_hash_check": embedded,
        }
        op.hash_after = digests.sha256
    store.write_manifest()
    return IngestResult(evidence_id, digests.md5, digests.sha256, digests.size, gaps, embedded,
                        identity.source_format, notes)


def _embedded_hash_check(embedded: tuple[tuple[str, str], ...], md5: str) -> dict[str, Any]:
    """Compare hashes stored inside an E01 with what we computed over its media."""
    stored = dict(embedded)
    if "md5" not in stored:
        return {"status": "none_stored"}
    return {"status": "match" if stored["md5"] == md5 else "MISMATCH",
            "stored_md5": stored["md5"], "computed_md5": md5}


def evidence_row(store: CaseStore, evidence_id: str) -> dict[str, Any]:
    cursor = store.conn.execute("SELECT * FROM evidence WHERE id = ?", (evidence_id,))
    row = cursor.fetchone()
    if row is None:
        raise ServiceError(f"no such evidence item: {evidence_id}")
    return dict(zip([c[0] for c in cursor.description], row, strict=True))


def default_evidence_id(store: CaseStore, evidence_id: str | None) -> str:
    if evidence_id:
        return evidence_id
    ids = [r[0] for r in store.conn.execute("SELECT id FROM evidence ORDER BY id")]
    if len(ids) == 1:
        return ids[0]
    raise ServiceError("specify --evidence" if ids else "the case has no evidence yet")


def open_evidence(store: CaseStore, evidence_id: str) -> EvidenceSource:
    """Reopen ingested evidence read-only, refusing if its size no longer matches."""
    row = evidence_row(store, evidence_id)
    src = open_source(Path(row["source_path"]))
    if src.size != row["capacity_bytes"]:
        src.close()
        raise ServiceError(
            f"{evidence_id} at {row['source_path']} is now {src.size} bytes but was "
            f"{row['capacity_bytes']} bytes at ingest — the evidence has changed or moved"
        )
    return src
