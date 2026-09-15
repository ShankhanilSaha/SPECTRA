"""Service refusals and failure paths (doc 3 §2: validate → audit → work → audit).

Complements `test_pipeline.py` (the happy path). Every refusal here must leave the case
consistent: nothing half-recorded, the audit chain intact, and the reason stated.
"""

from __future__ import annotations

import hashlib
import random
import sqlite3
from datetime import datetime, timedelta

import pytest

from spectra.core.casestore import CaseMeta, CaseStore
from spectra.core.media import MediaError, MediaTool
from spectra.plugins.dahua import DahuaPlugin
from spectra.services import ServiceError
from spectra.services import evidence as ev
from spectra.services import export as ex
from spectra.services import identify as ident
from spectra.services import parse as ps
from tests import dhavgen
from tests.media_fixtures import encode_test_stream, require_ffmpeg, split_access_units
from tests.test_audit import fixed_clock
from tests.test_media import TamperingTool

UNITS = [(b"\x00\x00\x00\x01\x65" + bytes([i]) * 60, i % 5 == 0) for i in range(10)]


@pytest.fixture
def store(tmp_path):
    case = CaseStore.create(tmp_path / "case", CaseMeta("CASE-S-1"), "examiner-1",
                            clock=fixed_clock())
    yield case
    case.close()


def usb(tmp_path, files, name="usb"):
    root = tmp_path / name
    root.mkdir()
    for filename, data in files.items():
        (root / filename).write_bytes(data)
    return root


def parsed_export(store, tmp_path, dav: bytes) -> str:
    evidence_id = ev.import_files(store, usb(tmp_path, {"a.dav": dav})).evidence_id
    ident.run_identify(store, evidence_id)
    ps.parse(store, evidence_id)
    return evidence_id


def actions(store):
    return [r.action for r in store.audit.records()]


# -- evidence ---------------------------------------------------------------------------------

def test_ingest_argument_refusals(store, tmp_path):
    (tmp_path / "dir").mkdir()
    (tmp_path / "file.img").write_bytes(bytes(512))
    with pytest.raises(ServiceError, match="use 'import files'"):
        ev.import_image(store, tmp_path / "dir", "A")
    with pytest.raises(ServiceError, match="not a directory"):
        ev.import_files(store, tmp_path / "file.img")
    with pytest.raises(ServiceError, match="no such evidence"):
        ev.evidence_row(store, "EV-404")
    assert actions(store) == ["case.create.start", "case.create.complete"]


def test_default_evidence_selection(store, tmp_path):
    with pytest.raises(ServiceError, match="no evidence yet"):
        ev.default_evidence_id(store, None)
    for name in ("a.img", "b.img"):
        (tmp_path / name).write_bytes(bytes(512))
    only = ev.import_image(store, tmp_path / "a.img", "B").evidence_id
    assert ev.default_evidence_id(store, None) == only
    ev.import_image(store, tmp_path / "b.img", "B")
    with pytest.raises(ServiceError, match="specify --evidence"):
        ev.default_evidence_id(store, None)
    assert ev.default_evidence_id(store, "EV-002") == "EV-002"


def test_embedded_hash_comparison():
    assert ev._embedded_hash_check((), "a" * 32) == {"status": "none_stored"}
    assert ev._embedded_hash_check((("md5", "a" * 32),), "a" * 32)["status"] == "match"
    assert ev._embedded_hash_check((("md5", "b" * 32),), "a" * 32)["status"] == "MISMATCH"


