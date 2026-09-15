"""CLI integration tests for spectra analyze motion command (doc 3 §2, doc 7 §5)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from spectra.cli import app
from tests import dhavgen


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


def setup_case(tmp_path: Path, cli) -> tuple[Path, str]:
    case_dir = tmp_path / "CASE-CLI-AN"
    usb = tmp_path / "usb"
    usb.mkdir(exist_ok=True)
    dav, _ = dhavgen.stream(
        [(b"\x00\x00\x00\x01\x65" + bytes([(i * 15) % 256]) * 40, i == 0) for i in range(10)],
        channel=0,
    )
    (usb / "clip.dav").write_bytes(dav)

    cli("case", "new", "--id", "CASE-CLI-AN", "--dir", str(case_dir))
    cli("case", "open", str(case_dir))
    cli("import", "files", "--dir", str(usb), "--label", "usb")
    cli("identify")
    cli("parse")

    rec_list = cli("list", "recordings", "--json")
    recordings = json.loads(rec_list)
    rec_id = recordings[0]["id"]
    return case_dir, rec_id


def test_cli_analyze_motion_text_output(tmp_path: Path, cli):
    case_dir, rec_id = setup_case(tmp_path, cli)

    out = cli("analyze", "motion", rec_id, "--case", str(case_dir), "--sensitivity", "15")
    assert f"recording: {rec_id}" in out
    assert "total frames:  10" in out
    assert "annotations:" in out
    assert "persisted in case.db" in out


def test_cli_analyze_motion_json_output(tmp_path: Path, cli):
    case_dir, rec_id = setup_case(tmp_path, cli)

    out = cli("analyze", "motion", rec_id, "--case", str(case_dir), "--json")
    data = json.loads(out)
    assert data["recording_id"] == rec_id
    assert data["total_frames"] == 10
    assert "motion_frames" in data
    assert "segments" in data
    assert "annotations_count" in data


def test_cli_analyze_motion_error_on_bad_id(tmp_path: Path, cli):
    case_dir, _ = setup_case(tmp_path, cli)

    cli("analyze", "motion", "bad-rec-id", "--case", str(case_dir), code=1)
