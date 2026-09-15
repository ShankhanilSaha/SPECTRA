"""Tests for Tier S Synthetic Corpus Generator (tools/make_corpus.py).

Verifies conformance to doc 6 §3.1, §3.4, and oracles for TC-ID-01, TC-ID-02, TC-ID-03, TC-AQ-07.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from spectra.core.source import RawImageSource
from spectra.identify.engine import identify
from spectra.plugins.dahua import DahuaPlugin
from tests.test_identify import FakeHikPlugin
from tools.make_corpus import CorpusBuilder


@pytest.fixture(scope="module")
def corpus_dir(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("tier_s_corpus")
    builder = CorpusBuilder(out)
    builder.build_all()
    return out


def test_all_tier_s_fixtures_generated(corpus_dir: Path):
    expected_fixtures = [
        "S-01", "S-02", "S-03", "S-04", "S-05", "S-06",
        "S-07", "S-08", "S-09", "S-10", "S-11",
    ]
    for fid in expected_fixtures:
        fixture_path = corpus_dir / fid
        assert fixture_path.is_dir(), f"Missing fixture directory {fid}"
        gt_path = fixture_path / "ground_truth.json"
        assert gt_path.is_file(), f"Missing ground_truth.json for {fid}"

        # Parse and validate ground truth schema
        gt = json.loads(gt_path.read_text(encoding="utf-8"))
        assert gt["fixture_id"] == fid
        assert "description" in gt
        assert "sha256" in gt and len(gt["sha256"]) == 64
        assert "md5" in gt and len(gt["md5"]) == 32
        assert gt["size_bytes"] > 0
        assert "oracles" in gt


def test_tc_id_01_s01_identified_as_dahua(corpus_dir: Path):
    img_path = corpus_dir / "S-01" / "s01_dahua_baseline.raw"
    src = RawImageSource(img_path)
    res = identify(src)
    assert res.status == "identified"
    assert res.selected is not None
    assert res.selected.family == "dahua"
    # Unverified superblock means raw disk drops to carving
    assert res.support == "carve_only"


def test_tc_id_02_s10_ambiguity_oracle(corpus_dir: Path):
    img_path = corpus_dir / "S-10" / "s10_ambiguous.raw"
    src = RawImageSource(img_path)
    res = identify(src, plugins=(DahuaPlugin, FakeHikPlugin))
    assert res.status == "ambiguous"
    assert res.selected is None
    assert res.support == "pending_selection"
    assert {c.family for c in res.candidates} == {"dahua", "hikvision_test"}


def test_tc_id_03_s11_empty_disk_oracle(corpus_dir: Path):
    img_path = corpus_dir / "S-11" / "s11_empty_disk.raw"
    src = RawImageSource(img_path)
    res = identify(src)
    assert res.status == "unknown"
    assert res.selected is None
    assert res.support == "none"
    assert len(res.candidates) == 0


def test_tc_aq_07_s06_bad_sector_oracle(corpus_dir: Path):
    gt_path = corpus_dir / "S-06" / "ground_truth.json"
    gt = json.loads(gt_path.read_text(encoding="utf-8"))
    assert gt["oracles"]["bad_sector_count"] > 0
    assert len(gt["oracles"]["bad_lbas"]) == gt["oracles"]["bad_sector_count"]
    for off, length in gt["oracles"]["bad_ranges"]:
        assert length == 512
        assert off % 512 == 0


def test_s07_clock_offset_oracle(corpus_dir: Path):
    gt_path = corpus_dir / "S-07" / "ground_truth.json"
    gt = json.loads(gt_path.read_text(encoding="utf-8"))
    assert gt["oracles"]["offset_seconds"] == 1062
    assert gt["oracles"]["device_timezone"] == "UTC+05:30"
