"""RecoverService end to end: tiers → merge → coverage map (FR-29, FR-41..FR-46, AC-06).

The first tests that exercise the `coverage` table, which until now nothing wrote to.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from spectra.core.casestore import CaseMeta, CaseStore
from spectra.services import ServiceError
from spectra.services import evidence as ev
from spectra.services import identify as ident
from spectra.services import parse as ps
from spectra.services import recover as rc
from tests import hikgen
from tests.test_audit import fixed_clock

RECS = [
    (1, datetime(2026, 3, 5, 14, 0, 0), datetime(2026, 3, 5, 14, 30, 0)),
    (2, datetime(2026, 3, 5, 14, 5, 0), datetime(2026, 3, 5, 14, 35, 0)),
]


def new_case(tmp_path: Path, name: str = "case") -> CaseStore:
    return CaseStore.create(
        tmp_path / name, CaseMeta("CASE-R-1", title="recovery"), "examiner-1",
        clock=fixed_clock(),
    )


def seeded(tmp_path: Path, image: bytes, *, do_parse: bool = True) -> tuple[CaseStore, str]:
    """A case with one Hikvision image, identified and optionally parsed."""
    path = tmp_path / "hik.raw"
    path.write_bytes(image)
    store = new_case(tmp_path)
    ingest = ev.import_image(store, path, "A", label="test disk")
    ident.run_identify(store, ingest.evidence_id)
    if do_parse:
        ps.parse(store, ingest.evidence_id)
    return store, ingest.evidence_id


# --- the tiers, through the service ---------------------------------------------------


def test_fr_41_t2_recovers_an_orphan_the_parse_did_not_report(tmp_path: Path) -> None:
    store, ev_id = seeded(tmp_path, hikgen.volume(recordings=RECS, orphan_indices=(1,)))

    before = ps.recording_rows(store, ev_id)
    assert len(before) == 1, "parse reports only live index entries"

    summary = rc.recover(store, ev_id, ("T2",))
    assert summary.added == 1
    assert summary.by_tier == {"T2": 1}
    assert "T2" in summary.tiers_run

    after = ps.recording_rows(store, ev_id)
    assert len(after) == 2
    assert {r["recovery_tier"] for r in after} == {"T1", "T2"}
    store.close()


def test_recovery_works_on_a_carve_only_image_where_parse_refuses(tmp_path: Path) -> None:
    """FR-03 images are exactly what T3 is for; recovery must not inherit parse's refusal."""
    store, ev_id = seeded(
        tmp_path, hikgen.volume(version=b"HIK.2099.01.01"), do_parse=False
    )
    with pytest.raises(ServiceError, match="carve-only"):
        ps.parse(store, ev_id)

    summary = rc.recover(store, ev_id, ("T3",))
    assert "T3" in summary.tiers_run
    assert any("carve-only" in n for n in summary.notes)
    store.close()


def test_t2_is_skipped_with_a_reason_when_there_is_no_index(tmp_path: Path) -> None:
    store, ev_id = seeded(
        tmp_path, hikgen.volume(version=b"HIK.2099.01.01"), do_parse=False
    )
    summary = rc.recover(store, ev_id, ("T2", "T3"))
    assert summary.skipped == ("T2 (no parseable index)",)
    assert "T3" in summary.tiers_run
    store.close()


def test_t4_restricts_the_scan_to_readable_ranges(tmp_path: Path) -> None:
    store, ev_id = seeded(tmp_path, hikgen.volume(recordings=RECS))
    summary = rc.recover(store, ev_id, ("T4",))
    assert "T4" in summary.tiers_run
    assert any("readable range" in n for n in summary.notes)
    store.close()


def test_unknown_tiers_are_rejected(tmp_path: Path) -> None:
    store, ev_id = seeded(tmp_path, hikgen.volume())
    with pytest.raises(ServiceError, match="unknown tier"):
        rc.recover(store, ev_id, ("T9",))
    with pytest.raises(ServiceError, match="no tiers requested"):
        rc.recover(store, ev_id, ())
    store.close()


# --- the coverage map (FR-29, TC-PS-07) ------------------------------------------------


def test_tc_ps_07_coverage_tiles_the_image_exactly(tmp_path: Path) -> None:
    image = hikgen.volume(recordings=RECS, orphan_indices=(1,))
    store, ev_id = seeded(tmp_path, image)
    summary = rc.recover(store, ev_id, ("T2", "T3"))

    rows = rc.coverage_rows(store, ev_id)
    assert rows, "the coverage table finally has rows in it"
    assert sum(r["length"] for r in rows) == len(image)
    assert sum(summary.coverage.values()) == len(image)

    cursor = 0
    for row in sorted(rows, key=lambda r: r["offset"]):
        assert row["offset"] == cursor, "runs must be contiguous"
        cursor += row["length"]
    assert cursor == len(image)
    store.close()


