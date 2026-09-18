"""Section 7 — generated, not written (FR-81, TC-RP-02, TC-RP-03).

The properties under test are the ones that make the section worth having: it appears
without being asked for, it cannot be suppressed, and it is deterministic.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from spectra.core.casestore import CaseMeta, CaseStore
from spectra.report.negative import negative_findings, summarise
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
        tmp_path / name, CaseMeta("CASE-N-1", title="negative"), "examiner-1",
        clock=fixed_clock(),
    )


def seeded(
    tmp_path: Path, image: bytes, *, do_parse: bool = True, do_recover: bool = True,
    provenance: str = "A",
) -> tuple[CaseStore, str]:
    path = tmp_path / "hik.raw"
    path.write_bytes(image)
    store = new_case(tmp_path)
    ingest = ev.import_image(store, path, provenance, label="disk")
    ident.run_identify(store, ingest.evidence_id)
    if do_parse:
        ps.parse(store, ingest.evidence_id)
    if do_recover:
        rc.recover(store, ingest.evidence_id, ("T2",))
    return store, ingest.evidence_id


def codes(store: CaseStore) -> set[str]:
    return {f.code for f in negative_findings(store)}


# --- it appears without being asked for -------------------------------------------------


def test_tc_rp_02_unaccounted_space_is_reported(tmp_path: Path) -> None:
    store, _ = seeded(tmp_path, hikgen.volume(recordings=RECS))
    found = [f for f in negative_findings(store) if f.code == "NF-COVERAGE-UNACCOUNTED"]
    assert found, "an image that is mostly empty must say so"
    assert found[0].numbers["unaccounted_bytes"] > 0
    assert "could not be explained" in found[0].detail or "no parsed recording" in found[0].detail
    store.close()


def test_absolute_time_not_established_is_a_serious_finding(tmp_path: Path) -> None:
    """No offset evidence means every time in the report is the recorder's own wrong clock."""
    store, _ = seeded(tmp_path, hikgen.volume(recordings=RECS))
    found = [f for f in negative_findings(store) if f.code == "NF-NO-CLOCK-OFFSET"]
    assert found and found[0].severity == "serious"
    assert "may be wrong by an unknown amount" in found[0].detail
    store.close()


def test_carve_only_evidence_says_no_index_was_read(tmp_path: Path) -> None:
    store, _ = seeded(
        tmp_path, hikgen.volume(version=b"HIK.2099.01.01"), do_parse=False, do_recover=False
    )
    found = [f for f in negative_findings(store) if f.code == "NF-CARVE-ONLY"]
    assert found
    assert "no index was read" in found[0].detail
    store.close()


def test_third_party_exports_are_flagged_as_the_weakest_provenance(tmp_path: Path) -> None:
    usb = tmp_path / "usb"
    usb.mkdir()
    (usb / "clip.dav").write_bytes(b"\x00" * 64)
    store = new_case(tmp_path)
    ev.import_files(store, usb, label="owner USB")
    found = [f for f in negative_findings(store) if f.code == "NF-PROVENANCE-D"]
    assert found and found[0].severity == "serious"
    assert "original storage was never examined" in found[0].detail
    assert "recommended" in found[0].detail, "class D should be a stage, not a destination"
    store.close()


def test_evidence_that_was_parsed_but_not_recovered_says_so(tmp_path: Path) -> None:
    store, _ = seeded(tmp_path, hikgen.volume(recordings=RECS), do_recover=False)
    found = [f for f in negative_findings(store) if f.code == "NF-NO-RECOVERY"]
    assert found
    assert "not evidence of absence" in found[0].detail
    store.close()


def test_evidence_that_was_never_identified_is_not_silently_ignored(tmp_path: Path) -> None:
    path = tmp_path / "x.raw"
    path.write_bytes(bytes(4096))
    store = new_case(tmp_path)
    ev.import_image(store, path, "A")
    assert "NF-NOT-IDENTIFIED" in codes(store)
    store.close()


