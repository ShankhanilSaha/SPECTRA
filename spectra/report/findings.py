"""`findings.json` — the report's data spine (FR-86, AC-11, NFR-08, doc 3 §10).

Everything a report says comes from here. The HTML and PDF are renderings of this
document; they add layout and add no facts. That has two consequences worth stating.

**A third party can check our conclusions without our renderer.** `findings.json` ships
beside the PDF, so a defence expert can diff two runs, or diff our numbers against their
own tool, without trusting our template.

**Determinism is a property of this file, not of the PDF.** AC-11 says two runs on the
same image produce identical findings JSON. Three rules make that true, and all three are
easy to break by accident:

1. **Every query is totally ordered.** SQLite's row order without `ORDER BY` is an
   implementation detail — stable enough to pass tests today and different after a
   `VACUUM`. Each `ORDER BY` here ends in a unique column so ties cannot reorder.
2. **No wall-clock reads.** `generated_utc` is passed in by the service, which takes it
   from the audit record for the report operation. `datetime.now()` in this module would
   make byte-identical output impossible by construction (CLAUDE.md rule 13).
3. **One canonicaliser.** `canonical_json` is imported from `core/audit.py` — the same
   function the hash chain uses. A second implementation would be a second chance to
   disagree about float formatting or key order.

The digest of this document is what the report prints and what `spectra verify` re-computes.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from spectra.core.audit import canonical_json
from spectra.core.casestore import SCHEMA_VERSION, CaseStore
from spectra.report.negative import negative_findings, summarise
from spectra.services import custody as custody_service

#: Bumped when the shape of this document changes in a way a consumer would notice.
#: A reader that does not recognise the version should refuse to interpret the contents
#: rather than guess which fields moved.
FINDINGS_VERSION = 1

#: The sections that are a pure function of the evidence and the analysis parameters —
#: what the tool concluded *about the image*.
#:
#: These get their own digest, and that digest is what AC-11 is actually about. The rest
#: of the document is this examination's own history: when it ran, who ran it, how long
#: the audit chain is, which artefacts exist. That history legitimately differs between
#: two examinations of the same disk, and it changes every time the report is regenerated
#: because generating a report is itself an audited event.
#:
#: Without this split the obvious test of the determinism claim — run it twice, compare —
#: fails on a tool that is behaving correctly, and the real guarantee gets lost in the
#: noise. With it, two examiners in different labs can compare one number.
CONCLUSION_KEYS: tuple[str, ...] = (
    "identification",
    "disk_layout",
    "coverage",
    "recordings",
    "time",
    "device_events",
    "negative_findings",
)


def _rows(store: CaseStore, sql: str, args: tuple = ()) -> list[dict[str, Any]]:
    cur = store.conn.execute(sql, args)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]


def _json_col(raw: str | None, default: Any) -> Any:
    """Decode a `*_json` column. A malformed one is reported, never silently dropped."""
    if raw is None or raw == "":
        return default
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return {"unparsable_json": raw[:200]}


def build(
    store: CaseStore,
    *,
    generated_utc: str,
    tool_versions: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Assemble the whole findings document.

    `generated_utc` must come from an audit record, not the clock — see the module
    docstring. The caller is the report service, which has the record to hand.
    """
    head_seq, head_digest = store.audit.head()
    chain = store.audit.verify()
    findings = negative_findings(store)

    doc: dict[str, Any] = {
        "findings_version": FINDINGS_VERSION,
        "schema_version": SCHEMA_VERSION,
        "generated_utc": generated_utc,
        "tool_versions": dict(sorted((tool_versions or {}).items())),
        "case": _case(store),
        "evidence": _evidence(store),
        "identification": _identification(store),
        "disk_layout": _disk_layout(store),
        "coverage": _coverage(store),
        "recordings": _recordings(store),
        "time": _time(store),
        "device_events": _device_events(store),
        "annotations": _annotations(store),
        "artifacts": _artifacts(store),
        "custody": _custody(store),
        "attachments": _attachments(store),
        "negative_findings": [f.to_json() for f in findings],
        "negative_summary": summarise(findings),
        "integrity": {
            "audit_head_seq": head_seq,
            "audit_head_digest": head_digest,
            "audit_records": chain.records_checked,
            "audit_ok": chain.ok,
            "audit_broken_at_seq": chain.broken_at_seq,
            "audit_reason": chain.reason,
            "audit_warnings": list(chain.warnings),
        },
    }
    doc["conclusions_digest"] = conclusions_digest(doc)
    return doc


