"""T2 orphan validation and cross-tier merge (FR-41, FR-44, FR-45)."""

from __future__ import annotations

import struct
from datetime import datetime
from pathlib import Path

import tests.hikgen as hikgen
from spectra.core.models import DeviceTime, Extent, Recording
from spectra.core.source import RawImageSource
from spectra.plugins.hikvision import HikvisionPlugin
from spectra.recover.merge import TIER_RANK, merge, recording_extents
from spectra.recover.orphans import recover, validate

RECS = [
    (1, datetime(2026, 3, 5, 14, 0, 0), datetime(2026, 3, 5, 14, 30, 0)),
    (2, datetime(2026, 3, 5, 14, 5, 0), datetime(2026, 3, 5, 14, 35, 0)),
]


def source(tmp_path: Path, data: bytes, name: str = "hik.raw") -> RawImageSource:
    path = tmp_path / name
    path.write_bytes(data)
    return RawImageSource(path)


def rec(
    offset: int,
    length: int,
    tier: str = "T1",
    *,
    channel: int | None = 1,
    note: str = "x",
) -> Recording:
    return Recording(
        channel=channel,
        stream="main",
        t_start=None,
        t_end=None,
        extents=(Extent(offset, length),),
        codec="h264",
        resolution=None,
        fps=None,
        size_bytes=length,
        recovery_tier=tier,  # type: ignore[arg-type]
        confidence=0.5,
        source_note=note,
    )


# --- T2 validation --------------------------------------------------------------------


def test_fr_41_a_surviving_orphan_block_is_validated_and_kept(tmp_path: Path) -> None:
    src = source(tmp_path, hikgen.volume(recordings=RECS, orphan_indices=(1,)))
    plugin = HikvisionPlugin()
    layout = plugin.superblock(src)
    out = list(recover(plugin, src, layout))

    assert len(out) == 1, "only the freed entry is this tier's business"
    kept = out[0]
    assert kept.recovery_tier == "T2"
    assert kept.channel == 2
    assert kept.confidence > 0.5
    assert "validated against block content" in kept.source_note
    assert any("T2 validation" in n for n in kept.notes)


def test_an_overwritten_block_is_downgraded_and_loses_its_metadata(
    tmp_path: Path,
) -> None:
    """The dangerous case: a stale entry pointing at a block that has been rewritten."""
    img = bytearray(hikgen.volume(recordings=RECS, orphan_indices=(1,)))
    # Blank the second data block — the entry survives, the video does not.
    data_area, block = 0x30000, 64 * 1024
    img[data_area + block : data_area + 2 * block] = bytes(block)
    src = source(tmp_path, bytes(img))

    plugin = HikvisionPlugin()
    out = list(recover(plugin, src, plugin.superblock(src)))
    assert len(out) == 1
    downgraded = out[0]
    assert downgraded.recovery_tier == "T3"
    assert downgraded.channel is None, "the entry's channel is not asserted (FR-45)"
    assert downgraded.t_start is None and downgraded.t_end is None
    assert downgraded.confidence <= 0.2
    assert any("did not corroborate" in n for n in downgraded.notes)
    assert downgraded.extents, "the bytes are still a carve candidate"


def test_inapplicable_checks_are_not_failures(tmp_path: Path) -> None:
    """Hikvision's PS payload carries no per-frame channel or time — that is not a failure.

    Downgrading every orphan because a container omits a field would discard good evidence
    and misreport the reason.
    """
    src = source(tmp_path, hikgen.volume(recordings=RECS, orphan_indices=(1,)))
    plugin = HikvisionPlugin()
    layout = plugin.superblock(src)
    orphan = next(
        r
        for r in plugin.enumerate(src, layout, include_orphans=True)
        if r.recovery_tier == "T2"
    )
    verdict = validate(plugin, src, orphan)

    results = {c.name: c.result for c in verdict.checks}
    assert results["structure"] == "pass"
    assert results["channel"] == "inapplicable"
    assert results["time"] == "inapplicable"
    assert verdict.trusted is True
    assert verdict.failed == ()
    assert "inapplicable" in verdict.explain()


def test_a_verdict_with_nothing_checkable_is_not_trusted(tmp_path: Path) -> None:
    """An entry whose block holds nothing parseable fails `structure` outright."""
    src = source(tmp_path, hikgen.volume())
    plugin = HikvisionPlugin()
    verdict = validate(plugin, src, rec(0x1000, 4096, "T2"))
    assert verdict.trusted is False
    assert [c.name for c in verdict.failed] == ["structure"]