@pytest.mark.ewf
def test_e01_ingest_records_hash_agreement_and_corruption_as_findings(store, tmp_path):
    pytest.importorskip("pyewf", reason="pyewf (libewf-python) not installed")
    from tests.ewfgen import CHUNK, write_e01

    clip, _ = dhavgen.stream(UNITS)
    media = bytearray(random.Random(5).randbytes(CHUNK * 8))
    media[CHUNK * 3 + 17 : CHUNK * 3 + 17 + len(clip)] = clip
    media = bytes(media)

    good = ev.import_image(store, write_e01(tmp_path / "good.E01", media), "A")
    assert good.sha256 == hashlib.sha256(media).hexdigest()
    assert good.embedded_hash_check["status"] == "match" and good.source_format == "ewf"
    # Identification reads through the E01 media: DHAV frames found, no layout → carve-only.
    result = ident.run_identify(store, good.evidence_id)
    assert (result.status, result.support) == ("identified", "carve_only")

    corrupt_path = write_e01(tmp_path / "corrupt.E01", media, compress=False)
    blob = bytearray(corrupt_path.read_bytes())
    blob[blob.find(media[CHUNK * 6 + 64 : CHUNK * 6 + 96]) + 3] ^= 0xFF
    corrupt_path.write_bytes(bytes(blob))
    corrupt = ev.import_image(store, corrupt_path, "A")
    assert corrupt.embedded_hash_check["status"] == "MISMATCH"
    assert "INTEGRITY" in ev.evidence_row(store, corrupt.evidence_id)["notes"]

    unhashed = ev.import_image(store, write_e01(tmp_path / "nohash.E01", media, stored_md5=None),
                               "A")
    assert unhashed.embedded_hash_check["status"] == "none_stored"
    assert "no stored MD5" in ev.evidence_row(store, unhashed.evidence_id)["notes"]
    assert store.verify().ok


# -- identify / parse ------------------------------------------------------------------------

def test_parse_refusals(store, tmp_path):
    (tmp_path / "noise.img").write_bytes(random.Random(9).randbytes(1 << 20))
    noise = ev.import_image(store, tmp_path / "noise.img", "B").evidence_id
    with pytest.raises(ServiceError, match="has not been identified"):
        ps.parse(store, noise)
    assert ident.run_identify(store, noise).status == "unknown"
    with pytest.raises(ServiceError, match="not a recognised format family"):
        ps.parse(store, noise)

    clip, _ = dhavgen.stream(UNITS)
    evidence_id = parsed_export(store, tmp_path, clip)
    with pytest.raises(ServiceError, match="already been parsed"):
        ps.parse(store, evidence_id)
    assert ps.recording_rows(store, channel=0) and ps.recording_rows(store, channel=7) == []


def test_parse_fails_cleanly_when_evidence_no_longer_matches_its_identification(store, tmp_path):
    clip, _ = dhavgen.stream(UNITS)
    root = usb(tmp_path, {"a.dav": clip})
    evidence_id = ev.import_files(store, root).evidence_id
    ident.run_identify(store, evidence_id)
    (root / "a.dav").write_bytes(b"\x00" * len(clip))  # same size, frames gone
    with pytest.raises(ServiceError, match="no export file begins"):
        ps.parse(store, evidence_id)
    assert actions(store)[-2:] == ["parse.start", "parse.error"]
    assert store.conn.execute("SELECT COUNT(*) FROM disk_layout").fetchone()[0] == 0


def test_parse_crash_mid_enumeration_rolls_back_everything(store, tmp_path, monkeypatch):
    clip, _ = dhavgen.stream(UNITS)
    evidence_id = ev.import_files(store, usb(tmp_path, {"a.dav": clip})).evidence_id
    ident.run_identify(store, evidence_id)
    real = DahuaPlugin.enumerate

    def crash_after_first(self, src, layout, include_orphans):
        yield next(real(self, src, layout, include_orphans))
        raise RuntimeError("worker killed")

    monkeypatch.setattr(DahuaPlugin, "enumerate", crash_after_first)
    with pytest.raises(RuntimeError, match="worker killed"):
        ps.parse(store, evidence_id)
    for table in ("recording", "disk_layout"):
        assert store.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    assert actions(store)[-1] == "parse.error"
    assert store.verify().ok


# -- export ------------------------------------------------------------------------------------

def test_export_refusals_leave_no_artifacts(store, tmp_path):
    with pytest.raises(ServiceError, match="no such recording"):
        ex.export_recording(store, "REC-9999", media=None)
    clip, _ = dhavgen.stream(UNITS)
    parsed_export(store, tmp_path, clip)
    store.conn.execute("UPDATE recording SET channel = 42 WHERE id = 'REC-0001'")
    with pytest.raises(ServiceError, match="no video frames"):
        ex.export_recording(store, "REC-0001", media=None)
    assert store.conn.execute("SELECT COUNT(*) FROM artifact").fetchone()[0] == 0
    assert actions(store)[-2:] == ["export.start", "export.error"]
    assert list((store.root / "artifacts" / "tmp").iterdir()) == []


