"""`spectra` command-line interface (doc 7 §5). A peer of the UI over the same services —
no logic lives here beyond argument handling and presentation (doc 3 §2)."""

from __future__ import annotations

import getpass
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any

import typer

import spectra
from spectra.core.audit import CanonicalisationError
from spectra.core.casestore import CaseError, CaseMeta, CaseStore
from spectra.core.media import MediaError, MediaTool, locate_ffmpeg
from spectra.core.source import SourceError
from spectra.identify.engine import IdentificationResult, IdentifyError
from spectra.ml.motion import MotionConfig
from spectra.report import certificate, findings
from spectra.report import generator as report_generator
from spectra.services import ServiceError
from spectra.services import analytics as analytics_service
from spectra.services import custody as custody_service
from spectra.services import evidence as evidence_service
from spectra.services import export as export_service
from spectra.services import identify as identify_service
from spectra.services import parse as parse_service
from spectra.services import recover as recover_service
from spectra.services import report as report_service
from spectra.services import timeline as timeline_service

app = typer.Typer(no_args_is_help=True, add_completion=False,
                  help="SPECTRA — vendor-agnostic DVR/NVR forensic analysis (offline).")
case_app = typer.Typer(no_args_is_help=True, help="Create, open, inspect and verify cases.")
import_app = typer.Typer(no_args_is_help=True, help="Ingest existing images or export files.")
identify_app = typer.Typer(help="Identify the format family of an evidence item.")
list_app = typer.Typer(no_args_is_help=True, help="List case contents.")
export_app = typer.Typer(no_args_is_help=True, help="Export evidence clips.")
verify_app = typer.Typer(no_args_is_help=True, help="Third-party verification commands.")
time_app = typer.Typer(no_args_is_help=True,
                       help="Clock-offset evidence and time normalisation (FR-50..FR-53).")
report_app = typer.Typer(no_args_is_help=True,
                         help="Findings JSON and the BSA s. 63(4) certificate.")
analyze_app = typer.Typer(no_args_is_help=True,
                          help="Run offline ML and motion gating analysis (FR-90..FR-97).")
app.add_typer(case_app, name="case")
app.add_typer(time_app, name="time")
app.add_typer(import_app, name="import")
app.add_typer(identify_app, name="identify")
app.add_typer(list_app, name="list")
app.add_typer(export_app, name="export")
app.add_typer(verify_app, name="verify")
app.add_typer(analyze_app, name="analyze")
app.add_typer(report_app, name="report")

HANDLED = (CaseError, ServiceError, SourceError, IdentifyError, MediaError, CanonicalisationError)

CaseOpt = Annotated[Path | None, typer.Option("--case", help="Case directory (else SPECTRA_CASE "
                                              "or the case last opened with 'case open').")]
EvidenceOpt = Annotated[str | None, typer.Option("--evidence", help="Evidence ID, e.g. EV-001.")]
JsonOpt = Annotated[bool, typer.Option("--json", help="Machine-readable output.")]


# -- context --------------------------------------------------------------------------------

def operator() -> str:
    return os.environ.get("SPECTRA_OPERATOR") or getpass.getuser()


def _pointer_file() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home())
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "spectra" / "current_case"


def _case_dir(case: Path | None) -> Path:
    if case:
        return case
    if os.environ.get("SPECTRA_CASE"):
        return Path(os.environ["SPECTRA_CASE"])
    pointer = _pointer_file()
    if pointer.is_file():
        return Path(pointer.read_text("utf-8").strip())
    raise CaseError("no case selected: pass --case, set SPECTRA_CASE, or run 'spectra case open'")


def _open(case: Path | None) -> CaseStore:
    return CaseStore.open(_case_dir(case), operator())


def _media(store: CaseStore) -> MediaTool | None:
    path = locate_ffmpeg()
    return MediaTool(path, store.root / "logs") if path else None


def _emit_json(data: Any) -> None:
    typer.echo(json.dumps(data, indent=2, sort_keys=True))


# -- case -----------------------------------------------------------------------------------

@case_app.command("new")
def case_new(
    case_id: Annotated[str, typer.Option("--id", help="Agency case number.")],
    directory: Annotated[Path, typer.Option("--dir", help="Case directory to create.")],
    title: Annotated[str, typer.Option("--title")] = "",
    agency: Annotated[str, typer.Option("--agency")] = "",
    fir: Annotated[str, typer.Option("--fir", help="FIR reference.")] = "",
    authority: Annotated[str, typer.Option("--authority", help="Authority reference.")] = "",
    examiner: Annotated[str, typer.Option("--examiner")] = "",
    designation: Annotated[str, typer.Option("--designation")] = "",
    s79a: Annotated[str, typer.Option("--s79a", help="IT Act s. 79A notification ref.")] = "",
) -> None:
    """Create a case directory with its database, audit chain and manifest."""
    meta = CaseMeta(case_id, title, agency, fir, authority, examiner, designation, s79a)
    with CaseStore.create(directory, meta, operator()) as store:
        head_seq, head = store.audit.head()
    typer.echo(f"created case {case_id} at {directory}")
    typer.echo(f"audit head: seq {head_seq} {head}")


