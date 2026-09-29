"""CLI smoke test — every step reachable headless (doc 3 §2, doc 7 §5)."""

from __future__ import annotations

import functools
import json
import sqlite3
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

import spectra
from spectra.cli import _pointer_file, app, main
from spectra.core.media import locate_ffmpeg
from spectra.plugins.dahua import DahuaPlugin
from spectra.services import identify as identify_service
from spectra.services.evidence import IngestResult
from tests import dhavgen
from tests.media_fixtures import encode_test_stream, split_access_units
from tests.test_identify import CrashingPlugin
from tests.test_pipeline import OtherFamily


@pytest.fixture
def cli(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("SPECTRA_OPERATOR", "si.kumar")
    monkeypatch.setenv("SPECTRA_FFMPEG", str(tmp_path / "no-ffmpeg-here"))
    monkeypatch.delenv("SPECTRA_CASE", raising=False)
    runner = CliRunner()

    def run(*args: str, code: int = 0):
        result = runner.invoke(app, list(args))
        assert result.exit_code == code, (result.output, result.exception)
        return result.output

    return run


def test_case_to_export_via_cli(tmp_path, cli):
    case_dir = tmp_path / "CASE-2026-0142"
    usb = tmp_path / "usb"
    usb.mkdir()
    dav, _ = dhavgen.stream([(b"\x00\x00\x00\x01\x65" + bytes([i]) * 40, i == 0) for i in range(8)],
                            channel=1)
    (usb / "ch2.dav").write_bytes(dav)

    assert "created case" in cli("case", "new", "--id", "CASE-2026-0142", "--dir", str(case_dir),
                                 "--title", "Market Rd CCTV", "--examiner", "A. Examiner")
    assert "current case" in cli("case", "open", str(case_dir))
    assert "ingested EV-001" in cli("import", "files", "--dir", str(usb))
    out = cli("identify")
    assert "IDENTIFIED" in out and "candidate dahua" in out and "44 48 41 56" in out
    identified = json.loads(cli("identify", "--json"))
    assert identified["selected_family"] == "dahua"
    assert "1 recording(s)" in cli("parse")
    listing = cli("list", "recordings")
    assert "REC-0001" in listing and "(device-local)" in listing
    out = cli("export", "clip", "--recording", "REC-0001", "--out", str(tmp_path / "out"))
    assert "MP4  not produced: FFmpeg not available" in out
    assert "VERIFIED" in cli("case", "verify")
    info = json.loads(cli("case", "info", "--json"))
    assert info["evidence"][0]["provenance_class"] == "D"


def test_every_step_the_desktop_ui_drives_has_json_output(tmp_path, cli):
    """The desktop UI is a client of this CLI (doc 3 §2): it runs each step with --json
    and renders what comes back, so every step it drives must answer in JSON."""
    case_dir = tmp_path / "CASE-UI"
    as_case = ("--case", str(case_dir), "--json")
    created = json.loads(cli("case", "new", "--id", "CASE-UI", "--dir", str(case_dir), "--json"))
    assert created["case_id"] == "CASE-UI" and created["audit_head"]["seq"] >= 1

    memo = tmp_path / "panchnama.txt"
    memo.write_text("seizure memo", "utf-8")
    attached = json.loads(cli("case", "attach", "--file", str(memo), "--kind", "panchnama",
                              *as_case))
    assert attached["kind"] == "panchnama" and len(attached["sha256"]) == 64

    ingest = json.loads(cli("import", "files", "--dir", str(_dav_usb(tmp_path)), *as_case))
    assert ingest["evidence_id"] == "EV-001" and len(ingest["sha256"]) == 64
    custody = json.loads(cli("case", "custody", "--from", "SI Kumar", "--to", "Lab",
                             "--purpose", "examination", *as_case))
    assert custody["entry"]["to_holder"] == "Lab"

    cli("identify", *as_case)
    audit_len = json.loads(cli("case", "info", *as_case))["audit_head"]["seq"]
    shown = json.loads(cli("identify", "show", *as_case))
    assert shown["support"] == "parse" and shown["selected_family"] == "dahua"
    assert shown["candidates"][0]["matches"], "the matched bytes are what the panel shows"
    after = json.loads(cli("case", "info", *as_case))["audit_head"]["seq"]
    assert after == audit_len, "showing a stored identification must not write to the audit"

    parsed = json.loads(cli("parse", *as_case))
    assert parsed["recordings"] == 1 and parsed["family"] == "dahua"
    exported = json.loads(cli("export", "clip", "--recording", "REC-0001", *as_case))
    assert exported["mp4"] is None and "FFmpeg" in exported["mp4_skipped_reason"]
    artifacts = json.loads(cli("list", "artifacts", "--recording", "REC-0001", *as_case))
    assert {a["kind"] for a in artifacts} == {"es", "frame_index"}
    assert all(Path(a["path"]).is_file() for a in artifacts)

    progress = json.loads(cli("case", "info", *as_case))["progress"]
    step = progress["evidence"][0]
    assert [d["kind"] for d in progress["attachments"]] == ["panchnama"]
    assert progress["exported_recordings"] == 1
    assert step["custody_entries"] == 1 and step["parsed"] and not step["recovered"]
    assert step["identification"]["support"] == "parse"
    assert step["recordings"]["by_tier"]["T1"] == 1
    assert step["time_observations"] == 0 and step["recordings"]["with_reference_time"] == 0

    timed = json.loads(cli("time", "set", "--method", "B", "--device-time",
                           "2026-03-05T14:22:00", "--true-time", "2026-03-05T14:04:18Z",
                           *as_case))
    assert timed["recordings_normalised"] == 1
    recovered = json.loads(cli("recover", "--tiers", "T3", *as_case))

    report = json.loads(cli("report", "generate", "--out", str(tmp_path / "report"),
                            "--no-pdf", *as_case))
    assert Path(report["html"]).is_file() and report["pdf"] is None and report["audit_ok"]

    progress = json.loads(cli("case", "info", *as_case))["progress"]
    step = progress["evidence"][0]
    assert step["time_observations"] == 1 and step["recordings"]["with_reference_time"] == 1
    assert step["recovered"] and step["last_recover"]["tiers_run"] == recovered["tiers_run"]
    assert progress["reports_generated"] == 1 and progress["last_report_utc"]


def _dav_usb(tmp_path, name="usb", frames=8):
    root = tmp_path / name
    root.mkdir()
    dav, _ = dhavgen.stream([(b"\x00\x00\x00\x01\x65" + bytes([i]) * 40, i == 0)
                             for i in range(frames)])
    (root / "a.dav").write_bytes(dav)
    return root


def test_main_turns_refusals_into_exit_code_2_on_stderr(tmp_path, cli, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["spectra", "case", "info"])
    with pytest.raises(SystemExit) as exit_info:
        main()
    assert exit_info.value.code == 2
    assert "error: no case selected" in capsys.readouterr().err


def test_case_is_resolved_from_spectra_case_env(tmp_path, cli, monkeypatch):
    case_dir = tmp_path / "case"
    cli("case", "new", "--id", "C-ENV", "--dir", str(case_dir), "--agency", "Cyber Cell")
    monkeypatch.setenv("SPECTRA_CASE", str(case_dir))
    cli("import", "files", "--dir", str(_dav_usb(tmp_path)))
    out = cli("case", "info")
    assert "case C-ENV" in out and "agency: Cyber Cell" in out and "audit head: seq 3" in out
    assert "EV-001  export_files  class D" in out
    assert "identify: not identified · parsed: no" in out


def test_case_verify_prints_warnings(tmp_path, cli):
    case_dir = tmp_path / "case"
    cli("case", "new", "--id", "C-W", "--dir", str(case_dir))
    conn = sqlite3.connect(case_dir / "case.db")
    conn.execute("DROP TRIGGER audit_no_delete")  # still a valid chain, but unprotected
    conn.commit()
    conn.close()
    out = cli("case", "verify", "--case", str(case_dir))
    assert "WARNING: append-only triggers on the audit table are missing" in out
    assert "VERIFIED" in out


def test_pointer_file_location_follows_the_platform(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert _pointer_file() == tmp_path / "xdg" / "spectra" / "current_case"
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    assert _pointer_file() == tmp_path / "roaming" / "spectra" / "current_case"


def test_identify_output_for_unknown_evidence_shows_observations_and_probe_errors(
    tmp_path, cli, monkeypatch
):
    case_dir = tmp_path / "case"
    cli("case", "new", "--id", "C-2", "--dir", str(case_dir))
    boot = bytearray(1 << 20)
    boot[3:11] = b"NTFS    "
    (tmp_path / "ntfs.img").write_bytes(bytes(boot))
    cli("import", "image", "--file", str(tmp_path / "ntfs.img"), "--provenance", "B",
        "--case", str(case_dir))
    monkeypatch.setattr(
        "spectra.cli.identify_service.run_identify",
        functools.partial(identify_service.run_identify, plugins=(CrashingPlugin, DahuaPlugin)),
    )
    out = cli("identify", "--case", str(case_dir))
    assert "EV-001: UNKNOWN  (support: none)" in out
    assert "NTFS boot sector" in out
    assert "PROBE ERROR in crashy 0.0.0" in out
    assert "No format family matched" in out
    assert "no recordings" in cli("list", "recordings", "--case", str(case_dir))


def test_ambiguous_identification_and_selection_via_cli(tmp_path, cli, monkeypatch):
    case_dir = tmp_path / "case"
    cli("case", "new", "--id", "C-3", "--dir", str(case_dir))
    cli("import", "files", "--dir", str(_dav_usb(tmp_path)), "--case", str(case_dir))
    monkeypatch.setattr(
        "spectra.cli.identify_service.run_identify",
        functools.partial(identify_service.run_identify, plugins=(DahuaPlugin, OtherFamily)),
    )
    out = cli("identify", "--case", str(case_dir))
    assert "AMBIGUOUS" in out and "Nothing was selected" in out
    out = cli("identify", "select", "--family", "dahua", "--reason", "DHAV pairs at file start",
              "--case", str(case_dir))
    assert "selected dahua (parse_supported True)" in out
    cli("parse", "--case", str(case_dir))
    rows = json.loads(cli("list", "recordings", "--json", "--case", str(case_dir)))
    assert rows[0]["id"] == "REC-0001" and rows[0]["recovery_tier"] == "T1"


def test_ingest_warnings_are_printed(tmp_path, cli, monkeypatch):
    case_dir = tmp_path / "case"
    cli("case", "new", "--id", "C-4", "--dir", str(case_dir))
    (tmp_path / "x.E01").write_bytes(b"x")
    results = iter([
        IngestResult("EV-001", "m", "s", 1, ((512, 512),),
                     {"status": "MISMATCH", "stored_md5": "a", "computed_md5": "b"}, "ewf"),
        IngestResult("EV-002", "m", "s", 1, (), {"status": "none_stored"}, "ewf"),
    ])
    monkeypatch.setattr("spectra.cli.evidence_service.import_image",
                        lambda *args, **kwargs: next(results))
    args = ("import", "image", "--file", str(tmp_path / "x.E01"), "--provenance", "A",
            "--case", str(case_dir))
    out = cli(*args)
    assert "1 unreadable range(s)" in out and "does not match its media" in out
    assert "stores no MD5" in cli(*args)


def test_version(cli):
    assert cli("version").strip() == f"spectra {spectra.__version__}"


REAL_FFMPEG = locate_ffmpeg()


@pytest.mark.ffmpeg
def test_export_clip_reports_verified_mp4(tmp_path, cli, monkeypatch):
    if REAL_FFMPEG is None:
        pytest.skip("FFmpeg not available (set SPECTRA_FFMPEG or put ffmpeg on PATH)")
    monkeypatch.setenv("SPECTRA_FFMPEG", str(REAL_FFMPEG))
    units = split_access_units(encode_test_stream(tmp_path, "h264", frames=12), "h264")
    dav, _ = dhavgen.stream(units)
    (tmp_path / "usb").mkdir()
    (tmp_path / "usb" / "clip.dav").write_bytes(dav)
    case_dir = tmp_path / "case"
    cli("case", "new", "--id", "C-5", "--dir", str(case_dir))
    for args in (("import", "files", "--dir", str(tmp_path / "usb")), ("identify",), ("parse",)):
        cli(*args, "--case", str(case_dir))
    out = cli("export", "clip", "--recording", "REC-0001", "--case", str(case_dir))
    assert "MP4  sha256" in out and "VCL NAL units verified identical" in out


def test_case_verify_names_the_tampered_record(tmp_path, cli):
    case_dir = tmp_path / "case"
    cli("case", "new", "--id", "C-1", "--dir", str(case_dir))
    image = tmp_path / "disk.img"
    image.write_bytes(bytes(2048))
    cli("import", "image", "--file", str(image), "--provenance", "a", "--case", str(case_dir))

    conn = sqlite3.connect(case_dir / "case.db")
    conn.execute("DROP TRIGGER audit_no_update")
    conn.execute("UPDATE audit SET operator='someone-else' WHERE seq=2")
    conn.commit()
    conn.close()

    out = cli("case", "verify", "--case", str(case_dir), code=1)
    assert "broken at seq 2" in out and "VERIFICATION FAILED" in out
    out = cli("verify", "chain", "--case", str(case_dir), "--json", code=1)
    assert json.loads(out)["broken_at_seq"] == 2