def conclusions(doc: dict[str, Any]) -> dict[str, Any]:
    """The reproducible part of the document — see `CONCLUSION_KEYS`."""
    return {key: doc[key] for key in CONCLUSION_KEYS if key in doc}


def conclusions_digest(doc: dict[str, Any]) -> str:
    """SHA-256 over the conclusions alone. The number two labs compare (AC-11).

    Deliberately excludes `generated_utc`, the audit head, the artefact list and the case
    metadata: none of those describe the disk, and all of them differ between two honest
    examinations of the same disk.
    """
    return hashlib.sha256(canonical_json(conclusions(doc)) + b"\n").hexdigest()


# --- sections ---------------------------------------------------------------------------


def _case(store: CaseStore) -> dict[str, Any]:
    rows = _rows(store, "SELECT * FROM case_meta ORDER BY case_id")
    return rows[0] if rows else {}


def _evidence(store: CaseStore) -> list[dict[str, Any]]:
    out = []
    for row in _rows(store, "SELECT * FROM evidence ORDER BY id"):
        row["members"] = _json_col(row.pop("members_json", None), [])
        row["gaps"] = _json_col(row.pop("gaps_json", None), [])
        out.append(row)
    return out


def _identification(store: CaseStore) -> list[dict[str, Any]]:
    out = []
    for row in _rows(store, "SELECT * FROM identification ORDER BY evidence_id"):
        row["candidates"] = _json_col(row.pop("candidates_json", None), [])
        row["probe_plan"] = _json_col(row.pop("probe_plan_json", None), {})
        row["observations"] = _json_col(row.pop("observations_json", None), [])
        row["errors"] = _json_col(row.pop("errors_json", None), [])
        out.append(row)
    return out


def _disk_layout(store: CaseStore) -> list[dict[str, Any]]:
    out = []
    for row in _rows(store, "SELECT * FROM disk_layout ORDER BY evidence_id"):
        row["index_extents"] = _json_col(row.pop("index_extents_json", None), [])
        row["log_extents"] = _json_col(row.pop("log_extents_json", None), [])
        out.append(row)
    return out


def _coverage(store: CaseStore) -> list[dict[str, Any]]:
    """Per-evidence totals plus the spans, with the tiling invariant restated.

    The spans are what make the section checkable, and the totals are what a reader
    actually looks at. Both are emitted; the sum is emitted too, so a reader can verify
    the buckets tile the image without re-adding the list themselves.
    """
    out = []
    for ev in _rows(store, "SELECT id, capacity_bytes FROM evidence ORDER BY id"):
        spans = _rows(
            store,
            "SELECT offset, length, bucket FROM coverage WHERE evidence_id = ? "
            "ORDER BY offset, length, bucket",
            (ev["id"],),
        )
        if not spans:
            continue
        totals: dict[str, int] = {}
        for span in spans:
            totals[span["bucket"]] = totals.get(span["bucket"], 0) + span["length"]
        covered = sum(totals.values())
        out.append(
            {
                "evidence_id": ev["id"],
                "image_size_bytes": ev["capacity_bytes"],
                "totals_bytes": dict(sorted(totals.items())),
                "covered_bytes": covered,
                "tiles_image": ev["capacity_bytes"] is not None
                and covered == ev["capacity_bytes"],
                "spans": spans,
            }
        )
    return out


