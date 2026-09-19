"""Tests for Tier S Synthetic Corpus Generator (tools/make_corpus.py).

Verifies conformance to doc 6 §3.1, §3.4, and oracles for TC-ID-01, TC-ID-02, TC-ID-03, TC-AQ-07.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from spectra.core.source import FileSetSource, RawImageSource
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


def test_s10_bytes_exercise_the_ambiguity_policy_with_a_stand_in_plugin(corpus_dir: Path):
    """The engine's refusal to auto-select, driven by a stand-in Hikvision plugin that
    recognises the Tier S layout. This does NOT show the shipping plugins meet the S-10
    oracle — `test_tc_id_02_s10_is_ambiguous_with_the_shipping_plugins` is that test."""
    img_path = corpus_dir / "S-10" / "s10_ambiguous.raw"
    src = RawImageSource(img_path)
    res = identify(src, plugins=(DahuaPlugin, FakeHikPlugin))
    assert res.status == "ambiguous"
    assert res.selected is None
    assert res.support == "pending_selection"
    assert {c.family for c in res.candidates} == {"dahua", "hikvision_test"}


@pytest.mark.xfail(
    strict=True,
    reason=(
        "known conflict: S-10 carries the Tier S Hikvision layout (master magic at offset 0), "
        "which the shipping Hikvision plugin does not recognise because it follows the "
        "published layout (0x210), so only Dahua matches. Same conflict as test_hikvision.py::"
        "test_tier_s_corpus_layout_diverges_from_the_published_one; resolving it needs P6 and "
        "a Tier R disk. Strict: once this passes, the layouts agree and the mark must go."
    ),
)
def test_tc_id_02_s10_is_ambiguous_with_the_shipping_plugins(corpus_dir: Path):
    """TC-ID-02 against the S-10 oracle with the registry the tool actually ships."""
    gt = json.loads((corpus_dir / "S-10" / "ground_truth.json").read_text(encoding="utf-8"))
    res = identify(RawImageSource(corpus_dir / "S-10" / "s10_ambiguous.raw"))
    assert res.status == gt["oracles"]["expected_status"]
    assert res.selected is None


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


def test_s08_clock_rollback_is_split_into_the_oracle_segments(corpus_dir: Path):
    """Against P6's independently built S-08: one recording per clock segment, never one
    recording that ends before it starts."""
    fixture = corpus_dir / "S-08"
    gt = json.loads((fixture / "ground_truth.json").read_text(encoding="utf-8"))
    # The fixture directory also holds ground_truth.json; the plugin reports it as a
    # non-DHAV file and does not parse it.
    src = FileSetSource(fixture)
    plugin = DahuaPlugin()
    recordings = list(plugin.enumerate(src, plugin.superblock(src), include_orphans=False))

    assert [r.frame_count for r in recordings] == [g["frame_count"] for g in gt["recordings"]]
    assert [r.t_start.local.isoformat() for r in recordings] == [
        g["t_device_start"] for g in gt["recordings"]
    ]
    assert all(r.t_end.local >= r.t_start.local for r in recordings)
    first, second = recordings
    step = (second.t_start.local - first.t_start.local).total_seconds()
    assert step == gt["oracles"]["rollback_delta_seconds"]
    (dav,) = [m for m in src.identity.members if m.path.endswith(".dav")]
    assert second.extents[0].offset - dav.extent.offset == gt["oracles"]["discontinuity_offset"]


def _ranges_in(node: object, key: str = "") -> list[list[int]]:
    """Every [offset, length] pair under a key naming byte ranges, at any depth."""
    if isinstance(node, dict):
        return [r for k, v in node.items() for r in _ranges_in(v, k)]
    if isinstance(node, list):
        if key == "extents" or key.endswith("ranges"):
            return [list(r) for r in node]
        return [r for item in node for r in _ranges_in(item, key)]
    return []


def test_every_oracle_range_is_a_real_span_of_its_image(corpus_dir: Path):
    """An oracle is only as good as its arithmetic. S-03 once listed a surviving range of
    -13086 bytes, which no parser could ever match."""
    for gt_path in sorted(corpus_dir.glob("S-*/ground_truth.json")):
        gt = json.loads(gt_path.read_text(encoding="utf-8"))
        for offset, length in _ranges_in(gt):
            assert length > 0, f"{gt['fixture_id']}: range ({offset}, {length}) is empty"
            assert 0 <= offset and offset + length <= gt["size_bytes"], (
                f"{gt['fixture_id']}: range ({offset}, {length}) lies outside the image"
            )


def test_s03_survivor_is_everything_before_the_overwrite(corpus_dir: Path):
    """The overwrite runs past the end of the S-03 recording, so exactly one piece
    survives, and it ends where the overwrite begins."""
    gt = json.loads((corpus_dir / "S-03" / "ground_truth.json").read_text(encoding="utf-8"))
    oracles = gt["oracles"]
    (survivor,) = oracles["surviving_ranges"]
    assert survivor[0] + survivor[1] == oracles["overwrite_start"]
    assert gt["recordings"][0]["surviving_ranges"] == oracles["surviving_ranges"]


def test_checked_in_oracles_match_the_generator(corpus_dir: Path):
    """tests/corpus holds only the oracles (the images are rebuilt). If the generator
    changes, the checked-in copies must be regenerated in the same change."""
    checked_in = Path(__file__).parent / "corpus"
    for generated in sorted(corpus_dir.glob("S-*/ground_truth.json")):
        fixture = generated.parent.name
        stored = checked_in / fixture / "ground_truth.json"
        assert stored.is_file(), f"{fixture}: ground_truth.json is not checked in"
        assert json.loads(stored.read_text(encoding="utf-8")) == json.loads(
            generated.read_text(encoding="utf-8")
        ), f"{fixture}: checked-in oracle is stale; rerun tools/make_corpus.py"


def test_s07_clock_offset_oracle(corpus_dir: Path):
    gt_path = corpus_dir / "S-07" / "ground_truth.json"
    gt = json.loads(gt_path.read_text(encoding="utf-8"))
    assert gt["oracles"]["offset_seconds"] == 1062
    assert gt["oracles"]["device_timezone"] == "UTC+05:30"