@case_app.command("open")
def case_open(directory: Path) -> None:
    """Make DIRECTORY the current case for subsequent commands."""
    with CaseStore.open(directory, operator()) as store:
        case_id = store.case_id
    pointer = _pointer_file()
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text(str(directory.resolve()), "utf-8")
    typer.echo(f"current case: {case_id} ({directory.resolve()})")


@case_app.command("info")
def case_info(case: CaseOpt = None, as_json: JsonOpt = False) -> None:
    """Show case metadata, evidence items and the audit head."""
    with _open(case) as store:
        meta = store.meta()
        items = [
            {k: row[k] for k in ("id", "kind", "provenance_class", "label", "capacity_bytes",
                                 "sha256", "source_path")}
            for row in (evidence_service.evidence_row(store, r[0]) for r in
                        store.conn.execute("SELECT id FROM evidence ORDER BY id"))
        ]
        head_seq, head = store.audit.head()
    data = {"case": meta, "evidence": items, "audit_head": {"seq": head_seq, "digest": head}}
    if as_json:
        _emit_json(data)
        return
    typer.echo(f"case {meta['case_id']}  {meta['title']}")
    for key in ("agency", "fir_ref", "examiner_name", "examiner_designation", "created_utc"):
        if meta.get(key):
            typer.echo(f"  {key}: {meta[key]}")
    for item in items:
        typer.echo(f"  {item['id']}  {item['kind']}  class {item['provenance_class']}  "
                   f"{item['capacity_bytes']} bytes  sha256 {item['sha256']}")
    typer.echo(f"  audit head: seq {head_seq} {head}")


@case_app.command("verify")
def case_verify(case: CaseOpt = None, as_json: JsonOpt = False) -> None:
    """Verify the audit chain, the manifest head digest, and every artefact hash (FR-72)."""
    with _open(case) as store:
        result = store.verify()
    _print_verify(result, as_json)
    if not result.ok:
        raise typer.Exit(1)


def _print_verify(result: Any, as_json: bool) -> None:
    if as_json:
        _emit_json({
            "ok": result.ok, "records_checked": result.chain.records_checked,
            "head_seq": result.chain.head_seq, "head_digest": result.chain.head_digest,
            "broken_at_seq": result.chain.broken_at_seq, "artifacts_checked":
            result.artifacts_checked, "problems": list(result.problems),
            "warnings": list(result.warnings),
        })
        return
    typer.echo(f"audit records checked: {result.chain.records_checked}")
    typer.echo(f"audit head: seq {result.chain.head_seq} {result.chain.head_digest}")
    typer.echo(f"artefacts checked: {result.artifacts_checked}")
    for warning in result.warnings:
        typer.echo(f"WARNING: {warning}")
    for problem in result.problems:
        typer.echo(f"FAIL: {problem}")
    typer.echo("VERIFIED" if result.ok else "VERIFICATION FAILED")


@case_app.command("custody")
def case_custody(
    from_holder: Annotated[str, typer.Option("--from", help="Who released the item.")],
    to_holder: Annotated[str, typer.Option("--to", help="Who received it.")],
    purpose: Annotated[str, typer.Option("--purpose", help="Why it moved.")],
    evidence: EvidenceOpt = None,
    seal: Annotated[str, typer.Option("--seal", help="Seal number (Form F-2).")] = "",
    seal_intact: Annotated[bool | None, typer.Option(
        "--seal-intact/--seal-broken",
        help="Whether the seal was intact on receipt. Omit if the item was not sealed — "
             "that is a different fact from a broken seal.")] = None,
    signature: Annotated[str, typer.Option("--signature", help="Signature reference.")] = "",
    note: Annotated[str, typer.Option("--note")] = "",
    case: CaseOpt = None,
) -> None:
    """Record a physical custody transfer (FR-73, SOP Form F-2)."""
    with _open(case) as store:
        ev_id = evidence_service.default_evidence_id(store, evidence)
        entry = custody_service.record_transfer(
            store, ev_id, from_holder=from_holder, to_holder=to_holder, purpose=purpose,
            seal_number=seal, seal_intact=seal_intact, signature_ref=signature, note=note,
        )
        breaks = custody_service.chain_breaks(store, ev_id)
    typer.echo(f"custody entry {entry.seq} recorded for {ev_id}")
    typer.echo(f"  {entry.from_holder} -> {entry.to_holder}   {entry.purpose}")
    seal_text = {None: "no seal recorded", True: "seal intact", False: "SEAL BROKEN"}[
        entry.seal_intact
    ]
    typer.echo(f"  {seal_text}" + (f" ({entry.seal_number})" if entry.seal_number else ""))
    for gap in breaks:
        typer.echo(f"  chain gap: {gap}", err=True)


