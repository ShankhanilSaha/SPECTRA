"""End-to-end through the services: ingest → identify → parse → export → verify.

Phase 3 done-criterion on the Tier E path (doc 6 §3.3; CLAUDE.md §13.1): a `.dav` export →
playable evidence MP4 whose ES hash equals the reference ES (TC-PS-04), VCL-identical after
remux (TC-PS-05, AC-05), with per-frame device timing as PTS (TC-PS-06). The `.dav` files are
built by the parser author from real x264/x265 output (spec conformance, not Tier S/E truth).
"""

from __future__ import annotations

import hashlib
import json
from typing import ClassVar

import pytest

from spectra.core.casestore import CaseMeta, CaseStore
from spectra.core.media import MediaTool
from spectra.core.models import ProbeResult, SignatureMatch
from spectra.services import ServiceError
from spectra.services import evidence as ev
from spectra.services import export as ex
from spectra.services import identify as ident
from spectra.services import parse as ps
from tests import dhavgen
from tests.media_fixtures import encode_test_stream, require_ffmpeg, split_access_units
from tests.test_audit import fixed_clock


def new_case(tmp_path, name="case"):
    return CaseStore.create(tmp_path / name, CaseMeta("CASE-T-1", title="pipeline"),
                            "examiner-1", clock=fixed_clock())


def write_export(tmp_path, files):
    root = tmp_path / "usb"
    root.mkdir(exist_ok=True)
    for name, data in files.items():
        (root / name).write_bytes(data)
    return root


@pytest.mark.ffmpeg
@pytest.mark.parametrize("codec", ["h264", "h265"])
def test_dav_export_to_verified_evidence_mp4(tmp_path, codec):
    ffmpeg = require_ffmpeg()
    reference = encode_test_stream(tmp_path, codec, frames=75)
    units = split_access_units(reference, codec)
    codec_id = dhavgen.CODEC_H264 if codec == "h264" else dhavgen.CODEC_H265
    dav, expected_pts = dhavgen.stream(units, channel=4, codec_id=codec_id, jitter_every=5)
    usb = write_export(tmp_path, {"NVR_ch5_main_20260305143210.dav": dav})

    store = new_case(tmp_path)
    ingest = ev.import_files(store, usb, label="owner USB stick")
    assert ingest.sha256 == hashlib.sha256(dav).hexdigest()

    result = ident.run_identify(store, ingest.evidence_id)
    assert result.status == "identified"
    assert (result.support, result.selected.family) == ("parse", "dahua")

    summary = ps.parse(store, ingest.evidence_id)
    assert (summary.recordings, summary.channels) == (1, (4,))
    (rec,) = ps.recording_rows(store, ingest.evidence_id)
    assert (rec["codec"], rec["frame_count"], rec["t_method"]) == (codec, 75, "none")
    assert rec["t_local_start"] == "2026-03-05T14:32:10"

    media = MediaTool(ffmpeg, store.root / "logs")
    exported = ex.export_recording(store, rec["id"], media, out_dir=tmp_path / "out")
    # TC-PS-04: extracted ES is byte-identical to the stream that was framed into DHAV.
    assert exported.es.sha256 == hashlib.sha256(reference).hexdigest()
    # TC-PS-05 / AC-05: the MP4 carries the same VCL NAL units, verified at export.
    assert exported.mp4 is not None and exported.vcl["result"] == "identical"
    # TC-PS-06: PTS follow the device's own frame timing, jitter included.
    pts = media.probe_pts(exported.mp4.path)
    assert [round((p - pts[0]) * 1000) for p in pts] == expected_pts

    index = [json.loads(line) for line in exported.frame_index.path.read_text().splitlines()]
    assert index[0]["t_device_local"] == "2026-03-05T14:32:10" and index[0]["key"]
    manifest = json.loads((tmp_path / "out" / f"{rec['id']}.manifest.json").read_text())
    assert manifest["files"][f"{rec['id']}.mp4"]["sha256"] == exported.mp4.sha256

    verification = store.verify()
    assert verification.ok, verification.problems
    actions = [r.action for r in store.audit.records()]
    assert actions == [
        "case.create.start", "case.create.complete",
        "import.files.start", "import.files.complete",
        "identify.start", "identify.complete",
        "parse.start", "parse.complete",
        "export.start", "export.complete",
    ]
    complete = list(store.audit.records())[-1]
    assert complete.params["es"]["sha256"] == exported.es.sha256
    assert complete.params["mp4"]["sha256"] == exported.mp4.sha256
    assert complete.hash_after == exported.mp4.sha256
    store.close()


