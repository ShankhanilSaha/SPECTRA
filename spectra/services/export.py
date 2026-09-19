"""Evidence-clip export (doc 3 §6, §12.2; FR-30..FR-33).

    plugin.frames(extent) → ES writer (payload bytes verbatim) → Hasher → artifacts/   (FR-32)
                          → frame index (per-frame size, PTS, raw device time) → artifacts/
    MediaTool.remux(-c copy, per-frame PTS) → verify VCL identity → Hasher → artifacts/ (FR-31)

The ES is the primary extraction and is always produced. The MP4 is a verified
re-containering of it; when it cannot be produced faithfully (no FFmpeg, a codec with no
Annex-B path, device time running backwards) the export still completes with the ES, and
the reason the MP4 is absent is recorded in the audit log and shown to the examiner.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from spectra.core.casestore import ArtifactRef, CaseStore
from spectra.core.media import FrameIndexEntry, MediaError, MediaTool, RemuxRefused
from spectra.core.models import Extent
from spectra.services import ServiceError
from spectra.services.evidence import open_evidence
from spectra.services.parse import selected_plugin

VIDEO_KINDS = ("I", "P", "B")
ES_SUFFIX = {"h264": ".h264", "h265": ".h265"}


@dataclass
class ExportResult:
    recording_id: str
    es: ArtifactRef
    frame_index: ArtifactRef
    frames: int
    mp4: ArtifactRef | None = None
    mp4_skipped_reason: str | None = None
    logs: list[ArtifactRef] = field(default_factory=list)
    vcl: dict[str, Any] | None = None
    copied_to: list[Path] = field(default_factory=list)


def export_recording(
    store: CaseStore, recording_id: str, media: MediaTool | None, out_dir: Path | None = None
) -> ExportResult:
    cursor = store.conn.execute("SELECT * FROM recording WHERE id=?", (recording_id,))
    values = cursor.fetchone()
    if values is None:
        raise ServiceError(f"no such recording: {recording_id}")
    rec = dict(zip([c[0] for c in cursor.description], values, strict=True))
    evidence_id = rec["evidence_id"]
    plugin, _ = selected_plugin(store, evidence_id)
    extents = [Extent(o, n) for o, n in json.loads(rec["extents_json"])]
    params = {"evidence_id": evidence_id, "family": plugin.family,
              "plugin_version": plugin.plugin_version, "codec": rec["codec"],
              "channel": rec["channel"], "extents": json.loads(rec["extents_json"])}
    if media is not None:
        params["ffmpeg"] = media.info().to_json()

    with store.audit.operation("export", target=recording_id, params=params) as op:
        assert op.start_record is not None
        created, seq = op.start_record.ts_utc, op.start_record.seq
        refs: list[ArtifactRef] = []
        index_refs: list[ArtifactRef] = []
        frames = 0
        with (
            open_evidence(store, evidence_id) as src,
            store.new_artifact("es", recording_id=recording_id, created_utc=created,
                               source_record_seq=seq, result=refs) as es_out,
            store.new_artifact("frame_index", recording_id=recording_id, created_utc=created,
                               source_record_seq=seq, result=index_refs) as index_out,
        ):
            offset_in_es = 0
            for extent in extents:
                for frame in plugin.frames(src, extent):
                    if frame.kind not in VIDEO_KINDS or frame.channel != rec["channel"]:
                        continue
                    size = len(frame.payload)
                    es_out.write(frame.payload)
                    entry = {
                        "n": frames, "es_offset": offset_in_es, "size": size,
                        "kind": frame.kind, "key": frame.kind == "I", "pts_ms": frame.pts_ms,
                        "source_offset": frame.extent.offset, "source_length": frame.extent.length,
                        "sequence": frame.sequence,
                        "t_device_raw": frame.t_device.raw_repr() if frame.t_device else None,
                        "t_device_encoding": frame.t_device.encoding if frame.t_device else None,
                        "t_device_local": (frame.t_device.local.isoformat()
                                           if frame.t_device and frame.t_device.local else None),
                    }
                    index_out.write((json.dumps(entry, sort_keys=True) + "\n").encode("utf-8"))
                    offset_in_es += size
                    frames += 1
            if frames == 0:
                raise ServiceError(f"{recording_id}: no video frames found in its extents")
        result = ExportResult(recording_id, refs[0], index_refs[0], frames)

        if media is None:
            result.mp4_skipped_reason = "FFmpeg not available (set SPECTRA_FFMPEG)"
        else:
            _remux(store, media, rec, result, created, seq)

        op.result_params = {
            "frames": frames,
            "es": {"sha256": result.es.sha256, "md5": result.es.md5, "size": result.es.size},
            "frame_index_sha256": result.frame_index.sha256,
            "mp4": ({"sha256": result.mp4.sha256, "md5": result.mp4.md5, "size": result.mp4.size}
                    if result.mp4 else None),
            "mp4_skipped_reason": result.mp4_skipped_reason,
            "vcl_verification": result.vcl,
            "logs": [r.sha256 for r in result.logs],
        }
        op.hash_after = result.mp4.sha256 if result.mp4 else result.es.sha256
    store.write_manifest()
    if out_dir is not None:
        result.copied_to = _copy_out(store, result, rec, out_dir)
    return result


def _remux(
    store: CaseStore, media: MediaTool, rec: dict[str, Any], result: ExportResult,
    created: str, seq: int,
) -> None:
    work = store.root / "artifacts" / "tmp" / f"export-{seq}"
    mp4_tmp = work / f"{rec['id']}.mp4"
    try:
        remuxed = media.remux(
            result.es.path, _read_index(result.frame_index.path), rec["codec"], mp4_tmp,
            work, f"{rec['id']}-{seq}",
        )
    except RemuxRefused as exc:
        result.mp4_skipped_reason = str(exc)
        shutil.rmtree(work, ignore_errors=True)
        return
    except MediaError:
        _keep_logs(store, work, rec, result, created, seq)
        shutil.rmtree(work, ignore_errors=True)
        raise
    result.mp4 = store.add_artifact_file(
        remuxed.mp4_path, "mp4_evidence", recording_id=rec["id"], created_utc=created,
        tool_versions=remuxed.tool.to_json(), source_record_seq=seq,  # type: ignore[arg-type]
    )
    result.vcl = {
        "method": "sha256 over length-prefixed VCL NAL units, source ES vs ES re-extracted "
                  "from the MP4 (identity per NAL unit; parameter-set placement excluded)",
        "vcl_digest": remuxed.source_vcl.vcl_digest,
        "vcl_units": remuxed.source_vcl.vcl_count,
        "non_vcl_units_source": remuxed.source_vcl.non_vcl_count,
        "non_vcl_units_mp4": remuxed.roundtrip_vcl.non_vcl_count,
        "result": "identical",
    }
    _keep_logs(store, work, rec, result, created, seq)
    shutil.rmtree(work, ignore_errors=True)


def _keep_logs(
    store: CaseStore, work: Path, rec: dict[str, Any], result: ExportResult, created: str, seq: int
) -> None:
    logs_dir = store.root / "logs"
    for log in sorted(logs_dir.glob(f"{rec['id']}-{seq}-*.log")):
        result.logs.append(store.add_artifact_file(
            log, "tool_log", recording_id=rec["id"], created_utc=created, source_record_seq=seq))


def _read_index(path: Path) -> Iterator[FrameIndexEntry]:
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            yield FrameIndexEntry.from_json(line)


def _copy_out(
    store: CaseStore, result: ExportResult, rec: dict[str, Any], out_dir: Path
) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    base = rec["id"]
    targets = [(result.es, f"{base}{ES_SUFFIX.get(rec['codec'], '.es')}"),
               (result.frame_index, f"{base}.frames.jsonl")]
    if result.mp4:
        targets.append((result.mp4, f"{base}.mp4"))
    copied = []
    for ref, name in targets:
        destination = out_dir / name
        if destination.exists():
            raise ServiceError(f"refusing to overwrite {destination}")
        shutil.copyfile(ref.path, destination)
        copied.append(destination)
    manifest = {
        "recording_id": base,
        "case_id": store.case_id,
        "files": {name: {"sha256": ref.sha256, "md5": ref.md5, "size": ref.size, "kind": ref.kind}
                  for ref, name in targets},
        "mp4_skipped_reason": result.mp4_skipped_reason,
        "vcl_verification": result.vcl,
        "note": "The disk image is the evidence; the MP4 is a verified extraction of its "
                "elementary stream. Transcoded copies are never evidence copies.",
    }
    manifest_path = out_dir / f"{base}.manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", "utf-8")
    copied.append(manifest_path)
    return copied


def artifact_rows(store: CaseStore, recording_id: str | None = None) -> list[dict[str, Any]]:
    """Stored artefacts, oldest first, each with its absolute path in the case store.

    Read-only. A viewer plays the evidence copy from here rather than from a loose file, so
    what is shown is the file whose digest the case records.
    """
    query = ("SELECT sha256, md5, kind, recording_id, is_derivative, created_utc, size_bytes"
             " FROM artifact")
    args: tuple[Any, ...] = ()
    if recording_id:
        query += " WHERE recording_id = ?"
        args = (recording_id,)
    cursor = store.conn.execute(query + " ORDER BY created_utc, sha256", args)
    names = [c[0] for c in cursor.description]
    rows = [dict(zip(names, row, strict=True)) for row in cursor]
    for row in rows:
        row["is_derivative"] = bool(row["is_derivative"])
        row["path"] = str(store.artifact_path(row["sha256"]))
    return rows