@case_app.command("attach")
def case_attach(
    file: Annotated[Path, typer.Option("--file", help="Document to attach and hash.")],
    kind: Annotated[str, typer.Option(
        "--kind", help="One of: " + ", ".join(custody_service.ATTACHMENT_KINDS))],
    description: Annotated[str, typer.Option("--description")] = "",
    provided_by: Annotated[str, typer.Option("--provided-by")] = "",
    evidence: EvidenceOpt = None,
    case: CaseOpt = None,
) -> None:
    """Attach and hash an external case document (FR-74).

    The panchnama, the BNSS s. 105 seizure recording, authorisation letters. Hashed on the
    way in and never modified, so their integrity is checkable exactly like the image's.
    """
    with _open(case) as store:
        ev_id = evidence if evidence else None
        ref = custody_service.attach_document(
            store, file, kind, description=description, evidence_id=ev_id,
            provided_by=provided_by,
        )
    typer.echo(f"attached {ref.id}  {ref.kind}  {ref.filename}")
    typer.echo(f"  sha256 {ref.sha256}")
    typer.echo(f"  size   {ref.size_bytes} bytes")
    if ref.statutory_ref:
        typer.echo(f"  under  {ref.statutory_ref}")


@case_app.command("chain")
def case_chain(evidence: EvidenceOpt = None, case: CaseOpt = None,
               as_json: JsonOpt = False) -> None:
    """Show the custody chain and attached documents for an evidence item."""
    with _open(case) as store:
        ev_id = evidence_service.default_evidence_id(store, evidence)
        entries = custody_service.custody_entries(store, ev_id)
        docs = custody_service.attachments(store, ev_id)
        docs += [d for d in custody_service.attachments(store, None)
                 if d.id not in {x.id for x in docs}]
        breaks = custody_service.chain_breaks(store, ev_id)
    if as_json:
        _emit_json({"evidence_id": ev_id, "custody": [e.to_json() for e in entries],
                    "attachments": [d.to_json() for d in docs], "chain_breaks": breaks})
        return
    typer.echo(f"custody chain for {ev_id}")
    if not entries:
        typer.echo("  (no transfers recorded — the chain is not established)")
    for entry in entries:
        seal = {None: "unsealed", True: "sealed/intact", False: "SEAL BROKEN"}[
            entry.seal_intact
        ]
        typer.echo(f"  {entry.seq:>3}  {entry.ts_utc}  {entry.from_holder} -> "
                   f"{entry.to_holder}  [{seal}]  {entry.purpose}")
    for gap in breaks:
        typer.echo(f"  GAP: {gap}")
    typer.echo("")
    typer.echo(f"attached documents ({len(docs)})")
    for doc in docs:
        typer.echo(f"  {doc.id}  {doc.kind:<16} {doc.filename}  {doc.sha256[:16]}...")


# -- import ---------------------------------------------------------------------------------

@import_app.command("image")
def import_image(
    file: Annotated[Path, typer.Option("--file", help="Raw, split-raw (.001) or E01 image.")],
    provenance: Annotated[str, typer.Option("--provenance", help="Provenance class A|B|C|D of "
                                            "this image — stated by the examiner (FR-19).")],
    label: Annotated[str, typer.Option("--label")] = "",
    note: Annotated[str, typer.Option("--note")] = "",
    case: CaseOpt = None,
) -> None:
    """Ingest an existing image and hash it (FR-17)."""
    with _open(case) as store:
        result = evidence_service.import_image(store, file, provenance.upper(), label, note)
    _print_ingest(result)


@import_app.command("files")
def import_files(
    directory: Annotated[Path, typer.Option("--dir", help="Directory of export files.")],
    label: Annotated[str, typer.Option("--label")] = "",
    note: Annotated[str, typer.Option("--note")] = "",
    case: CaseOpt = None,
) -> None:
    """Ingest owner-provided export files as provenance class D (FR-18)."""
    with _open(case) as store:
        result = evidence_service.import_files(store, directory, label, note)
    _print_ingest(result)