@pytest.mark.ffmpeg
def test_two_independent_runs_produce_identical_artefacts(tmp_path):
    """NFR-08 / AC-11 for the export path: same input + version ⇒ same bytes."""
    ffmpeg = require_ffmpeg()
    units = split_access_units(encode_test_stream(tmp_path, "h264", frames=30), "h264")
    dav, _ = dhavgen.stream(units)
    usb = write_export(tmp_path, {"clip.dav": dav})
    hashes = []
    for run in ("a", "b"):
        store = new_case(tmp_path, f"case-{run}")
        evidence_id = ev.import_files(store, usb).evidence_id
        ident.run_identify(store, evidence_id)
        ps.parse(store, evidence_id)
        out = ex.export_recording(store, "REC-0001", MediaTool(ffmpeg, store.root / "logs"))
        hashes.append((out.es.sha256, out.frame_index.sha256, out.mp4.sha256))
        store.close()
    assert hashes[0] == hashes[1]


def test_export_without_ffmpeg_keeps_the_es_and_says_why(tmp_path):
    units = [(b"\x00\x00\x00\x01\x65" + bytes([i]) * 90, i == 0) for i in range(12)]
    dav, _ = dhavgen.stream(units)
    store = new_case(tmp_path)
    evidence_id = ev.import_files(store, write_export(tmp_path, {"a.dav": dav})).evidence_id
    ident.run_identify(store, evidence_id)
    ps.parse(store, evidence_id)
    result = ex.export_recording(store, "REC-0001", media=None)
    assert result.mp4 is None and "FFmpeg not available" in result.mp4_skipped_reason
    assert result.es.path.read_bytes() == b"".join(u for u, _ in units)
    last = list(store.audit.records())[-1]
    assert last.action == "export.complete" and last.params["mp4"] is None
    assert store.verify().ok
    store.close()


def test_raw_dahua_disk_is_carve_only_and_parse_is_refused(tmp_path):
    body, _ = dhavgen.stream([(b"\x00\x00\x01\x65" + bytes(80), True)] * 10)
    disk = bytearray(3 << 20)
    disk[(1 << 20) + 5 : (1 << 20) + 5 + len(body)] = body
    path = tmp_path / "pulled-disk.img"
    path.write_bytes(bytes(disk))
    store = new_case(tmp_path)
    evidence_id = ev.import_image(store, path, "B", note="test").evidence_id
    result = ident.run_identify(store, evidence_id)
    assert (result.status, result.support) == ("identified", "carve_only")
    with pytest.raises(ServiceError, match="carve-only"):
        ps.parse(store, evidence_id)
    actions = [r.action for r in store.audit.records()]
    assert actions[-2:] == ["identify.start", "identify.complete"]  # refusal wrote nothing
    store.close()


class OtherFamily:
    family: ClassVar[str] = "otherfam"
    layout_versions: ClassVar[tuple[str, ...]] = ("x",)
    plugin_version: ClassVar[str] = "0.0.1"

    @classmethod
    def probe(cls, src, plan):
        return ProbeResult(cls.family, None, 0.6, False,
                           (SignatureMatch(0, src.read(0, 4), "test"),), cls.plugin_version)


def test_ambiguous_identification_blocks_parse_until_audited_selection(tmp_path):
    from spectra.plugins.dahua import DahuaPlugin

    dav, _ = dhavgen.stream([(b"\x00\x00\x01\x65" + bytes(20), True)] * 3)
    store = new_case(tmp_path)
    evidence_id = ev.import_files(store, write_export(tmp_path, {"a.dav": dav})).evidence_id
    result = ident.run_identify(store, evidence_id, plugins=(DahuaPlugin, OtherFamily))
    assert result.status == "ambiguous"
    with pytest.raises(ServiceError, match="ambiguous"):
        ps.parse(store, evidence_id)
    with pytest.raises(ServiceError, match="not a candidate"):
        ident.select_family(store, evidence_id, "hikvision", "chassis says Hikvision")
    with pytest.raises(ServiceError, match="reason is required"):
        ident.select_family(store, evidence_id, "dahua", "  ")
    ident.select_family(store, evidence_id, "dahua", "DHAV header+trailer pairs at file starts")
    assert ps.parse(store, evidence_id).recordings == 1
    records = list(store.audit.records())
    assert "identify.ambiguous" in [r.action for r in records]
    select = next(r for r in records if r.action == "identify.select.start")
    assert select.params["reason"] == "DHAV header+trailer pairs at file starts"
    store.close()


def test_import_image_requires_stated_provenance_and_detects_changed_evidence(tmp_path):
    path = tmp_path / "img.dd"
    path.write_bytes(bytes(4096))
    store = new_case(tmp_path)
    with pytest.raises(ServiceError, match="provenance class"):
        ev.import_image(store, path, "")
    evidence_id = ev.import_image(store, path, "A").evidence_id
    with pytest.raises(ServiceError, match="already ingested"):
        ev.import_image(store, path, "A")
    path.write_bytes(bytes(8192))  # the image changed after ingest
    with pytest.raises(ServiceError, match="has changed or moved"):
        ev.open_evidence(store, evidence_id)
    store.close()
