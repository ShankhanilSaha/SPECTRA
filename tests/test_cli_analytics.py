"""CLI integration for `spectra analyze motion` (doc 3 §2, doc 7 §5, FR-90, FR-96).

Uses real encoded video, because the command's value depends on it having decoded
something. See `tests/test_services_analytics.py` for why that is the property under test.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from spectra.cli import app
from tests import dhavgen
from tests.media_fixtures import (
    STATIC_SOURCE,
    encode_test_stream,
    require_ffmpeg,
    split_access_units,
)


@pytest.fixture
def cli(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("SPECTRA_OPERATOR", "si.kumar")
    monkeypatch.delenv("SPECTRA_CASE", raising=False)
    runner = CliRunner()

    def run(*args: str, code: int = 0) -> str:
        result = runner.invoke(app, list(args))
        assert result.exit_code == code, (result.output, result.exception)
        return result.output

    return run


def setup_case(tmp_path: Path, cli, *, source: str | None = None) -> tuple[Path, str]:
    case_dir = tmp_path / "CASE-CLI-AN"
    usb = tmp_path / "usb"
    usb.mkdir(exist_ok=True)

    kwargs = {"source": source} if source else {}
    reference = encode_test_stream(tmp_path, "h264", frames=50, **kwargs)
    units = split_access_units(reference, "h264")
    dav, _ = dhavgen.stream(units, channel=0, codec_id=dhavgen.CODEC_H264)
    (usb / "clip.dav").write_bytes(dav)

    cli("case", "new", "--id", "CASE-CLI-AN", "--dir", str(case_dir))
    cli("case", "open", str(case_dir))
    cli("import", "files", "--dir", str(usb), "--label", "usb")
    cli("identify")
    cli("parse")

    recordings = json.loads(cli("list", "recordings", "--json"))
    return case_dir, recordings[0]["id"]


@pytest.mark.ffmpeg
def test_cli_analyze_motion_text_output(tmp_path, cli):
    require_ffmpeg()
    case_dir, rec_id = setup_case(tmp_path, cli)
    out = cli("analyze", "motion", rec_id, "--case", str(case_dir))
    assert "total frames:" in out
    assert "motion frames:" in out
    assert "persisted in case.db" in out


@pytest.mark.ffmpeg
def test_the_command_states_that_hits_are_leads_not_identifications(tmp_path, cli):
    """FR-96 — the framing travels with the output, not just the report."""
    require_ffmpeg()
    case_dir, rec_id = setup_case(tmp_path, cli)
    out = cli("analyze", "motion", rec_id, "--case", str(case_dir))
    assert "Not identifications." in out
    assert "sampled at" in out, "a sampled analysis must say that it sampled"


@pytest.mark.ffmpeg
def test_cli_analyze_motion_json_output(tmp_path, cli):
    require_ffmpeg()
    case_dir, rec_id = setup_case(tmp_path, cli)
    data = json.loads(cli("analyze", "motion", rec_id, "--case", str(case_dir), "--json"))
    assert data["recording_id"] == rec_id
    assert data["total_frames"] > 0
    assert data["sampled_fps"] > 0
    assert "decode_note" in data
    assert isinstance(data["segments"], list)


@pytest.mark.ffmpeg
def test_a_static_scene_reports_no_motion_through_the_cli(tmp_path, cli):
    require_ffmpeg()
    case_dir, rec_id = setup_case(tmp_path, cli, source=STATIC_SOURCE)
    data = json.loads(cli("analyze", "motion", rec_id, "--case", str(case_dir), "--json"))
    assert data["total_frames"] > 0
    assert data["motion_frames"] == 0
    assert data["segments"] == []


def test_cli_analyze_motion_unknown_recording(tmp_path, cli):
    case_dir = tmp_path / "CASE-CLI-AN2"
    cli("case", "new", "--id", "CASE-CLI-AN2", "--dir", str(case_dir))
    cli("analyze", "motion", "REC-9999", "--case", str(case_dir), code=1)
