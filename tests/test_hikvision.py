"""Hikvision family — master sector, dual HIKBTREE, orphan entries (FR-22, FR-41).

Structure under test is the published layout (Han, Jeong & Lee, ICDF2C 2015). These are
spec-conformance tests: they prove the parser implements the paper, not that the paper
matches a real recorder — that needs a Tier R image (doc 6 §3.2, doc 4 §12 rows 1-2).
"""

from __future__ import annotations

import struct
from datetime import datetime
from pathlib import Path

import pytest

import tests.hikgen as hikgen
from spectra.core.models import Extent
from spectra.core.source import RawImageSource
from spectra.identify.engine import build_plan, identify
from spectra.plugins.base import LayoutNotSupported
from spectra.plugins.hikvision import (
    RECORDING_NOW,
    HikvisionPlugin,
    decode_unix32,
    validate_pack_header,
)

RECS = [
    (1, datetime(2026, 3, 5, 14, 0, 0), datetime(2026, 3, 5, 14, 30, 0)),
    (2, datetime(2026, 3, 5, 14, 5, 0), datetime(2026, 3, 5, 14, 35, 0)),
]


def source(tmp_path: Path, data: bytes, name: str = "hik.raw") -> RawImageSource:
    path = tmp_path / name
    path.write_bytes(data)
    return RawImageSource(path)


def plan_for(src: RawImageSource):
    return build_plan(src.size)


# --- probe: identification and degrading loudly ---------------------------------------


def test_probe_identifies_a_valid_volume(tmp_path: Path) -> None:
    src = source(tmp_path, hikgen.volume(recordings=RECS))
    result = HikvisionPlugin.probe(src, plan_for(src))
    assert result is not None
    assert result.family == "hikvision"
    assert result.layout_version == "hik_2011_03_08"
    assert result.parse_supported is True
    assert result.confidence > 0.9
    assert result.matches[0].offset == 0x210, "signature sits at 0x210, not 0"


def test_probe_returns_none_on_foreign_data(tmp_path: Path) -> None:
    src = source(tmp_path, bytes(64 * 1024))
    assert HikvisionPlugin.probe(src, plan_for(src)) is None


def test_fr_03_unknown_layout_version_degrades_to_carve_only(tmp_path: Path) -> None:
    src = source(tmp_path, hikgen.volume(version=b"HIK.2099.01.01"))
    result = HikvisionPlugin.probe(src, plan_for(src))
    assert result is not None
    assert result.parse_supported is False
    assert result.layout_version is None
    assert "carve-only" in result.note


def test_fr_03_master_sector_without_a_usable_tree_is_carve_only(tmp_path: Path) -> None:
    img = bytearray(hikgen.volume())
    img[0x10000 : 0x10000 + 0x40] = bytes(0x40)  # primary
    img[0x20000 : 0x20000 + 0x40] = bytes(0x40)  # backup
    src = source(tmp_path, bytes(img))
    result = HikvisionPlugin.probe(src, plan_for(src))
    assert result is not None and result.parse_supported is False
    assert "neither HIKBTREE" in result.note


def test_identify_engine_selects_the_family(tmp_path: Path) -> None:
    src = source(tmp_path, hikgen.volume())
    result = identify(src)
    assert result.status == "identified"
    assert result.selected is not None and result.selected.family == "hikvision"
    assert result.support == "parse"


# --- superblock: both copies, divergence as a finding ---------------------------------


def test_superblock_reports_which_tree_copy_was_used(tmp_path: Path) -> None:
    src = source(tmp_path, hikgen.volume(recordings=RECS))
    layout = HikvisionPlugin().superblock(src)
    assert layout.family == "hikvision"
    assert layout.block_size == 64 * 1024
    assert layout.blocks_used == 2
    assert "HIKBTREE copy used: primary" in layout.note
    assert len(layout.index_extents) == 2, "both copies are recorded as structural"