def test_export_writes_only_video_payloads_to_the_es(store, tmp_path):
    start = datetime(2026, 3, 5, 14, 32, 10)
    parts = []
    for i, (unit, key) in enumerate(UNITS):
        moment = dhavgen.pack_date(start + timedelta(milliseconds=40 * i))
        parts.append(dhavgen.frame(unit, frame_type=dhavgen.I_FRAME if key else dhavgen.P_FRAME,
                                   seq=i, date=moment, tick=40 * i,
                                   ext=dhavgen.video_ext() if key else b""))
        parts.append(dhavgen.frame(b"\xAD" * 80, frame_type=dhavgen.AUDIO, seq=i, date=moment))
    parsed_export(store, tmp_path, b"".join(parts))
    result = ex.export_recording(store, "REC-0001", media=None)
    assert result.frames == len(UNITS)
    assert result.es.path.read_bytes() == b"".join(u for u, _ in UNITS)


def test_export_out_dir_never_overwrites(store, tmp_path):
    clip, _ = dhavgen.stream(UNITS)
    parsed_export(store, tmp_path, clip)
    out = tmp_path / "out"
    out.mkdir()
    (out / "REC-0001.h264").write_bytes(b"someone else's file")
    with pytest.raises(ServiceError, match="refusing to overwrite"):
        ex.export_recording(store, "REC-0001", media=None, out_dir=out)
    assert (out / "REC-0001.h264").read_bytes() == b"someone else's file"


def _backwards_clock_dav(units):
    start = datetime(2026, 3, 5, 14, 32, 10)
    frames = []
    for i, (unit, key) in enumerate(units):
        moment = start + timedelta(milliseconds=40 * i)
        if i == len(units) // 2:
            moment -= timedelta(hours=2)  # device clock set back mid-recording (S-08)
        frames.append(dhavgen.frame(unit, frame_type=dhavgen.I_FRAME if key else dhavgen.P_FRAME,
                                    seq=i, date=dhavgen.pack_date(moment),
                                    tick=(40 * i) % 0x10000,
                                    ext=dhavgen.video_ext() if key else b""))
    return b"".join(frames)


@pytest.mark.ffmpeg
def test_export_with_backwards_device_time_keeps_es_and_records_why_no_mp4(store, tmp_path):
    ffmpeg = require_ffmpeg()
    units = split_access_units(encode_test_stream(tmp_path, "h264", frames=20), "h264")
    parsed_export(store, tmp_path, _backwards_clock_dav(units))
    result = ex.export_recording(store, "REC-0001", MediaTool(ffmpeg, store.root / "logs"))
    assert result.mp4 is None
    assert "not strictly increasing" in result.mp4_skipped_reason
    assert result.es.path.read_bytes() == b"".join(u for u, _ in units)
    complete = list(store.audit.records())[-1]
    assert complete.action == "export.complete"
    assert "not strictly increasing" in complete.params["mp4_skipped_reason"]
    assert not list((store.root / "artifacts" / "tmp").glob("export-*"))


@pytest.mark.ffmpeg
def test_export_verification_failure_is_an_audited_error_with_transcripts_kept(store, tmp_path):
    ffmpeg = require_ffmpeg()
    units = split_access_units(encode_test_stream(tmp_path, "h264", frames=15), "h264")
    dav, _ = dhavgen.stream(units)
    parsed_export(store, tmp_path, dav)
    with pytest.raises(MediaError, match="verification failed"):
        ex.export_recording(store, "REC-0001", TamperingTool(ffmpeg, store.root / "logs"))
    kinds = [row[0] for row in store.conn.execute("SELECT kind FROM artifact ORDER BY kind")]
    assert "mp4_evidence" not in kinds
    assert kinds.count("tool_log") == 2  # remux + verify transcripts, hashed into the CAS
    assert actions(store)[-1] == "export.error"
    assert not list((store.root / "artifacts" / "tmp").glob("export-*"))
    assert store.verify().ok


def test_identification_row_is_required_before_selection(store, tmp_path):
    (tmp_path / "x.img").write_bytes(bytes(512))
    evidence_id = ev.import_image(store, tmp_path / "x.img", "B").evidence_id
    with pytest.raises(ServiceError, match="has not been identified"):
        ident.select_family(store, evidence_id, "dahua", "reason")
    with pytest.raises(sqlite3.IntegrityError):  # schema keeps identification tied to evidence
        store.conn.execute("INSERT INTO identification (evidence_id) VALUES ('EV-404')")