def _print_ingest(result: evidence_service.IngestResult) -> None:
    typer.echo(f"ingested {result.evidence_id}: {result.size} bytes")
    typer.echo(f"  md5    {result.md5}")
    typer.echo(f"  sha256 {result.sha256}")
    if result.gaps:
        typer.echo(f"  WARNING: {len(result.gaps)} unreadable range(s) zero-filled and recorded")
    status = result.embedded_hash_check.get("status")
    if status == "MISMATCH":
        typer.echo("  WARNING: MD5 stored in the E01 does not match its media — image corrupt "
                   "or altered (recorded on the evidence item)")
    elif status == "none_stored" and result.source_format == "ewf":
        typer.echo("  NOTE: the E01 stores no MD5; chunk corruption would be undetectable")


# -- identify -------------------------------------------------------------------------------

@identify_app.callback(invoke_without_command=True)
def identify_cmd(
    ctx: typer.Context, evidence: EvidenceOpt = None, case: CaseOpt = None, as_json: JsonOpt = False
) -> None:
    """Probe every registered plugin and report all candidates with matched bytes (FR-01..04)."""
    if ctx.invoked_subcommand is not None:
        return
    with _open(case) as store:
        evidence_id = evidence_service.default_evidence_id(store, evidence)
        result = identify_service.run_identify(store, evidence_id)
    if as_json:
        _emit_json({"evidence_id": evidence_id, **result.to_json()})
    else:
        _print_identification(evidence_id, result)


def _print_identification(evidence_id: str, result: IdentificationResult) -> None:
    typer.echo(f"{evidence_id}: {result.status.upper()}  (support: {result.support})")
    for candidate in result.candidates:
        layout = candidate.layout_version or "unrecognised"
        typer.echo(f"  candidate {candidate.family}  layout {layout}"
                   f"  confidence {candidate.confidence:.2f}  parse_supported "
                   f"{candidate.parse_supported}  plugin {candidate.plugin_version}")
        for match in candidate.matches:
            typer.echo(f"    @ {match.offset:#x}  {match.data.hex(' ')}  — {match.description}")
        if candidate.note:
            typer.echo(f"    note: {candidate.note}")
    for observation in result.observations:
        typer.echo(f"  observed @ {observation.offset:#x} {observation.data.hex(' ')} — "
                   f"{observation.description}")
    for error in result.errors:
        typer.echo(f"  PROBE ERROR in {error.family} {error.plugin_version}: "
                   f"{error.error_type}: {error.message}")
    if result.status == "ambiguous":
        typer.echo("  More than one family matched. Review the matched bytes, then run "
                   "'spectra identify select --family F --reason \"...\"'. Nothing was selected.")
    elif result.status == "unknown":
        typer.echo("  No format family matched: not parseable; carving is the remaining route.")


@identify_app.command("select")
def identify_select(
    family: Annotated[str, typer.Option("--family")],
    reason: Annotated[str, typer.Option("--reason", help="Why this candidate (audited).")],
    evidence: EvidenceOpt = None,
    case: CaseOpt = None,
) -> None:
    """Select one of the reported candidates for an ambiguous identification (audited)."""
    with _open(case) as store:
        evidence_id = evidence_service.default_evidence_id(store, evidence)
        chosen = identify_service.select_family(store, evidence_id, family, reason)
    typer.echo(f"{evidence_id}: selected {chosen.family} "
               f"(parse_supported {chosen.parse_supported})")


# -- parse / list / export ------------------------------------------------------------------

@app.command("parse")
def parse_cmd(evidence: EvidenceOpt = None, case: CaseOpt = None) -> None:
    """Read the layout and enumerate T1 recordings through the selected plugin."""
    with _open(case) as store:
        evidence_id = evidence_service.default_evidence_id(store, evidence)
        summary = parse_service.parse(store, evidence_id)
    typer.echo(f"{summary.evidence_id}: {summary.family} {summary.layout_version} — "
               f"{summary.recordings} recording(s) on channel(s) {list(summary.channels)}")
    typer.echo("  times are device-local; no clock offset established (FR-53)")