def test_falls_back_to_the_backup_tree_and_says_so(tmp_path: Path) -> None:
    """doc 4 §4.2: index redundancy is the point, and which copy was used is reportable."""
    src = source(tmp_path, hikgen.volume(recordings=RECS, corrupt_primary=True))
    layout = HikvisionPlugin().superblock(src)
    assert "HIKBTREE copy used: backup" in layout.note
    assert "primary HIKBTREE did not validate" in layout.note


def test_superblock_refuses_rather_than_mis_parsing(tmp_path: Path) -> None:
    src = source(tmp_path, bytes(1024 * 1024))
    with pytest.raises(LayoutNotSupported, match="no Hikvision master sector"):
        HikvisionPlugin().superblock(src)
    src2 = source(tmp_path, hikgen.volume(version=b"HIK.2099.01.01"), name="b.raw")
    with pytest.raises(LayoutNotSupported, match="unknown Hikvision layout"):
        HikvisionPlugin().superblock(src2)


# --- enumerate: T1, the big-endian channel, and T2 orphans ----------------------------


def test_t1_enumeration_decodes_entries(tmp_path: Path) -> None:
    src = source(tmp_path, hikgen.volume(recordings=RECS))
    plugin = HikvisionPlugin()
    layout = plugin.superblock(src)
    recs = list(plugin.enumerate(src, layout, include_orphans=False))
    assert len(recs) == 2
    assert [r.channel for r in recs] == [1, 2]
    assert all(r.recovery_tier == "T1" for r in recs)
    assert recs[0].t_start is not None
    assert recs[0].t_start.local == datetime(2026, 3, 5, 14, 0, 0)
    assert all(len(r.extents) == 1 for r in recs)


def test_channel_is_big_endian_the_one_field_that_is(tmp_path: Path) -> None:
    """Read little-endian, channel 2 becomes 512. This test is the guard against that."""
    src = source(tmp_path, hikgen.volume(recordings=[(2, RECS[0][1], RECS[0][2])]))
    plugin = HikvisionPlugin()
    recs = list(plugin.enumerate(src, plugin.superblock(src), include_orphans=False))
    assert recs[0].channel == 2
    assert recs[0].channel != 512


def test_fr_41_orphan_entries_appear_only_when_asked_for(tmp_path: Path) -> None:
    src = source(tmp_path, hikgen.volume(recordings=RECS, orphan_indices=(1,)))
    plugin = HikvisionPlugin()
    layout = plugin.superblock(src)

    t1 = list(plugin.enumerate(src, layout, include_orphans=False))
    assert [r.channel for r in t1] == [1]
    assert all(r.recovery_tier == "T1" for r in t1)

    both = list(plugin.enumerate(src, layout, include_orphans=True))
    assert len(both) == 2
    orphan = next(r for r in both if r.recovery_tier == "T2")
    assert orphan.channel == 2
    assert orphan.confidence < 0.95, "an orphan is not as trustworthy as an index entry"
    assert any("orphaned index entry" in n for n in orphan.notes)


def test_in_progress_sentinel_is_not_read_as_a_2038_timestamp(tmp_path: Path) -> None:
    img = bytearray(hikgen.volume(recordings=RECS))
    # Overwrite the first entry's end time with the in-progress sentinel.
    entry_at = 0x11000 + 0x60
    struct.pack_into("<I", img, entry_at + 0x1C, RECORDING_NOW)
    src = source(tmp_path, bytes(img))
    plugin = HikvisionPlugin()
    recs = list(plugin.enumerate(src, plugin.superblock(src), include_orphans=False))
    assert recs[0].t_end is None
    assert any("in-progress sentinel" in n for n in recs[0].notes)


def test_tc_rb_06_a_cyclic_page_chain_terminates(tmp_path: Path) -> None:
    img = bytearray(hikgen.volume(recordings=RECS))
    struct.pack_into("<Q", img, 0x11000 + 0x20, 0x11000)  # page points at itself
    src = source(tmp_path, bytes(img))
    plugin = HikvisionPlugin()
    recs = list(plugin.enumerate(src, plugin.superblock(src), include_orphans=True))
    assert len(recs) <= 2, "a cycle must not produce unbounded output"


# --- frames and carving ---------------------------------------------------------------