def test_recordings_without_a_timestamp_are_counted(tmp_path: Path) -> None:
    store, ev_id = seeded(tmp_path, hikgen.volume(recordings=RECS), do_recover=False)
    store.conn.execute(
        "UPDATE recording SET t_local_start = NULL WHERE evidence_id = ?", (ev_id,)
    )
    store.conn.commit()
    found = [f for f in negative_findings(store) if f.code == "NF-TIME-UNKNOWN"]
    assert found
    assert "no time has been inferred" in found[0].detail
    store.close()


# --- it cannot be suppressed ------------------------------------------------------------


def test_tc_rp_03_a_finding_cannot_be_deleted_only_the_fact_can_change(
    tmp_path: Path,
) -> None:
    """There is no suppression path: delete the row and the finding returns.

    This is the whole design of FR-81. The section is non-deletable because nothing
    persists it — every call re-derives it from the case.
    """
    store, ev_id = seeded(tmp_path, hikgen.volume(recordings=RECS))
    assert "NF-COVERAGE-UNACCOUNTED" in codes(store)

    # There is no API to dismiss a finding. Even clearing the derived table only means it
    # is recomputed from what remains.
    store.conn.execute("DELETE FROM coverage WHERE evidence_id = ?", (ev_id,))
    store.conn.commit()
    after = codes(store)
    assert "NF-COVERAGE-UNACCOUNTED" not in after, "the underlying fact is gone"
    assert "NF-NO-RECOVERY" in after, "and removing it creates a different finding"
    store.close()


def test_the_module_offers_no_way_to_suppress_a_finding() -> None:
    import spectra.report.negative as mod

    surface = {name for name in dir(mod) if not name.startswith("_")}
    for forbidden in ("dismiss", "suppress", "hide", "ignore", "exclude", "delete"):
        assert not any(forbidden in name.lower() for name in surface), (
            f"a {forbidden!r} entry point would defeat FR-81"
        )


# --- it is deterministic ----------------------------------------------------------------


def test_ac_11_two_runs_produce_an_identical_list(tmp_path: Path) -> None:
    store, _ = seeded(tmp_path, hikgen.volume(recordings=RECS, orphan_indices=(1,)))
    first = [f.to_json() for f in negative_findings(store)]
    second = [f.to_json() for f in negative_findings(store)]
    assert first == second
    store.close()


def test_findings_are_ordered_most_serious_first(tmp_path: Path) -> None:
    store, _ = seeded(tmp_path, hikgen.volume(recordings=RECS))
    order = {"serious": 0, "attention": 1, "info": 2}
    ranks = [order[f.severity] for f in negative_findings(store)]
    assert ranks == sorted(ranks)
    store.close()


def test_summarise_counts_every_severity_band(tmp_path: Path) -> None:
    store, _ = seeded(tmp_path, hikgen.volume(recordings=RECS))
    findings = negative_findings(store)
    summary = summarise(findings)
    assert summary["total"] == len(findings)
    assert set(summary["by_severity"]) == {"serious", "attention", "info"}
    assert sum(summary["by_severity"].values()) == len(findings)
    store.close()


def test_an_empty_case_produces_no_findings_and_does_not_crash(tmp_path: Path) -> None:
    store = new_case(tmp_path)
    assert negative_findings(store) == []
    assert summarise([]) == {"total": 0, "by_severity": {"serious": 0, "attention": 0, "info": 0}}
    store.close()


@pytest.mark.parametrize("field", ["code", "severity", "title", "detail"])
def test_every_finding_carries_the_fields_a_report_needs(tmp_path: Path, field: str) -> None:
    store, _ = seeded(tmp_path, hikgen.volume(recordings=RECS))
    findings = negative_findings(store)
    assert findings
    for finding in findings:
        assert getattr(finding, field), f"{finding.code} has an empty {field}"
    store.close()