@app.command("recover")
def recover_cmd(
    tiers: Annotated[str, typer.Option("--tiers",
        help="Comma-separated: T2 (orphan index entries), T3 (signature carve), "
             "T4 (bad-sector tolerant). Default T2,T3.")] = "T2,T3",
    budget: Annotated[float | None, typer.Option("--budget-seconds",
        help="Abort the carve after this long rather than returning a short result.")] = None,
    evidence: EvidenceOpt = None,
    case: CaseOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """Recover deleted and unindexed footage, and write the coverage map (FR-41..FR-46)."""
    requested = tuple(t.strip().upper() for t in tiers.split(",") if t.strip())
    with _open(case) as store:
        evidence_id = evidence_service.default_evidence_id(store, evidence)
        summary = recover_service.recover(store, evidence_id, requested, budget_s=budget)
    if as_json:
        _emit_json(summary.to_json())
        return

    typer.echo(f"{summary.evidence_id}: ran {', '.join(summary.tiers_run) or 'nothing'}"
               f" — {summary.added} new recording(s)")
    for tier, count in sorted(summary.by_tier.items()):
        typer.echo(f"  {tier}: {count}")
    if summary.duplicates_merged:
        typer.echo(f"  {summary.duplicates_merged} overlapping result(s) merged, "
                   "highest tier kept (FR-44)")
    for skipped in summary.skipped:
        typer.echo(f"  skipped {skipped}")

    gain = summary.gain_pct
    if gain is not None:
        typer.echo(f"  recovered {summary.recovered_minutes:.1f} min against "
                   f"{summary.t1_minutes:.1f} min at T1 — {gain}% (AC-06 target 30%)")
    elif summary.added:
        typer.echo("  yield in minutes not measurable: no T1 baseline with times on "
                   "this evidence")
    for note in summary.notes:
        typer.echo(f"  note: {note}")


@app.command("coverage")
def coverage_cmd(
    evidence: EvidenceOpt = None, case: CaseOpt = None, as_json: JsonOpt = False
) -> None:
    """Show the byte-level coverage map — including what could not be explained (FR-29)."""
    with _open(case) as store:
        evidence_id = evidence_service.default_evidence_id(store, evidence)
        rows = recover_service.coverage_rows(store, evidence_id)
    if as_json:
        _emit_json(rows)
        return
    if not rows:
        typer.echo(f"{evidence_id}: no coverage map yet — run 'spectra recover'")
        return

    totals: dict[str, int] = {}
    for row in rows:
        totals[row["bucket"]] = totals.get(row["bucket"], 0) + row["length"]
    total = sum(totals.values())
    typer.echo(f"{evidence_id}: {len(rows)} run(s) over {total} bytes")
    for bucket in ("structural", "parsed", "carved", "unreadable", "unaccounted"):
        size = totals.get(bucket, 0)
        pct = (100.0 * size / total) if total else 0.0
        typer.echo(f"  {bucket:<12} {size:>14,} bytes  {pct:5.1f}%")
    if totals.get("unaccounted"):
        typer.echo("  unaccounted bytes are a finding: data the tool could not explain "
                   "(report §7, FR-81)")


@list_app.command("recordings")
def list_recordings(
    evidence: EvidenceOpt = None,
    channel: Annotated[int | None, typer.Option("--channel")] = None,
    case: CaseOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """List recordings with tier, confidence and device-local times."""
    with _open(case) as store:
        rows = parse_service.recording_rows(store, evidence, channel)
    if as_json:
        _emit_json(rows)
        return
    for row in rows:
        start = row["t_local_start"] or "time unknown"
        end = row["t_local_end"] or "time unknown"
        typer.echo(f"{row['id']}  {row['evidence_id']}  ch {row['channel']}  {row['codec']}  "
                   f"{start} → {end} (device-local)  {row['frame_count']} frames  "
                   f"{row['recovery_tier']} conf {row['confidence']:.2f}")
    if not rows:
        typer.echo("no recordings")


@export_app.command("clip")
def export_clip(
    recording: Annotated[str, typer.Option("--recording", help="Recording ID, e.g. REC-0001.")],
    out: Annotated[
        Path | None, typer.Option("--out", help="Also copy files + manifest here.")
    ] = None,
    case: CaseOpt = None,
) -> None:
    """Export the ES (always) and a verified evidence-copy MP4 (when possible)."""
    with _open(case) as store:
        result = export_service.export_recording(store, recording, _media(store), out)
    typer.echo(f"{result.recording_id}: {result.frames} frames")
    typer.echo(f"  ES   sha256 {result.es.sha256}  md5 {result.es.md5}")
    if result.mp4:
        typer.echo(f"  MP4  sha256 {result.mp4.sha256}  md5 {result.mp4.md5}  (evidence copy, "
                   "VCL NAL units verified identical to the ES)")
    else:
        typer.echo(f"  MP4  not produced: {result.mp4_skipped_reason}")
    for path in result.copied_to:
        typer.echo(f"  wrote {path}")


# -- time & timeline --------------------------------------------------------------------------

@time_app.command("set")
def time_set(
    method: Annotated[str, typer.Option("--method", help="A|B|C|D (FR-51).")],
    device_time: Annotated[str, typer.Option("--device-time", help="Device wall-clock reading, "
                                             "naive ISO, e.g. 2026-03-05T14:22:00.")],
    true_time: Annotated[str, typer.Option("--true-time", help="True time WITH timezone, "
                                           "e.g. 2026-03-05T14:04:18Z.")],
    uncertainty: Annotated[float | None, typer.Option("--uncertainty",
                                                      help="± seconds (method floor if omitted; "
                                                      "method C defaults to ±60).")] = None,
    tz_offset_minutes: Annotated[int | None, typer.Option("--tz-offset-minutes",
                                                          help="Device timezone, e.g. 330 for "
                                                          "+05:30, when known.")] = None,
    valid_from: Annotated[str | None, typer.Option("--valid-from", help="Device-local start of "
                                                   "the range this offset covers.")] = None,
    valid_to: Annotated[str | None, typer.Option("--valid-to", help="Device-local end of the "
                                                 "range this offset covers.")] = None,
    note: Annotated[str, typer.Option("--note", help="How the reading was made (audited, "
                                      "printed in the report).")] = "",
    evidence: EvidenceOpt = None,
    case: CaseOpt = None,
) -> None:
    """Record one clock-offset observation and renormalise the evidence item (audited)."""
    methods = {"A": "A_ntp", "B": "B_reference_capture", "C": "C_external_event",
               "D": "D_live_rtc"}
    resolved = methods.get(method.upper(), method)
    if resolved not in methods.values():
        raise typer.BadParameter(f"--method must be one of {'|'.join(methods)} (FR-51)")
    with _open(case) as store:
        evidence_id = evidence_service.default_evidence_id(store, evidence)
        summary = timeline_service.set_offset(
            store, evidence_id, resolved, device_time, true_time, uncertainty,
            tz_offset_minutes * 60 if tz_offset_minutes is not None else None,
            valid_from, valid_to, note,
        )
    typer.echo(f"{summary.evidence_id}: recorded {summary.observation_id}")
    typer.echo(f"  {summary.offset_note}")
    typer.echo(f"  recordings normalised: {summary.recordings_normalised}  "
               f"still device-local: {summary.recordings_refused}")


@time_app.command("show")
def time_show(evidence: EvidenceOpt = None, case: CaseOpt = None,
              as_json: JsonOpt = False) -> None:
    """Show the offset observations and the clock model they establish (FR-50)."""
    with _open(case) as store:
        evidence_id = evidence_service.default_evidence_id(store, evidence)
        data = timeline_service.show(store, evidence_id)
    if as_json:
        _emit_json(data)
        return
    typer.echo(f"{data['evidence_id']}: {len(data['observations'])} observation(s)")
    for obs in data["observations"]:
        typer.echo(f"  {obs['id']}  {obs['method']}  device {obs['device_local']} vs true "
                   f"{obs['true_utc']}  ±{obs['uncertainty_s']:g} s  {obs['note'] or ''}")
    for segment in data["segments"]:
        span = f"{segment['valid_from'] or '…'} → {segment['valid_to'] or '…'}"
        typer.echo(f"  segment {span}: {segment['note']}")
    if not data["segments"]:
        typer.echo("  no offset established: all times are device-local (FR-53)")


@app.command("timeline")
def timeline_cmd(evidence: EvidenceOpt = None, case: CaseOpt = None,
                 as_json: JsonOpt = False) -> None:
    """Multi-channel, multi-device timeline on the reference axis (FR-57 data, FR-60)."""
    with _open(case) as store:
        data = timeline_service.timeline_data(store, evidence)
    if as_json:
        _emit_json(data)
        return
    for lane in data["lanes"]:
        typer.echo(f"{lane['label']}")
        for segment in lane["segments"]:
            typer.echo(f"  {segment['start']} → {segment['end']}  "
                       f"±{segment['uncertainty_s']:g} s  {segment['recording_id']}")
    if data["unplaced"]:
        typer.echo("not on the reference axis (FR-53):")
        for item in data["unplaced"]:
            typer.echo(f"  {item['recording_id']}  ch {item['channel']}  — {item['reason']}")
    if not data["lanes"] and not data["unplaced"]:
        typer.echo("no recordings")


@app.command("gaps")
def gaps_cmd(
    evidence: EvidenceOpt = None,
    min_gap: Annotated[float, typer.Option("--min-gap", help="Smallest gap to report, "
                                           "seconds.")] = 1.0,
    case: CaseOpt = None,
    as_json: JsonOpt = False,
) -> None:
    """Report no-recording windows per channel, and synchronised across channels (FR-58)."""
    with _open(case) as store:
        data = timeline_service.gap_report(store, evidence, min_gap)
    if as_json:
        _emit_json(data)
        return
    for entry in data["evidence"]:
        basis = entry["basis"]
        banner = f"  [{entry['banner']}]" if entry.get("banner") else ""
        typer.echo(f"{entry['evidence_id']} ({basis} time){banner}")
        for gap in entry["channel_gaps"]:
            typer.echo(f"  ch {gap['channel']}: {gap['start']} → {gap['end']}  "
                       f"{gap['duration_s']:.0f} s  ±{gap['uncertainty_s']:g} s")
        for gap in entry["synchronised_gaps"]:
            typer.echo(f"  ALL CHANNELS: {gap['start']} → {gap['end']}  "
                       f"{gap['duration_s']:.0f} s — synchronised absence is a finding")
        if not entry["channel_gaps"] and not entry["synchronised_gaps"]:
            typer.echo(f"  no gaps ≥ {data['min_gap_s']:g} s")


# -- analytics ------------------------------------------------------------------------------

@analyze_app.command("motion")
def analyze_motion_cmd(
    recording: Annotated[str, typer.Argument(help="Recording ID to analyze.")],
    case: CaseOpt = None,
    sensitivity: Annotated[int, typer.Option("--sensitivity", "-s",
                                            help="Pixel threshold (1..255).")] = 25,
    min_area: Annotated[int, typer.Option("--min-area", "-a",
                                         help="Min changed pixels for motion.")] = 400,
    as_json: JsonOpt = False,
) -> None:
    """Run Stage 1 motion/activity gating on a recording (FR-90)."""
    cfg = MotionConfig(sensitivity=sensitivity, min_area_pixels=min_area)
    with _open(case) as store:
        summary = analytics_service.run_motion_analysis(store, recording, config=cfg)
    if as_json:
        data = {
            "recording_id": summary.recording_id,
            "total_frames": summary.total_frames,
            "motion_frames": summary.motion_frames,
            "segments": [
                {
                    "start_frame": s.start_frame,
                    "end_frame": s.end_frame,
                    "start_pts_ms": s.start_pts_ms,
                    "end_pts_ms": s.end_pts_ms,
                    "peak_score": s.peak_score,
                    "motion_frame_count": s.motion_frame_count,
                }
                for s in summary.segments
            ],
            "annotations_count": len(summary.annotations),
        }
        _emit_json(data)
        return

    typer.echo(f"recording: {summary.recording_id}")
    typer.echo(f"  total frames:  {summary.total_frames}")
    typer.echo(f"  motion frames: {summary.motion_frames}")
    typer.echo(f"  segments:      {len(summary.segments)}")
    for i, seg in enumerate(summary.segments, 1):
        typer.echo(f"    seg {i}: frames {seg.start_frame}..{seg.end_frame} "
                   f"({seg.start_pts_ms} ms → {seg.end_pts_ms} ms, "
                   f"peak score {seg.peak_score:.2f})")
    typer.echo(f"  annotations:   {len(summary.annotations)} persisted in case.db")


# -- third-party verification ---------------------------------------------------------------

@verify_app.command("chain")
def verify_chain(case: Annotated[Path, typer.Option("--case")], as_json: JsonOpt = False) -> None:
    """Verify a case's audit chain, manifest head and artefacts on any machine (FR-85)."""
    with CaseStore.open(case, operator()) as store:
        result = store.verify()
    _print_verify(result, as_json)
    if not result.ok:
        raise typer.Exit(1)


# -- report ---------------------------------------------------------------------------------

@report_app.command("findings")
def report_findings(
    case: CaseOpt = None,
    as_json: JsonOpt = False,
    out: Annotated[Path | None, typer.Option(
        "--out", help="Also write findings.json here (it is always stored in the case).")] = None,
) -> None:
    """Build the deterministic findings document the report renders from (FR-86, AC-11)."""
    with _open(case) as store:
        doc, digest = report_service.generate_findings(store)
    if out:
        out.write_bytes(findings.serialise(doc))
    if as_json:
        _emit_json(doc)
        return
    typer.echo(f"findings.json      sha256 {digest}")
    typer.echo(f"conclusions        sha256 {doc['conclusions_digest']}")
    typer.echo("  the conclusions digest covers what was found on the disk, and is "
               "what another\n  examiner reproduces; the file digest also covers "
               "this examination's own history")
    typer.echo(f"negative findings  {doc['negative_summary']['total']}")
    for severity, count in sorted(doc["negative_summary"]["by_severity"].items()):
        typer.echo(f"  {severity:<10} {count}")
    if not doc["integrity"]["audit_ok"]:
        typer.echo(
            f"WARNING: audit chain broken at seq {doc['integrity']['audit_broken_at_seq']}",
            err=True,
        )
    if out:
        typer.echo(f"written            {out}")


@report_app.command("certificate")
def report_certificate(
    evidence: EvidenceOpt = None,
    case: CaseOpt = None,
    instance: Annotated[int, typer.Option(
        "--instance",
        help="Submission number. s. 63(4) requires a certificate at each instance.")] = 1,
    instituted: Annotated[str | None, typer.Option(
        "--instituted-on",
        help="Date the proceeding was instituted (YYYY-MM-DD). Before 2024-07-01 selects "
             "the IEA s. 65B(4) form.")] = None,
    control: Annotated[str | None, typer.Option(
        "--control",
        help="How the device was held: Owned,Maintained,Managed,Operated.")] = None,
    out: Annotated[Path | None, typer.Option(
        "--out", help="Directory to write the certificate and its hash report into.")] = None,
    as_json: JsonOpt = False,
) -> None:
    """Pre-fill the BSA s. 63(4) certificate and its mandatory hash enclosure (FR-83).

    Signatures are left blank: the certificate is a sworn statement by two named people.
    """
    modes = tuple(m.strip() for m in (control or "").split(",") if m.strip())
    when = datetime.strptime(instituted, "%Y-%m-%d") if instituted else None
    with _open(case) as store:
        ev_id = evidence_service.default_evidence_id(store, evidence)
        cert, enclosure, warnings = report_service.generate_certificate(
            store, ev_id, instance=instance, instituted_on=when, control_modes=modes,
        )
        written = (
            report_service.write_certificate_files(store, cert, enclosure, out) if out else []
        )
    if as_json:
        _emit_json(
            {"certificate": cert.to_json(), "hash_report": enclosure, "warnings": warnings}
        )
        return
    typer.echo(cert.heading)
    typer.echo(cert.authority)
    typer.echo(f"case            {cert.case_id}   instance {cert.instance}")
    typer.echo(f"record          {cert.record_description}")
    typer.echo(f"source type     {cert.device.source_type}")
    typer.echo(f"hash boxes      {', '.join(cert.hashes.ticked) or 'none'}")
    typer.echo(f"enclosure       {cert.hash_report_ref}")
    held = certificate.withheld(enclosure)
    if held:
        typer.echo("")
        typer.echo("WITHHELD FROM TENDER (disclosed on the form):")
        for row in held:
            typer.echo(f"  {row['kind']}  {row['artefact']}")
    for part in cert.unsigned:
        typer.echo(f"UNSIGNED: {part}")
    for warning in warnings:
        typer.echo(f"warning: {warning}", err=True)
    for path in written:
        typer.echo(f"written         {path}")


@report_app.command("generate")
def report_generate(
    out: Annotated[Path, typer.Option("--out", help="Directory for the report files.")],
    case: CaseOpt = None,
    agency: Annotated[str, typer.Option("--agency", help="Agency name for the "
                                        "letterhead (FR-87).")] = "",
    letterhead: Annotated[str, typer.Option("--letterhead")] = "",
    footer: Annotated[str, typer.Option("--footer")] = "",
    no_pdf: Annotated[bool, typer.Option("--no-pdf", help="Write the HTML only.")] = False,
) -> None:
    """Render the twelve-section examination report (FR-80, AC-09)."""
    branding = report_generator.Branding(
        agency_name=agency, letterhead_line=letterhead, footer_note=footer
    )
    with _open(case) as store:
        result = report_service.generate_report(
            store, out_dir=out, branding=branding,
            tool_version=spectra.__version__, pdf=not no_pdf,
        )
    typer.echo(f"report        {result['html']}")
    typer.echo(f"  sha256      {result['html_sha256']}")
    if result["pdf"]:
        typer.echo(f"pdf           {result['pdf']}")
    typer.echo(f"findings      sha256 {result['findings_digest']}")
    typer.echo(f"conclusions   sha256 {result['conclusions_digest']}")
    summary = result["negative_summary"]
    typer.echo(f"section 7     {summary['total']} negative finding(s): "
               f"{summary['by_severity']['serious']} serious, "
               f"{summary['by_severity']['attention']} attention, "
               f"{summary['by_severity']['info']} info")
    if not result["audit_ok"]:
        typer.echo("WARNING: the audit chain does not verify; the report says so on its "
                   "first page", err=True)
    if result["pdf_error"]:
        typer.echo(f"note: {result['pdf_error']}", err=True)


@app.command("version")
def version() -> None:
    """Print the SPECTRA version."""
    typer.echo(f"spectra {spectra.__version__}")


def main() -> None:
    try:
        app()
    except HANDLED as exc:
        typer.echo(f"error: {exc}", err=True)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