def test_coverage_records_structural_metadata_separately_from_video(tmp_path: Path) -> None:
    store, ev_id = seeded(tmp_path, hikgen.volume(recordings=RECS))
    summary = rc.recover(store, ev_id, ("T2",))
    assert summary.coverage["structural"] > 0, "both HIKBTREE copies are metadata"
    assert summary.coverage["parsed"] > 0
    store.close()


def test_unaccounted_space_is_reported_not_hidden(tmp_path: Path) -> None:
    """An image that is mostly empty must say so — that is the §7 negative finding."""
    store, ev_id = seeded(tmp_path, hikgen.volume(recordings=RECS))
    summary = rc.recover(store, ev_id, ("T2",))
    assert summary.coverage["unaccounted"] > 0
    store.close()


def test_rerunning_recovery_replaces_the_map_rather_than_doubling_it(
    tmp_path: Path,
) -> None:
    image = hikgen.volume(recordings=RECS)
    store, ev_id = seeded(tmp_path, image)
    rc.recover(store, ev_id, ("T2",))
    first = rc.coverage_rows(store, ev_id)
    rc.recover(store, ev_id, ("T2",))
    second = rc.coverage_rows(store, ev_id)
    assert sum(r["length"] for r in second) == len(image)
    assert len(second) == len(first)
    store.close()


# --- merge, yield, and honesty about what cannot be measured --------------------------


def test_fr_44_a_rerun_does_not_duplicate_recordings(tmp_path: Path) -> None:
    store, ev_id = seeded(tmp_path, hikgen.volume(recordings=RECS, orphan_indices=(1,)))
    rc.recover(store, ev_id, ("T2",))
    count_after_first = len(ps.recording_rows(store, ev_id))
    summary = rc.recover(store, ev_id, ("T2",))
    assert summary.added == 0, "the orphan is already recorded"
    assert len(ps.recording_rows(store, ev_id)) == count_after_first
    store.close()


def test_ac_06_yield_is_reported_against_the_t1_baseline(tmp_path: Path) -> None:
    store, ev_id = seeded(tmp_path, hikgen.volume(recordings=RECS, orphan_indices=(1,)))
    summary = rc.recover(store, ev_id, ("T2",))
    assert summary.t1_minutes == pytest.approx(30.0), "one 30-minute T1 recording"
    assert summary.recovered_minutes == pytest.approx(30.0), "the orphan is 30 minutes"
    assert summary.gain_pct == pytest.approx(100.0)
    store.close()


def test_items_without_a_time_are_excluded_from_the_yield_and_counted(
    tmp_path: Path,
) -> None:
    """FR-45: a carved run with no time contributes no minutes, and the report says how many."""
    store, ev_id = seeded(tmp_path, hikgen.volume(recordings=RECS))
    summary = rc.recover(store, ev_id, ("T3",))
    if summary.added:
        assert summary.items_without_time > 0
        assert any("no recoverable time" in n for n in summary.notes)
    store.close()


def test_gain_is_none_rather_than_zero_when_there_is_no_baseline(tmp_path: Path) -> None:
    store, ev_id = seeded(
        tmp_path, hikgen.volume(version=b"HIK.2099.01.01"), do_parse=False
    )
    summary = rc.recover(store, ev_id, ("T3",))
    assert summary.gain_pct is None, "no T1 baseline means the ratio is undefined, not 0%"
    store.close()


# --- audit (§15.1) ---------------------------------------------------------------------


def test_recovery_is_audited_start_and_complete(tmp_path: Path) -> None:
    store, ev_id = seeded(tmp_path, hikgen.volume(recordings=RECS, orphan_indices=(1,)))
    rc.recover(store, ev_id, ("T2",))
    actions = [
        r[0]
        for r in store.conn.execute("SELECT action FROM audit ORDER BY seq").fetchall()
    ]
    assert "recover.start" in actions
    assert "recover.complete" in actions
    assert store.audit.verify().ok, "the chain still verifies after recovery"
    store.close()


def test_summary_json_is_serialisable_and_stable(tmp_path: Path) -> None:
    import json

    store, ev_id = seeded(tmp_path, hikgen.volume(recordings=RECS, orphan_indices=(1,)))
    summary = rc.recover(store, ev_id, ("T2",))
    encoded = json.dumps(summary.to_json(), sort_keys=True)
    assert json.loads(encoded)["by_tier"] == {"T2": 1}
    store.close()
