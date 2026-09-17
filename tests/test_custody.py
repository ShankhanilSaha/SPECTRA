"""Chain of custody and case documents (FR-73, FR-74, TC-IN-02, TC-IN-03).

The chain is the part of the case that happens off the disk, and the part a defence
expert attacks first. These tests pin what the tool refuses to let pass silently.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from spectra.core.casestore import CaseMeta, CaseStore
from spectra.report.negative import negative_findings
from spectra.services import ServiceError
from spectra.services import custody as cs
from spectra.services import evidence as ev
from tests.test_audit import fixed_clock


def seeded(tmp_path: Path, provenance: str = "A") -> tuple[CaseStore, str]:
    path = tmp_path / "disk.raw"
    path.write_bytes(bytes(4096))
    store = CaseStore.create(
        tmp_path / "case", CaseMeta("CASE-K-1", title="custody"), "examiner-1",
        clock=fixed_clock(),
    )
    ingest = ev.import_image(store, path, provenance, label="disk")
    return store, ingest.evidence_id


def codes(store: CaseStore) -> set[str]:
    return {f.code for f in negative_findings(store)}


def doc(tmp_path: Path, name: str, data: bytes = b"scanned page") -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


# --- custody transfers (FR-73) ----------------------------------------------------------


def test_a_transfer_is_recorded_with_its_seal(tmp_path: Path) -> None:
    store, ev_id = seeded(tmp_path)
    entry = cs.record_transfer(
        store, ev_id, from_holder="SI Rao", to_holder="Malkhana",
        purpose="storage pending examination", seal_number="SEAL-4471", seal_intact=True,
    )
    assert entry.seq == 1
    assert entry.seal_intact is True
    assert cs.custody_entries(store, ev_id)[0].seal_number == "SEAL-4471"
    store.close()


def test_an_unsealed_transfer_and_a_broken_seal_are_different_facts(tmp_path: Path) -> None:
    """Collapsing these into one flag would turn a serious finding into a routine one."""
    store, ev_id = seeded(tmp_path)
    cs.record_transfer(store, ev_id, from_holder="A", to_holder="B", purpose="x")
    cs.record_transfer(
        store, ev_id, from_holder="B", to_holder="C", purpose="y", seal_intact=False
    )
    entries = cs.custody_entries(store, ev_id)
    assert entries[0].seal_intact is None, "no seal recorded is not a broken seal"
    assert entries[1].seal_intact is False
    store.close()


def test_a_transfer_without_both_holders_is_refused(tmp_path: Path) -> None:
    store, ev_id = seeded(tmp_path)
    with pytest.raises(ServiceError, match="gap in the chain"):
        cs.record_transfer(store, ev_id, from_holder="SI Rao", to_holder="  ", purpose="x")
    store.close()


def test_a_transfer_without_a_purpose_is_refused(tmp_path: Path) -> None:
    store, ev_id = seeded(tmp_path)
    with pytest.raises(ServiceError, match="stated purpose"):
        cs.record_transfer(store, ev_id, from_holder="A", to_holder="B", purpose="")
    store.close()


def test_the_timestamp_comes_from_the_audit_record(tmp_path: Path) -> None:
    """So the custody row and the chain cannot disagree about when it was entered."""
    store, ev_id = seeded(tmp_path)
    entry = cs.record_transfer(store, ev_id, from_holder="A", to_holder="B", purpose="x")
    starts = [r for r in store.audit.records() if r.action == "custody.transfer.start"]
    assert entry.ts_utc == starts[-1].ts_utc
    store.close()


def test_a_continuous_chain_reports_no_breaks(tmp_path: Path) -> None:
    store, ev_id = seeded(tmp_path)
    cs.record_transfer(store, ev_id, from_holder="SI Rao", to_holder="Malkhana",
                       purpose="storage")
    cs.record_transfer(store, ev_id, from_holder="Malkhana", to_holder="FSL",
                       purpose="examination")
    assert cs.chain_breaks(store, ev_id) == []
    store.close()


def test_an_item_that_moved_without_a_row_is_detected(tmp_path: Path) -> None:
    """The whole point of Form F-2: each release must match the previous receipt."""
    store, ev_id = seeded(tmp_path)
    cs.record_transfer(store, ev_id, from_holder="SI Rao", to_holder="Malkhana",
                       purpose="storage")
    cs.record_transfer(store, ev_id, from_holder="District Court", to_holder="FSL",
                       purpose="examination")
    breaks = cs.chain_breaks(store, ev_id)
    assert len(breaks) == 1
    assert "not recorded" in breaks[0]
    store.close()


def test_holder_matching_ignores_case_and_padding(tmp_path: Path) -> None:
    store, ev_id = seeded(tmp_path)
    cs.record_transfer(store, ev_id, from_holder="SI Rao", to_holder="Malkhana",
                       purpose="storage")
    cs.record_transfer(store, ev_id, from_holder="  malkhana ", to_holder="FSL",
                       purpose="examination")
    assert cs.chain_breaks(store, ev_id) == []
    store.close()


# --- attachments (FR-74) ----------------------------------------------------------------


def test_a_document_is_hashed_on_the_way_in(tmp_path: Path) -> None:
    store, ev_id = seeded(tmp_path)
    ref = cs.attach_document(
        store, doc(tmp_path, "panchnama.pdf"), "panchnama",
        description="seizure memo", evidence_id=ev_id, provided_by="SI Rao",
    )
    assert len(ref.sha256) == 64 and len(ref.md5) == 32
    assert store.artifact_path(ref.sha256).exists()
    store.close()


def test_a_seizure_video_carries_its_statutory_hook(tmp_path: Path) -> None:
    """So a reader sees why it is in the case without knowing the SOP."""
    store, ev_id = seeded(tmp_path)
    ref = cs.attach_document(
        store, doc(tmp_path, "seizure.mp4"), "seizure_video", evidence_id=ev_id
    )
    assert "s. 105" in ref.statutory_ref
    store.close()


def test_an_unknown_document_kind_is_refused(tmp_path: Path) -> None:
    store, ev_id = seeded(tmp_path)
    with pytest.raises(ServiceError, match="not an attachment kind"):
        cs.attach_document(store, doc(tmp_path, "x.pdf"), "selfie", evidence_id=ev_id)
    store.close()


def test_a_missing_file_is_refused_rather_than_recorded_empty(tmp_path: Path) -> None:
    store, ev_id = seeded(tmp_path)
    with pytest.raises(ServiceError, match="not found"):
        cs.attach_document(store, tmp_path / "absent.pdf", "panchnama", evidence_id=ev_id)
    store.close()


def test_attachments_are_not_listed_as_tool_output(tmp_path: Path) -> None:
    """`artifact` is what SPECTRA produced; a panchnama there would be presented as ours."""
    store, ev_id = seeded(tmp_path)
    cs.attach_document(store, doc(tmp_path, "panchnama.pdf"), "panchnama", evidence_id=ev_id)
    kinds = {r[0] for r in store.conn.execute("SELECT kind FROM artifact")}
    assert "panchnama" not in kinds
    assert kinds == {"attachment"}, "the bytes are stored, the document semantics are not"
    store.close()


def test_a_case_wide_document_counts_for_every_item(tmp_path: Path) -> None:
    """One authorisation commonly covers a whole seizure."""
    store, _ = seeded(tmp_path)
    cs.attach_document(store, doc(tmp_path, "auth.pdf"), "authorisation")
    assert [a.kind for a in cs.attachments(store)] == ["authorisation"]
    store.close()


# --- what the report says about all this ------------------------------------------------


def test_evidence_with_no_custody_record_is_a_serious_finding(tmp_path: Path) -> None:
    store, _ = seeded(tmp_path)
    found = [f for f in negative_findings(store) if f.code == "NF-NO-CUSTODY"]
    assert found and found[0].severity == "serious"
    assert "not established by this case file" in found[0].detail
    store.close()


def test_a_broken_seal_is_reported_as_serious(tmp_path: Path) -> None:
    store, ev_id = seeded(tmp_path)
    cs.record_transfer(store, ev_id, from_holder="A", to_holder="B", purpose="x",
                       seal_number="S-1", seal_intact=False)
    found = [f for f in negative_findings(store) if f.code == "NF-SEAL-BROKEN"]
    assert found and found[0].severity == "serious"
    assert found[0].numbers["broken_seals"] == 1
    store.close()


def test_a_chain_gap_reaches_the_report(tmp_path: Path) -> None:
    store, ev_id = seeded(tmp_path)
    cs.record_transfer(store, ev_id, from_holder="SI Rao", to_holder="Malkhana",
                       purpose="storage", seal_intact=True)
    cs.record_transfer(store, ev_id, from_holder="Court", to_holder="FSL",
                       purpose="examination", seal_intact=True)
    assert "NF-CUSTODY-GAP" in codes(store)
    store.close()


def test_a_seized_recorder_is_expected_to_have_seizure_documents(tmp_path: Path) -> None:
    store, _ = seeded(tmp_path, provenance="A")
    found = [f for f in negative_findings(store) if f.code == "NF-MISSING-DOCUMENTS"]
    assert found
    for expected in ("panchnama", "seizure_video", "authorisation"):
        assert expected in found[0].detail
    store.close()


def test_attaching_the_documents_clears_the_finding(tmp_path: Path) -> None:
    store, ev_id = seeded(tmp_path, provenance="A")
    for name, kind in (
        ("panchnama.pdf", "panchnama"),
        ("seizure.mp4", "seizure_video"),
        ("auth.pdf", "authorisation"),
    ):
        cs.attach_document(store, doc(tmp_path, name), kind, evidence_id=ev_id)
    assert "NF-MISSING-DOCUMENTS" not in codes(store)
    store.close()


def test_a_third_party_export_is_not_asked_for_a_seizure_video(tmp_path: Path) -> None:
    """Demanding documents a case could not have trains examiners to skim the section."""
    store, ev_id = seeded(tmp_path, provenance="D")
    cs.attach_document(store, doc(tmp_path, "auth.pdf"), "authorisation", evidence_id=ev_id)
    assert "NF-MISSING-DOCUMENTS" not in codes(store)
    store.close()


def test_a_live_acquisition_is_not_asked_for_a_seizure_video(tmp_path: Path) -> None:
    """Whether s. 105 attaches turns on whether it was a search — not ours to guess."""
    store, ev_id = seeded(tmp_path, provenance="C")
    for name, kind in (("panchnama.pdf", "panchnama"), ("auth.pdf", "authorisation")):
        cs.attach_document(store, doc(tmp_path, name), kind, evidence_id=ev_id)
    assert "NF-MISSING-DOCUMENTS" not in codes(store)
    store.close()


def test_custody_and_attachments_reach_findings_json(tmp_path: Path) -> None:
    from spectra.report import findings as fnd

    store, ev_id = seeded(tmp_path)
    cs.record_transfer(store, ev_id, from_holder="A", to_holder="B", purpose="x",
                       seal_intact=True)
    cs.attach_document(store, doc(tmp_path, "auth.pdf"), "authorisation", evidence_id=ev_id)
    document = fnd.build(store, generated_utc="2026-03-05T14:00:00Z")
    assert len(document["custody"]) == 1
    assert document["custody"][0]["chain_breaks"] == []
    assert [a["kind"] for a in document["attachments"]] == ["authorisation"]
    store.close()


def test_the_audit_chain_survives_both_operations(tmp_path: Path) -> None:
    store, ev_id = seeded(tmp_path)
    cs.record_transfer(store, ev_id, from_holder="A", to_holder="B", purpose="x")
    cs.attach_document(store, doc(tmp_path, "auth.pdf"), "authorisation", evidence_id=ev_id)
    assert store.audit.verify().ok
    store.close()