def test_frames_never_invent_a_channel_or_a_timestamp(tmp_path: Path) -> None:
    """FR-45: the PS layer carries neither dependably, so both stay None."""
    src = source(tmp_path, hikgen.volume(recordings=RECS))
    plugin = HikvisionPlugin()
    layout = plugin.superblock(src)
    rec = next(iter(plugin.enumerate(src, layout, include_orphans=False)))
    frames = list(plugin.frames(src, rec.extents[0]))
    assert frames, "the fixture block holds a program stream"
    assert all(f.channel is None and f.t_device is None for f in frames)
    assert all(isinstance(f.payload, memoryview) for f in frames)
    assert frames[0].kind == "I"


def test_pack_header_validator_rejects_a_bare_pattern(tmp_path: Path) -> None:
    src = source(tmp_path, b"\x00\x00\x01\xba" + bytes(64))
    assert validate_pack_header(src, 0) is None


def test_pack_header_validator_accepts_a_real_one(tmp_path: Path) -> None:
    blob = hikgen.pack_header() + hikgen.pes_packet(b"\x00\x00\x00\x01\x65" + bytes(16))
    src = source(tmp_path, blob)
    assert validate_pack_header(src, 0) == 14


# --- NFR-12: known-good timestamp vectors ---------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (1772719200, datetime(2026, 3, 5, 14, 0, 0)),
        (946684800, datetime(2000, 1, 1, 0, 0, 0)),
        (1234567890, datetime(2009, 2, 13, 23, 31, 30)),
    ],
)
def test_nfr_12_known_good_time_vectors(raw: int, expected: datetime) -> None:
    assert decode_unix32(raw).local == expected


@pytest.mark.parametrize("raw", [0, 1, RECORDING_NOW, 0xFFFFFFFF, _MAX := 4_102_444_800])
def test_implausible_times_stay_unresolved_rather_than_clamped(raw: int) -> None:
    decoded = decode_unix32(raw)
    assert decoded.local is None
    assert decoded.raw == raw, "the raw value is always preserved"
    assert decoded.encoding == "hikvision_unix32"


# --- the corpus conflict, kept visible ------------------------------------------------


def test_tier_s_corpus_layout_diverges_from_the_published_one(tmp_path: Path) -> None:
    """S-04/S-05 are built to a different layout and are correctly NOT recognised.

    `tools/make_corpus.py` places the master magic at offset 0 and the two tree offsets as
    `<QQ` at offset 32; the published layout puts them at 0x210 and 0x298/0x2A8. The two
    share only the magic strings, so one of them does not describe a real Hikvision disk.

    Corpus independence (CLAUDE.md §15.1) forbids editing the generator to match this
    parser, and the parser follows the peer-reviewed source. This test exists so the
    conflict is visible in CI rather than discovered on a real image. Resolving it needs
    P6 and a Tier R disk; until then neither side should be quietly changed.
    """
    img = bytearray(4 * 1024 * 1024)
    img[0:18] = b"HIKVISION@HANGZHOU"
    struct.pack_into("<QQ", img, 32, 64 * 1024, 128 * 1024)
    img[64 * 1024 : 64 * 1024 + 8] = b"HIKBTREE"
    src = source(tmp_path, bytes(img), name="s04_style.raw")

    assert HikvisionPlugin.probe(src, plan_for(src)) is None, (
        "the S-04 layout is not the published one; if this ever starts passing, the two "
        "layouts have been reconciled and this test should be replaced"
    )


def test_probe_is_bounded_on_a_tiny_image(tmp_path: Path) -> None:
    for size in (0, 1, 511, 512, 0x2FF):
        src = source(tmp_path, bytes(size), name=f"t{size}.raw")
        if src.size == 0:
            continue
        assert HikvisionPlugin.probe(src, build_plan(max(1, src.size))) is None


def test_frames_on_an_empty_extent_yields_nothing(tmp_path: Path) -> None:
    src = source(tmp_path, hikgen.volume())
    assert list(HikvisionPlugin().frames(src, Extent(0, 0))) == []
