"""ParseService — superblock + T1 enumeration through the selected plugin (FR-04, FR-28, FR-40)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from spectra.core.casestore import CaseStore
from spectra.core.models import DeviceTime, Recording
from spectra.plugins.base import LayoutNotSupported, VendorPlugin
from spectra.plugins.registry import get_plugin
from spectra.services import ServiceError
from spectra.services.evidence import open_evidence
from spectra.services.identify import identification_row


@dataclass(frozen=True, slots=True)
class ParseSummary:
    evidence_id: str
    family: str
    layout_version: str
    recordings: int
    channels: tuple[int | None, ...]


def selected_plugin(store: CaseStore, evidence_id: str) -> tuple[VendorPlugin, dict[str, Any]]:
    row = identification_row(store, evidence_id)
    if row["status"] == "ambiguous" and row["selection"] != "operator":
        raise ServiceError(
            f"{evidence_id} is ambiguous between several format families; review the matched "
            "bytes and run 'spectra identify select' (the choice is audited)"
        )
    if row["family"] is None:
        raise ServiceError(f"{evidence_id} is not a recognised format family: carving only")
    return get_plugin(row["family"])(), row


def parse(store: CaseStore, evidence_id: str) -> ParseSummary:
    plugin, row = selected_plugin(store, evidence_id)
    if not row["parse_supported"]:
        raise ServiceError(
            f"{evidence_id}: {row['family']} layout is not parseable by plugin version "
            f"{plugin.plugin_version} — carve-only (FR-03, known limitation L2)"
        )
    if store.conn.execute("SELECT 1 FROM recording WHERE evidence_id=? LIMIT 1",
                          (evidence_id,)).fetchone():
        raise ServiceError(f"{evidence_id} has already been parsed")
    params = {"family": plugin.family, "plugin_version": plugin.plugin_version,
              "layout_version": row["layout_version"], "include_orphans": False}
    with store.audit.operation("parse", target=evidence_id, params=params) as op:
        with open_evidence(store, evidence_id) as src:
            try:
                layout = plugin.superblock(src)
            except LayoutNotSupported as exc:
                raise ServiceError(f"{evidence_id}: {exc}") from exc
            store.conn.execute("BEGIN IMMEDIATE")
            try:
                # The volume format time is kept in the layer it belongs to. It is the
                # recorder's own clock, so it is stored device-local and `format_utc`
                # stays NULL until an offset is established (FR-53). Discarding it would
                # lose the fact that bounds how far back footage can possibly go.
                fmt = layout.format_time
                store.conn.execute(
                    "INSERT INTO disk_layout (evidence_id, block_size, block_count, blocks_used,"
                    " index_extents_json, log_extents_json, format_t_device_raw,"
                    " format_t_device_encoding, format_t_local, format_utc, family,"
                    " layout_version, note) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (evidence_id, layout.block_size, layout.block_count, layout.blocks_used,
                     json.dumps([e.to_json() for e in layout.index_extents]),
                     json.dumps([e.to_json() for e in layout.log_extents]),
                     None if fmt is None else fmt.raw_repr(),
                     None if fmt is None else fmt.encoding,
                     _local(fmt), None,
                     layout.family, layout.layout_version, layout.note),
                )
                first = store.conn.execute("SELECT COUNT(*) FROM recording").fetchone()[0]
                count = 0
                channels: set[int | None] = set()
                for recording in plugin.enumerate(src, layout, include_orphans=False):
                    count += 1
                    channels.add(recording.channel)
                    _insert_recording(store, f"REC-{first + count:04d}", evidence_id, recording)
                store.conn.commit()
            except BaseException:
                store.conn.rollback()
                raise
        ordered = tuple(sorted(channels, key=lambda c: (c is None, c or 0)))
        op.result_params = {"recordings": count, "channels": list(ordered),
                            "layout_note": layout.note}
    store.write_manifest()
    return ParseSummary(evidence_id, layout.family, layout.layout_version, count, ordered)


def _local(value: DeviceTime | None) -> str | None:
    return value.local.isoformat() if value is not None and value.local is not None else None


def _insert_recording(store: CaseStore, rec_id: str, evidence_id: str, rec: Recording) -> None:
    raw = [None if t is None else t.raw_repr() for t in (rec.t_start, rec.t_end)]
    encoding = next((t.encoding for t in (rec.t_start, rec.t_end) if t is not None), None)
    store.conn.execute(
        "INSERT INTO recording (id, evidence_id, channel, stream, codec, width, height, fps,"
        " t_device_raw, t_device_encoding, t_local_start, t_local_end, t_method, extents_json,"
        " size_bytes, recovery_tier, confidence, source_note, frame_count, notes_json)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            rec_id, evidence_id, rec.channel, rec.stream, rec.codec,
            rec.resolution[0] if rec.resolution else None,
            rec.resolution[1] if rec.resolution else None,
            rec.fps, json.dumps(raw), encoding, _local(rec.t_start), _local(rec.t_end),
            "none",  # no offset established yet: device-local time only (FR-53)
            json.dumps([e.to_json() for e in rec.extents]), rec.size_bytes, rec.recovery_tier,
            rec.confidence, rec.source_note, rec.frame_count, json.dumps(list(rec.notes)),
        ),
    )


def recording_rows(
    store: CaseStore, evidence_id: str | None = None, channel: int | None = None
) -> list[dict[str, Any]]:
    clauses, args = [], []
    if evidence_id:
        clauses.append("evidence_id = ?")
        args.append(evidence_id)
    if channel is not None:
        clauses.append("channel = ?")
        args.append(channel)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    cursor = store.conn.execute(f"SELECT * FROM recording{where} ORDER BY id", args)
    names = [c[0] for c in cursor.description]
    return [dict(zip(names, row, strict=True)) for row in cursor]