def test_t1_entries_are_left_alone_by_the_orphan_tier(tmp_path: Path) -> None:
    src = source(tmp_path, hikgen.volume(recordings=RECS))
    plugin = HikvisionPlugin()
    assert list(recover(plugin, src, plugin.superblock(src))) == []


# --- merge ----------------------------------------------------------------------------


def test_fr_44_overlapping_results_keep_the_highest_tier() -> None:
    t1 = [rec(0, 1000, "T1", note="index")]
    t3 = [rec(200, 300, "T3", note="carve")]
    kept, stats = merge(t1, t3)
    assert len(kept) == 1
    assert kept[0].recovery_tier == "T1"
    assert stats.dropped == 1
    assert stats.groups_with_duplicates == 1


def test_fr_44_the_survivor_records_that_duplicates_existed() -> None:
    kept, _ = merge([rec(0, 1000, "T1")], [rec(10, 10, "T3")], [rec(20, 10, "T4")])
    note = " ".join(kept[0].notes)
    assert "2 overlapping result(s)" in note
    assert "T3, T4" in note
    assert "highest tier was kept" in note


def test_non_overlapping_results_all_survive() -> None:
    kept, stats = merge([rec(0, 100, "T1")], [rec(500, 100, "T3")])
    assert len(kept) == 2
    assert stats.dropped == 0
    assert stats.groups_with_duplicates == 0
    assert [r.extents[0].offset for r in kept] == [0, 500]


def test_merge_is_deterministic_regardless_of_input_order() -> None:
    a, b, c = rec(0, 100, "T3"), rec(50, 100, "T1"), rec(500, 10, "T4")
    first, s1 = merge([a, b, c])
    second, s2 = merge([c, b, a])
    third, s3 = merge([b], [c], [a])
    def shape(out: list[Recording]) -> list[tuple[str, int]]:
        return [(r.recovery_tier, r.extents[0].offset) for r in out]

    assert shape(first) == shape(second) == shape(third)
    assert s1.to_json() == s2.to_json() == s3.to_json()


def test_overlap_grouping_is_transitive() -> None:
    """A long T1 absorbs several short carves that each touch it."""
    long_t1 = rec(0, 1000, "T1")
    carves = [rec(i * 100, 50, "T3") for i in range(10)]
    kept, stats = merge([long_t1], carves)
    assert len(kept) == 1
    assert stats.dropped == 10


def test_empty_input_is_handled() -> None:
    kept, stats = merge()
    assert kept == []
    assert stats.to_json() == {"kept": 0, "dropped": 0, "groups_with_duplicates": 0}


def test_tier_rank_orders_t1_above_t4() -> None:
    assert TIER_RANK["T1"] < TIER_RANK["T2"] < TIER_RANK["T3"] < TIER_RANK["T4"]


def test_recording_extents_feeds_the_coverage_map() -> None:
    out = recording_extents([rec(100, 50), rec(0, 10)])
    assert out == [Extent(0, 10), Extent(100, 50)]


# --- the two tiers together, end to end ------------------------------------------------


def test_t1_plus_validated_t2_merge_into_a_clean_inventory(tmp_path: Path) -> None:
    src = source(tmp_path, hikgen.volume(recordings=RECS, orphan_indices=(1,)))
    plugin = HikvisionPlugin()
    layout = plugin.superblock(src)

    t1 = list(plugin.enumerate(src, layout, include_orphans=False))
    t2 = list(recover(plugin, src, layout))
    kept, stats = merge(t1, t2)

    assert [r.recovery_tier for r in kept] == ["T1", "T2"]
    assert [r.channel for r in kept] == [1, 2]
    assert stats.dropped == 0, "the two blocks do not overlap"


def test_device_time_is_preserved_through_validation(tmp_path: Path) -> None:
    src = source(tmp_path, hikgen.volume(recordings=RECS, orphan_indices=(1,)))
    plugin = HikvisionPlugin()
    out = list(recover(plugin, src, plugin.superblock(src)))
    assert isinstance(out[0].t_start, DeviceTime)
    assert out[0].t_start.encoding == "hikvision_unix32"
    assert out[0].t_start.local == RECS[1][1]


def test_cycle_safe_enumeration_does_not_stall_orphan_recovery(tmp_path: Path) -> None:
    img = bytearray(hikgen.volume(recordings=RECS, orphan_indices=(1,)))
    struct.pack_into("<Q", img, 0x11000 + 0x20, 0x11000)
    src = source(tmp_path, bytes(img))
    plugin = HikvisionPlugin()
    out = list(recover(plugin, src, plugin.superblock(src)))
    assert len(out) <= 2
