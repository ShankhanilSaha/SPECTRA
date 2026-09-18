"""ReportService — `findings.json`, the s. 63(4) certificate, and the hash enclosure.

Follows the standard service shape: validate → audit `*.start` → work → audit
`*.complete` → rewrite the manifest (doc 3 §2).

## Why the certificate re-hashes instead of copying stored digests

The case database records MD5 and SHA-256 for every artefact at the moment it was written.
The certificate could simply reprint those. It does not, for two reasons.

**A re-hash proves the file being handed over.** A digest recorded three weeks ago
attests to a file that existed three weeks ago. The certificate is tendered *with* the
artefacts, and its whole purpose is to let the court bind the two together, so the digests
on it should be measured from the files in the bundle.

**It is the only integrity check that costs nothing extra.** Artefacts are clips and
reports, not multi-TB images, and `Hasher` computes all three digests in one pass over
bytes we have to read anyway.

The evidence image is the exception: it is too large to re-hash for a form, so the
certificate reprints the stored MD5 and SHA-256 and leaves the SHA-1 box blank. The
Schedule lists algorithms as checkboxes, so ticking two of three is a complete answer —
and a blank box is honest where an invented digest would not be.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from spectra.core.casestore import CaseStore
from spectra.core.hashing import hash_case_file
from spectra.report import certificate as cert
from spectra.report import findings as fnd
from spectra.report import generator as gen
from spectra.services import ServiceError
from spectra.services.evidence import evidence_row

#: Artefact kinds that form part of what is tendered to the court. A log or an
#: intermediate is kept for audit but is not what the certificate speaks to.
TENDERABLE_KINDS: tuple[str, ...] = ("evidence_copy", "derivative", "findings", "report")


def generate_findings(
    store: CaseStore, *, tool_versions: dict[str, str] | None = None
) -> tuple[dict[str, Any], str]:
    """Build and store `findings.json`. Returns the document and its digest.

    `generated_utc` comes from this operation's own audit record, never from the clock,
    so re-running on an unchanged case yields byte-identical output (AC-11).
    """
    with store.audit.operation("report.findings") as op:
        assert op.start_record is not None
        generated = op.start_record.ts_utc
        doc = fnd.build(store, generated_utc=generated, tool_versions=tool_versions)
        digest = fnd.digest(doc)
        with store.new_artifact("findings", created_utc=generated,
                                source_record_seq=op.start_record.seq) as writer:
            writer.write(fnd.serialise(doc))
        op.hash_after = digest
        op.result_params = {
            "findings_digest": digest,
            "conclusions_digest": doc["conclusions_digest"],
            "negative_findings": doc["negative_summary"]["total"],
        }
    store.write_manifest()
    return doc, digest



def generate_report(
    store: CaseStore,
    *,
    out_dir: Path,
    branding: gen.Branding | None = None,
    tool_version: str = "",
    pdf: bool = True,
) -> dict[str, Any]:
    """Build findings, render the twelve-section report, and store both (FR-80, AC-09).

    Returns what was written and what was not. `pdf=True` asks for a PDF; if WeasyPrint is
    absent the HTML is still produced and `pdf_error` says why the PDF is missing, because
    a forensic tool may not hand over a lesser artefact than the one requested (rule 7).
    """
    doc, digest = generate_findings(store, tool_versions={"spectra": tool_version}
                                    if tool_version else None)
    out_dir.mkdir(parents=True, exist_ok=True)

    with store.audit.operation("report.generate", params={"findings_digest": digest}) as op:
        assert op.start_record is not None
        html_text = gen.render_html(doc, digest, branding=branding,
                                    tool_version=tool_version)
        html_path = out_dir / f"report-{store.case_id}.html"
        html_path.write_text(html_text, encoding="utf-8")
        ref = store.add_artifact_file(html_path, "report",
                                      created_utc=op.start_record.ts_utc,
                                      source_record_seq=op.start_record.seq)

        pdf_path: Path | None = None
        pdf_error = ""
        if pdf:
            try:
                pdf_path = gen.render_pdf(html_text, out_dir / f"report-{store.case_id}.pdf")
                store.add_artifact_file(pdf_path, "report",
                                        created_utc=op.start_record.ts_utc,
                                        source_record_seq=op.start_record.seq)
            except gen.PdfUnavailable as exc:
                pdf_error = str(exc)

        op.hash_after = ref.sha256
        op.result_params = {
            "findings_digest": digest,
            "conclusions_digest": doc["conclusions_digest"],
            "report_sha256": ref.sha256,
            "pdf_written": pdf_path is not None,
            "pdf_error": pdf_error,
            "negative_findings": doc["negative_summary"]["total"],
        }
    store.write_manifest()
    return {
        "findings_digest": digest,
        "conclusions_digest": doc["conclusions_digest"],
        "html": html_path,
        "html_sha256": ref.sha256,
        "pdf": pdf_path,
        "pdf_error": pdf_error,
        "negative_summary": doc["negative_summary"],
        "audit_ok": doc["integrity"]["audit_ok"],
    }


def _stored_row(store: CaseStore, sha256: str) -> dict[str, Any] | None:
    cur = store.conn.execute(
        "SELECT sha256, md5, path, kind, size_bytes FROM artifact WHERE sha256 = ?",
        (sha256,),
    )
    row = cur.fetchone()
    if row is None:
        return None
    return dict(zip([d[0] for d in cur.description], row, strict=True))


class DigestMismatch(ServiceError):
    """An artefact on disk no longer hashes to the digest the case recorded for it."""


def _on_digest_mismatch(
    row: dict[str, Any], measured_sha256: str, measured_size: int
) -> dict[str, Any] | None:
    """Policy for an artefact whose re-hash disagrees with the case record.

    Called once per mismatched artefact while assembling the hash enclosure. Returning a
    row includes that artefact in the tender; returning `None` leaves it out; raising
    `DigestMismatch` abandons the certificate entirely.

    **The artefact is withheld from the tender and the withholding is disclosed on the
    form.** Three options were available and two are wrong for this tool:

    *Raising* would abandon the certificate over one bad file, blocking artefacts that are
    perfectly intact. The examiner's way out would be to edit the case and retry — exactly
    the audit-trail hole rule 5 exists to close.

    *Tendering it with the measured digest* would produce a form that looks complete while
    carrying a digest contradicting the case record, with no field admitting it. That is
    the confident wrong answer doc 6 §1 calls the only disqualifying failure.

    *Returning `None`* — plain exclusion — is safe for the tender but leaves the enclosure
    silently shorter than the case, and an undisclosed omission is what a defence expert
    looks for first (doc 3 §14 threat (c)).

    So the row is emitted with its **hash columns empty** and a note recording both
    digests. A reader scanning the SHA-256 column must never find a value there for
    something absent from the bundle, and `kind` is prefixed so withheld items sort
    together instead of hiding among tendered ones.

    This function cannot tell bit rot from tampering, and it does not try: it reports what
    it measured and leaves the inference to the examiner, who has the custody record.
    """
    return {
        "path": row["path"] or row["sha256"],
        "kind": f"WITHHELD - {row['kind']}",
        "size_bytes": measured_size,
        "md5": "",
        "sha1": "",
        "sha256": "",
        "note": (
            "WITHHELD FROM TENDER: this artefact no longer matches the digest recorded "
            f"when it was created. Recorded SHA-256 {row['sha256']}; the file now present "
            f"hashes to {measured_sha256} at {measured_size} bytes. It is listed here "
            "because it forms part of the case record, and excluded from the tender "
            "because its integrity cannot be asserted. The cause (media error or "
            "alteration) is not determined by the tool."
        ),
    }


def tender_rows(store: CaseStore) -> tuple[list[dict[str, Any]], list[str]]:
    """Re-hash every tenderable artefact. Returns (rows for the enclosure, warnings).

    Each artefact is read from the content-addressed store and hashed afresh. A file whose
    digest still matches contributes a row with all three algorithms; a file that does not
    match is handed to `_on_digest_mismatch`, and a file that has gone missing is recorded
    as missing rather than skipped.
    """
    rows: list[dict[str, Any]] = []
    warnings: list[str] = []
    cur = store.conn.execute(
        "SELECT sha256, md5, path, kind, size_bytes FROM artifact "
        "WHERE kind IN (" + ",".join("?" * len(TENDERABLE_KINDS)) + ") "
        "ORDER BY kind, path, sha256",
        TENDERABLE_KINDS,
    )
    cols = [d[0] for d in cur.description]
    for raw in cur.fetchall():
        row = dict(zip(cols, raw, strict=True))
        path = store.artifact_path(row["sha256"])
        if not path.exists():
            warnings.append(
                f"artefact {row['sha256'][:12]} ({row['kind']}) is recorded in the case "
                "but its file is absent from the artefact store; it cannot be tendered"
            )
            # Disclosed, not dropped — for the same reason as a digest mismatch.
            rows.append(
                {
                    "path": row["path"] or row["sha256"],
                    "kind": f"WITHHELD - {row['kind']}",
                    "size_bytes": row["size_bytes"],
                    "md5": "",
                    "sha1": "",
                    "sha256": "",
                    "note": (
                        "WITHHELD FROM TENDER: recorded in the case with SHA-256 "
                        f"{row['sha256']} but no corresponding file is present in the "
                        "artefact store, so no digest can be asserted for it."
                    ),
                }
            )
            continue
        measured = hash_case_file(path)
        if measured.sha256 != row["sha256"]:
            warnings.append(
                f"artefact {row['sha256'][:12]} ({row['kind']}) re-hashes to "
                f"{measured.sha256[:12]}; the file has changed since it was recorded"
            )
            kept = _on_digest_mismatch(row, measured.sha256, measured.size)
            if kept is not None:
                rows.append(kept)
            continue
        rows.append(
            {
                "path": row["path"] or row["sha256"],
                "kind": row["kind"],
                "size_bytes": measured.size,
                "md5": measured.md5,
                "sha1": measured.sha1,
                "sha256": measured.sha256,
            }
        )
    return rows, warnings


def _evidence_row_for_certificate(store: CaseStore, evidence_id: str) -> dict[str, Any]:
    """The evidence image's own line in the enclosure, from stored digests.

    No SHA-1: it was never computed for this image and a multi-TB re-hash to fill one
    checkbox is not a reason to make the examiner wait. The blank is the honest answer.
    """
    row = evidence_row(store, evidence_id)
    return {
        "path": row.get("source_path") or evidence_id,
        "kind": "evidence_image",
        "size_bytes": row.get("capacity_bytes"),
        "md5": row.get("md5") or "",
        "sha1": "",
        "sha256": row.get("sha256") or "",
    }


def generate_certificate(
    store: CaseStore,
    evidence_id: str,
    *,
    instance: int = 1,
    instituted_on: datetime | None = None,
    device: cert.DeviceParticulars | None = None,
    control_modes: tuple[str, ...] = (),
    record_description: str = "",
) -> tuple[cert.Certificate, list[dict[str, Any]], list[str]]:
    """Pre-fill the s. 63(4) certificate for one instance of submission (FR-83).

    Returns the certificate, the hash-report rows it encloses, and any warnings raised
    while re-hashing. Signatories are left blank — see `report/certificate.py`.
    """
    row = evidence_row(store, evidence_id)
    if instance < 1:
        raise ServiceError("instance numbering starts at 1 — one per submission")

    statute = cert.statute_for(instituted_on)
    with store.audit.operation(
        "report.certificate",
        target=evidence_id,
        params={"instance": instance, "statute": statute},
    ) as op:
        assert op.start_record is not None
        rows, warnings = tender_rows(store)
        rows = [_evidence_row_for_certificate(store, evidence_id), *rows]
        enclosure = cert.hash_report_rows(rows)

        particulars = device or cert.DeviceParticulars(
            source_type="DVR",
            make_and_model=" ".join(
                p for p in (row.get("device_make"), row.get("device_model")) if p
            ),
            serial_number=row.get("device_serial") or "",
            other_information=_disk_note(row),
        )
        certificate = cert.Certificate(
            case_id=store.case_id,
            instance=instance,
            statute=statute,
            device=particulars,
            hashes=cert.HashBlock(
                md5=row.get("md5") or "",
                sha256=row.get("sha256") or "",
            ),
            record_description=record_description or _default_description(store, evidence_id),
            hash_report_ref=_enclosure_ref(instance, enclosure),
            control_modes=control_modes,
            generated_utc=op.start_record.ts_utc,
        )
        op.result_params = {
            "statute": statute,
            "enclosure_items": len(enclosure),
            "tendered_items": len(cert.tendered(enclosure)),
            "withheld_items": len(cert.withheld(enclosure)),
            # The warnings themselves, not a count. A count records that something was
            # wrong without recording what, which is no use to anyone reading the chain
            # later — and this is the only permanent record of it.
            "warnings": warnings,
            "unsigned_parts": list(certificate.unsigned),
        }
    store.write_manifest()
    return certificate, enclosure, warnings


def _enclosure_ref(instance: int, enclosure: list[dict[str, Any]]) -> str:
    """The enclosure reference printed on the form.

    It states the withheld count on the certificate face, so a reader learns that the
    enclosure is incomplete from the form itself rather than by counting its rows.
    """
    held = len(cert.withheld(enclosure))
    ref = f"Annexure H-{instance} (enclosed): {len(cert.tendered(enclosure))} item(s) tendered"
    if held:
        ref += f"; {held} item(s) listed as WITHHELD with reasons stated"
    return ref


def _disk_note(row: dict[str, Any]) -> str:
    """Disk identity belongs on the form: the record came off the disk, not the chassis."""
    bits = [
        f"{label}: {row[key]}"
        for label, key in (
            ("Disk make", "disk_make"),
            ("Disk model", "disk_model"),
            ("Disk serial", "disk_serial"),
            ("Capacity (bytes)", "capacity_bytes"),
            ("Provenance class", "provenance_class"),
        )
        if row.get(key)
    ]
    return "; ".join(bits)


def _default_description(store: CaseStore, evidence_id: str) -> str:
    """Describe what is being tendered in the words the case can support."""
    counts = store.conn.execute(
        "SELECT COUNT(*), COUNT(DISTINCT channel) FROM recording WHERE evidence_id = ?",
        (evidence_id,),
    ).fetchone()
    total, channels = counts[0] or 0, counts[1] or 0
    if total == 0:
        return (
            f"Forensic image of evidence item {evidence_id} and the analysis artefacts "
            "listed in the enclosed hash report. No recordings were enumerated."
        )
    return (
        f"{total} recording(s) across {channels} channel(s) extracted from the forensic "
        f"image of evidence item {evidence_id}, together with the analysis artefacts "
        "listed in the enclosed hash report."
    )


def write_certificate_files(
    store: CaseStore,
    certificate: cert.Certificate,
    enclosure: list[dict[str, Any]],
    out_dir: Path,
) -> list[Path]:
    """Write the certificate and its enclosure beside each other, since they travel together."""
    out_dir.mkdir(parents=True, exist_ok=True)
    import json

    base = f"certificate-{certificate.case_id}-{certificate.instance:02d}"
    cert_path = out_dir / f"{base}.json"
    encl_path = out_dir / f"{base}-hash-report.json"
    cert_path.write_text(
        json.dumps(certificate.to_json(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    encl_path.write_text(
        json.dumps(enclosure, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return [cert_path, encl_path]