def _recordings(store: CaseStore) -> list[dict[str, Any]]:
    """Ordered by channel then time then id, so two runs list them identically.

    `id` breaks the tie. Two recordings on one channel can legitimately share a start
    time — a main and a sub stream, or a T1 item and the T3 fragment beside it.
    """
    out = []
    rows = _rows(
        store,
        "SELECT * FROM recording ORDER BY evidence_id, channel, t_local_start, id",
    )
    for row in rows:
        row["extents"] = _json_col(row.pop("extents_json", None), [])
        row["notes"] = _json_col(row.pop("notes_json", None), {})
        out.append(row)
    return out


def _time(store: CaseStore) -> dict[str, Any]:
    """The time model: observations, and whether absolute time was established at all.

    `absolute_established` is the field that decides whether the report may print a UTC
    time anywhere (FR-53). It is derived from the recordings, not from the presence of an
    observation — an observation that produced no usable offset establishes nothing.
    """
    observations = _rows(
        store,
        "SELECT * FROM time_observation ORDER BY evidence_id, valid_from, method, id",
    )
    with_ref = store.conn.execute(
        "SELECT COUNT(*) FROM recording WHERE t_ref_start IS NOT NULL"
    ).fetchone()[0]
    total = store.conn.execute("SELECT COUNT(*) FROM recording").fetchone()[0]
    methods = sorted({str(o["method"]) for o in observations})
    return {
        "observations": observations,
        "methods_used": methods,
        "absolute_established": with_ref > 0,
        "recordings_total": total,
        "recordings_with_reference_time": with_ref,
    }


def _device_events(store: CaseStore) -> list[dict[str, Any]]:
    return _rows(
        store,
        "SELECT * FROM device_event ORDER BY evidence_id, t_device, kind, detail",
    )


def _annotations(store: CaseStore) -> list[dict[str, Any]]:
    out = []
    rows = _rows(
        store,
        "SELECT * FROM annotation ORDER BY recording_id, frame_no, label, id",
    )
    for row in rows:
        row["bbox"] = _json_col(row.pop("bbox_json", None), None)
        out.append(row)
    return out


def _artifacts(store: CaseStore) -> list[dict[str, Any]]:
    out = []
    rows = _rows(store, "SELECT * FROM artifact ORDER BY kind, path, sha256")
    for row in rows:
        row["tool_versions"] = _json_col(row.pop("tool_versions_json", None), {})
        out.append(row)
    return out


def _custody(store: CaseStore) -> list[dict[str, Any]]:
    """Transfers, plus where the chain fails to join up.

    `chain_breaks` is emitted per evidence item rather than left for a reader to spot by
    comparing holder names down the table — that is the check Form F-2 exists to make
    possible, and a report that prints the rows without running it has only printed rows.
    """
    entries = _rows(store, "SELECT * FROM custody ORDER BY seq")
    breaks: dict[str, list[str]] = {}
    for ev_id in sorted({str(e["evidence_id"]) for e in entries if e["evidence_id"]}):
        found = custody_service.chain_breaks(store, ev_id)
        if found:
            breaks[ev_id] = found
    return [{**e, "chain_breaks": breaks.get(str(e["evidence_id"]), [])} for e in entries]


def _attachments(store: CaseStore) -> list[dict[str, Any]]:
    """External case documents (FR-74). Never tool output — see services/custody.py."""
    return _rows(store, "SELECT * FROM attachment ORDER BY kind, id")


# --- serialisation ----------------------------------------------------------------------


def serialise(doc: dict[str, Any]) -> bytes:
    """Canonical bytes for hashing and for writing to disk.

    One trailing newline: a text file without one is awkward for `sha256sum` users and
    for `git diff`, and the newline is part of what gets hashed either way.
    """
    return canonical_json(doc) + b"\n"


def digest(doc: dict[str, Any]) -> str:
    """SHA-256 over `serialise(doc)` — the number the report prints for verification."""
    return hashlib.sha256(serialise(doc)).hexdigest()


def readable(doc: dict[str, Any]) -> str:
    """Indented copy for a human reader.

    Deliberately *not* what gets hashed. Pretty-printing is a presentation choice and
    changing the indent must never change the digest, so the canonical form stays the
    only hashed form.
    """
    return json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
