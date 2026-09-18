"""CustodyService — physical custody transfers and scene documents (FR-73, FR-74).

The part of the chain that happens before SPECTRA ever sees a disk, and the part that
continues after. Two operations, one purpose: make the paper chain checkable.

## Custody (FR-73, Form F-2)

A transfer is a row: who released, who received, when, why, and — the part that makes the
row mean anything — the seal number and whether the seal was intact on receipt. SOP-5 puts
it plainly: *a gap in this form is a gap in the chain*.

`seal_intact` is deliberately three-valued. `NULL` means the item was never sealed;
`0` means it was sealed and found broken. Collapsing those into one flag would turn a
serious finding into an unremarkable one.

## Attachments (FR-74, BNSS s. 105)

BNSS 2023 s. 105 makes audio-video recording of search and seizure mandatory, so a case
normally arrives with a recording, a panchnama, and an authorisation. SPECTRA hashes each
on the way in and never modifies them; from then on their integrity is checkable the same
way the evidence image's is.

They are stored apart from `artifact` on purpose. That table is what the tool *produced*;
an officer's panchnama listed there would be presented as tool output, and a s. 63(4)
tender that included it would be claiming to vouch for a document SPECTRA never made.
The bytes still go in the same content-addressed store, so nothing is duplicated.

Attachments are not evidence and are never parsed. They are read once, hashed, copied, and
thereafter only ever re-hashed.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from spectra.core.casestore import CaseStore
from spectra.services import ServiceError
from spectra.services.evidence import evidence_row

#: The document kinds a case is normally assembled from. `other` exists because a real
#: case will arrive with something this list did not anticipate, and refusing it would
#: push the examiner to keep it outside the case entirely.
ATTACHMENT_KINDS: tuple[str, ...] = (
    "panchnama",          # seizure memo signed by witnesses
    "seizure_video",      # BNSS s. 105 audio-video recording of the search and seizure
    "authorisation",      # search/seizure authorisation, court order, requisition
    "seizure_form",       # SOP Form F-1
    "custody_form",       # SOP Form F-2
    "acquisition_form",   # SOP Form F-3
    "photograph",         # scene and device photographs, incl. rear cabling
    "correspondence",
    "other",
)

#: The statutory hook each kind hangs off, printed in the report so a reader does not have
#: to know the SOP to see why a document is in the case.
_STATUTORY_REF: dict[str, str] = {
    "panchnama": "BNSS 2023 s. 103 (search and seizure memo)",
    "seizure_video": "BNSS 2023 s. 105 (mandatory audio-video recording of seizure)",
    "authorisation": "BNSS 2023 s. 94 / s. 185 (authorisation to search and seize)",
}


@dataclass(frozen=True, slots=True)
class CustodyEntry:
    seq: int
    evidence_id: str
    ts_utc: str
    from_holder: str
    to_holder: str
    purpose: str
    seal_number: str
    seal_intact: bool | None
    signature_ref: str
    note: str

    def to_json(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "evidence_id": self.evidence_id,
            "ts_utc": self.ts_utc,
            "from_holder": self.from_holder,
            "to_holder": self.to_holder,
            "purpose": self.purpose,
            "seal_number": self.seal_number,
            "seal_intact": self.seal_intact,
            "signature_ref": self.signature_ref,
            "note": self.note,
        }


@dataclass(frozen=True, slots=True)
class AttachmentRef:
    id: str
    kind: str
    description: str
    sha256: str
    md5: str
    size_bytes: int
    filename: str
    statutory_ref: str

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "description": self.description,
            "sha256": self.sha256,
            "md5": self.md5,
            "size_bytes": self.size_bytes,
            "filename": self.filename,
            "statutory_ref": self.statutory_ref,
        }


def record_transfer(
    store: CaseStore,
    evidence_id: str,
    *,
    from_holder: str,
    to_holder: str,
    purpose: str,
    seal_number: str = "",
    seal_intact: bool | None = None,
    signature_ref: str = "",
    note: str = "",
) -> CustodyEntry:
    """Record one physical custody transfer (FR-73, Form F-2).

    The timestamp comes from this operation's own audit record, so the custody row and the
    audit chain cannot disagree about when it was entered. That is not the same as when
    the transfer happened — a transfer entered late is recorded as entered late, and
    `note` is where the examiner says so.
    """
    evidence_row(store, evidence_id)
    if not from_holder.strip() or not to_holder.strip():
        raise ServiceError(
            "a custody transfer needs both holders named — 'a gap in this form is a gap "
            "in the chain' (SOP Form F-2)"
        )
    if not purpose.strip():
        raise ServiceError("a custody transfer needs a stated purpose")

    with store.audit.operation(
        "custody.transfer",
        target=evidence_id,
        params={
            "from": from_holder, "to": to_holder, "purpose": purpose,
            "seal_number": seal_number, "seal_intact": seal_intact,
        },
    ) as op:
        assert op.start_record is not None
        ts = op.start_record.ts_utc
        with store.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO custody (evidence_id, ts_utc, from_holder, to_holder, purpose,"
                " signature_ref, seal_number, seal_intact, note)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (evidence_id, ts, from_holder, to_holder, purpose, signature_ref,
                 seal_number, None if seal_intact is None else int(seal_intact), note),
            )
            seq = int(cur.lastrowid or 0)
        op.result_params = {"seq": seq}
    store.write_manifest()
    return CustodyEntry(
        seq, evidence_id, ts, from_holder, to_holder, purpose,
        seal_number, seal_intact, signature_ref, note,
    )


def attach_document(
    store: CaseStore,
    path: Path,
    kind: str,
    *,
    description: str = "",
    evidence_id: str | None = None,
    provided_by: str = "",
) -> AttachmentRef:
    """Hash an external case document and copy it into the case (FR-74).

    The file is read once and never written to. It lands in the content-addressed store
    alongside everything else the case holds, so `spectra verify` checks it with the same
    walk that checks exported clips.
    """
    if kind not in ATTACHMENT_KINDS:
        raise ServiceError(
            f"{kind!r} is not an attachment kind; use one of {', '.join(ATTACHMENT_KINDS)}"
        )
    if not path.is_file():
        raise ServiceError(f"attachment not found: {path}")
    if evidence_id is not None:
        evidence_row(store, evidence_id)

    with store.audit.operation(
        "custody.attach",
        target=evidence_id,
        params={"kind": kind, "filename": path.name, "provided_by": provided_by},
    ) as op:
        assert op.start_record is not None
        ts = op.start_record.ts_utc
        ref = store.add_artifact_file(path, "attachment", created_utc=ts)
        att_id = store.next_id("ATT", "attachment")
        with store.transaction() as conn:
            conn.execute(
                "INSERT INTO attachment (id, case_id, evidence_id, kind, description,"
                " provided_by, statutory_ref, sha256, md5, size_bytes, filename, attached_utc)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (att_id, store.case_id, evidence_id, kind, description, provided_by,
                 _STATUTORY_REF.get(kind, ""), ref.sha256, ref.md5, ref.size,
                 path.name, ts),
            )
        op.hash_after = ref.sha256
        op.result_params = {"attachment_id": att_id, "sha256": ref.sha256,
                            "size_bytes": ref.size}
    store.write_manifest()
    return AttachmentRef(
        att_id, kind, description, ref.sha256, ref.md5, ref.size, path.name,
        _STATUTORY_REF.get(kind, ""),
    )


# --- reading ----------------------------------------------------------------------------


def custody_entries(store: CaseStore, evidence_id: str | None = None) -> list[CustodyEntry]:
    sql = "SELECT * FROM custody"
    args: tuple = ()
    if evidence_id is not None:
        sql += " WHERE evidence_id = ?"
        args = (evidence_id,)
    sql += " ORDER BY seq"
    cur = store.conn.execute(sql, args)
    cols = [d[0] for d in cur.description]
    out = []
    for raw in cur.fetchall():
        row = dict(zip(cols, raw, strict=True))
        out.append(
            CustodyEntry(
                row["seq"], row["evidence_id"] or "", row["ts_utc"] or "",
                row["from_holder"] or "", row["to_holder"] or "", row["purpose"] or "",
                row["seal_number"] or "",
                None if row["seal_intact"] is None else bool(row["seal_intact"]),
                row["signature_ref"] or "", row["note"] or "",
            )
        )
    return out


def attachments(store: CaseStore, evidence_id: str | None = None) -> list[AttachmentRef]:
    sql = "SELECT * FROM attachment"
    args: tuple = ()
    if evidence_id is not None:
        sql += " WHERE evidence_id = ?"
        args = (evidence_id,)
    sql += " ORDER BY kind, id"
    cur = store.conn.execute(sql, args)
    cols = [d[0] for d in cur.description]
    return [
        AttachmentRef(
            row["id"], row["kind"] or "", row["description"] or "", row["sha256"] or "",
            row["md5"] or "", row["size_bytes"] or 0, row["filename"] or "",
            row["statutory_ref"] or "",
        )
        for row in (dict(zip(cols, raw, strict=True)) for raw in cur.fetchall())
    ]


def chain_breaks(store: CaseStore, evidence_id: str) -> list[str]:
    """Where the custody chain does not join up.

    A chain is continuous when each transfer's releasing holder is the previous
    transfer's receiving holder. A mismatch means the item moved without a row, which is
    the thing Form F-2 exists to make visible.
    """
    entries = custody_entries(store, evidence_id)
    breaks = []
    for previous, current in zip(entries, entries[1:], strict=False):
        if current.from_holder.strip().casefold() != previous.to_holder.strip().casefold():
            breaks.append(
                f"entry {current.seq} is released by {current.from_holder!r} but entry "
                f"{previous.seq} left the item with {previous.to_holder!r}; a transfer "
                "between them is not recorded"
            )
    return breaks
