"""Coverage map — the tiling invariant and the precedence contract (FR-29, TC-PS-07)."""

from __future__ import annotations

import random

import pytest

from spectra.core.models import Extent
from spectra.recover.coverage import (
    BUCKET_PRECEDENCE,
    Claim,
    CoverageMap,
    CoverageRun,
    claim,
    resolve,
)

MIB = 1024 * 1024


def _totals_sum(cov: CoverageMap) -> int:
    return sum(cov.totals().values())


# --- the invariant this module exists for (TC-PS-07, FR-29) -------------------------


def test_tc_ps_07_buckets_sum_to_image_size_exactly() -> None:
    cov = resolve(
        1000,
        [
            claim(0, 512, "structural", "master sector"),
            claim(512, 200, "parsed", "REC-0001"),
            claim(900, 50, "carved", "T3 run"),
        ],
    )
    assert _totals_sum(cov) == 1000
    assert cov.totals()["unaccounted"] == 1000 - 512 - 200 - 50


def test_tc_ps_07_empty_claim_set_is_entirely_unaccounted() -> None:
    cov = resolve(4096, [])
    assert cov.runs == (CoverageRun(0, 4096, "unaccounted"),)
    assert cov.unaccounted_bytes == 4096
    assert cov.explained_fraction == 0.0


def test_tc_ps_07_full_coverage_leaves_nothing_unaccounted() -> None:
    cov = resolve(800, [claim(0, 800, "parsed", "one recording")])
    assert cov.unaccounted_bytes == 0
    assert cov.explained_fraction == 1.0


def test_zero_length_image_is_vacuously_covered() -> None:
    cov = resolve(0, [])
    assert cov.runs == ()
    assert cov.explained_fraction == 1.0


def test_verify_rejects_a_map_that_does_not_tile() -> None:
    with pytest.raises(ValueError, match="sum to"):
        CoverageMap(100, (CoverageRun(0, 50, "parsed"),))
    with pytest.raises(ValueError, match="not contiguous"):
        CoverageMap(100, (CoverageRun(0, 10, "parsed"), CoverageRun(20, 80, "carved")))


# --- the precedence contract (CLAUDE.md §14, owner P3) ------------------------------


def test_unreadable_outranks_every_other_bucket() -> None:
    """Sectors that were not read cannot also be 'parsed'. Physical truth wins."""
    for other in ("structural", "parsed", "carved"):
        cov = resolve(
            100,
            [claim(0, 100, other), claim(40, 20, "unreadable", "bad sector run")],  # type: ignore[arg-type]
        )
        assert cov.bucket_at(50) == "unreadable", other
        assert cov.totals()["unreadable"] == 20


def test_structural_outranks_parsed_so_metadata_is_never_counted_as_video() -> None:
    cov = resolve(
        100,
        [claim(0, 100, "parsed", "REC-0001"), claim(0, 16, "structural", "HIKBTREE")],
    )
    assert cov.bucket_at(0) == "structural"
    assert cov.bucket_at(16) == "parsed"
    assert cov.totals()["structural"] == 16


def test_parsed_outranks_carved_matching_merge_keeping_the_highest_tier() -> None:
    cov = resolve(
        100,
        [claim(0, 60, "carved", "T3"), claim(20, 60, "parsed", "T1")],
    )
    assert cov.bucket_at(30) == "parsed"
    assert cov.totals() == {
        "unreadable": 0,
        "structural": 0,
        "parsed": 60,
        "carved": 20,
        "unaccounted": 20,
    }


def test_precedence_order_is_the_documented_one() -> None:
    assert BUCKET_PRECEDENCE == (
        "unreadable",
        "structural",
        "parsed",
        "carved",
        "unaccounted",
    )


def test_unaccounted_cannot_be_claimed_it_is_derived() -> None:
    with pytest.raises(ValueError, match="derived, not claimed"):
        claim(0, 10, "unaccounted")
    with pytest.raises(ValueError, match="unknown coverage bucket"):
        Claim(Extent(0, 10), "nonsense")  # type: ignore[arg-type]


# --- shape and hygiene ---------------------------------------------------------------


def test_adjacent_same_bucket_runs_merge_so_rows_track_transitions_not_extents() -> None:
    """A fragmented carve must not cost one row per fragment (NFR-04)."""
    cov = resolve(1000, [claim(i, 1, "carved") for i in range(0, 500)])
    assert cov.runs == (
        CoverageRun(0, 500, "carved"),
        CoverageRun(500, 500, "unaccounted"),
    )


def test_claims_running_past_the_tail_are_clamped_not_fatal() -> None:
    """A carver legitimately probes past the tail on its last window."""
    cov = resolve(100, [claim(80, 500, "carved"), claim(0, 10, "parsed")])
    assert _totals_sum(cov) == 100
    assert cov.totals()["carved"] == 20
    cov2 = resolve(100, [claim(500, 10, "carved")])
    assert cov2.totals()["carved"] == 0


def test_a_claim_cannot_start_before_the_image() -> None:
    """`Extent` enforces this, so `resolve` never sees a negative offset."""
    with pytest.raises(ValueError, match="invalid extent"):
        claim(-1, 10, "carved")


def test_bucket_at_outside_the_image_is_none() -> None:
    cov = resolve(100, [claim(0, 100, "parsed")])
    assert cov.bucket_at(-1) is None
    assert cov.bucket_at(100) is None
    assert cov.bucket_at(99) == "parsed"


def test_to_rows_matches_the_casestore_schema() -> None:
    cov = resolve(10, [claim(0, 4, "structural")])
    rows = list(cov.to_rows("EV-001"))
    assert rows[0] == {
        "evidence_id": "EV-001",
        "offset": 0,
        "length": 4,
        "bucket": "structural",
    }
    assert {r["bucket"] for r in rows} <= set(BUCKET_PRECEDENCE)


def test_to_json_is_deterministic_and_lists_every_bucket() -> None:
    cov = resolve(100, [claim(0, 10, "parsed")])
    a, b = cov.to_json(), cov.to_json()
    assert a == b
    assert list(a["totals"]) == list(BUCKET_PRECEDENCE)  # type: ignore[arg-type]


# --- the property that matters: any claim set still tiles exactly --------------------


def test_random_overlapping_claims_always_tile_the_image_exactly() -> None:
    """TC-RB-04 in spirit: hostile, overlapping, out-of-range input must not break the map."""
    rng = random.Random(20260918)
    buckets = [b for b in BUCKET_PRECEDENCE if b != "unaccounted"]
    for _ in range(300):
        size = rng.randint(1, 8192)
        claims = [
            claim(
                rng.randint(0, size + 100),
                rng.randint(0, size // 2 + 1),
                rng.choice(buckets),  # type: ignore[arg-type]
            )
            for _ in range(rng.randint(0, 12))
        ]
        cov = resolve(size, claims)
        cov.verify()
        assert _totals_sum(cov) == size
        assert all(r.length > 0 for r in cov.runs)
        for left, right in zip(cov.runs, cov.runs[1:], strict=False):
            assert left.end == right.offset
            assert left.bucket != right.bucket, "adjacent runs should have merged"
