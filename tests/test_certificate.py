"""BSA s. 63(4) certificate and its hash enclosure (FR-83, AC-09, TC-RP-04).

The properties under test are the ones a court relies on: the form matches the Schedule,
the tool does not sign it, the enclosure never asserts a digest for something it is not
handing over, and an artefact the case knows about is never silently dropped.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from spectra.core.casestore import CaseMeta, CaseStore
from spectra.report import certificate as cert
from spectra.services import evidence as ev
from spectra.services import report as rp
from tests.test_audit import fixed_clock


def new_case(tmp_path: Path) -> CaseStore:
    return CaseStore.create(
        tmp_path / "case",
        CaseMeta("CASE-C-1", title="certificate", examiner_name="R. Examiner"),
        "examiner-1",
        clock=fixed_clock(),
    )


def seeded(tmp_path: Path) -> tuple[CaseStore, str]:
    path = tmp_path / "hik.raw"
    path.write_bytes(bytes(8192))
    store = new_case(tmp_path)
    ingest = ev.import_image(store, path, "A", label="disk")
    return store, ingest.evidence_id


def add_artifact(store: CaseStore, kind: str, data: bytes) -> str:
    """Put a real file in the CAS so the certificate has something to re-hash."""
    src = store.root / f"{kind}.bin"
    src.write_bytes(data)
    ref = store.add_artifact_file(src, kind, created_utc="2026-03-05T14:00:00Z")
    return ref.sha256


# --- the form matches the Schedule ------------------------------------------------------


def test_dvr_is_a_named_source_type_on_the_form() -> None:
    """The statute contemplates this device class explicitly; the default reflects that."""
    assert "DVR" in cert.SOURCE_TYPES
    assert cert.DeviceParticulars().source_type == "DVR"


def test_only_the_three_algorithms_the_schedule_names_are_tickable() -> None:
    assert cert.HASH_ALGORITHMS == ("SHA1", "SHA256", "MD5")
    block = cert.HashBlock(sha256="a" * 64, md5="b" * 32)
    assert block.ticked == ("SHA256", "MD5"), "an uncomputed digest must not be ticked"


def test_sha512_has_to_go_under_other_because_the_schedule_omits_it() -> None:
    block = cert.HashBlock(other_algorithm="SHA512", other_value="c" * 128)
    assert block.ticked == ("Other (SHA512)",)


def test_a_source_type_the_schedule_does_not_list_is_refused() -> None:
    with pytest.raises(ValueError, match="not one the Schedule lists"):
        cert.DeviceParticulars(source_type="Smart Fridge")


def test_a_control_mode_that_is_not_on_the_form_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not on the form"):
        cert.Certificate(
            case_id="C", instance=1, statute="BSA_63",
            device=cert.DeviceParticulars(), hashes=cert.HashBlock(),
            record_description="x", hash_report_ref="y",
            control_modes=("Borrowed",),
        )


def test_part_b_carries_a_designation_and_part_a_carries_the_recital() -> None:
    """The asymmetry is in the statute — Part A recites, Part B is an expert."""
    c = cert.Certificate(
        case_id="C", instance=1, statute="BSA_63",
        device=cert.DeviceParticulars(), hashes=cert.HashBlock(),
        record_description="x", hash_report_ref="y",
        part_b=cert.Signatory(name="Dr A", designation="Assistant Director (Cyber)"),
    )
    assert "lawful control" in c.recital
    assert c.part_b.to_json()["designation"] == "Assistant Director (Cyber)"


def test_the_statute_follows_the_date_the_proceeding_began_not_the_footage() -> None:
    assert cert.statute_for(datetime(2024, 6, 30)) == "IEA_65B"
    assert cert.statute_for(datetime(2024, 7, 1)) == "BSA_63"
    assert cert.statute_for(None) == "BSA_63"


def test_an_old_proceeding_gets_the_old_heading() -> None:
    c = cert.Certificate(
        case_id="C", instance=1, statute="IEA_65B",
        device=cert.DeviceParticulars(), hashes=cert.HashBlock(),
        record_description="x", hash_report_ref="y",
    )
    assert "65B" in c.heading
    assert "before 1 July 2024" in c.authority


# --- the tool does not sign it ----------------------------------------------------------


def test_the_tool_leaves_both_signatures_blank_and_says_which(tmp_path: Path) -> None:
    """Pre-filling a sworn statement would forge the thing s. 63(4) exists to obtain."""
    store, ev_id = seeded(tmp_path)
    c, _, _ = rp.generate_certificate(store, ev_id)
    assert len(c.unsigned) == 2
    assert any("lawful control" in part for part in c.unsigned)
    assert any("expert" in part for part in c.unsigned)
    store.close()


def test_instance_numbering_starts_at_one_because_each_submission_needs_its_own() -> None:
    with pytest.raises(ValueError, match="starts at 1"):
        cert.Certificate(
            case_id="C", instance=0, statute="BSA_63",
            device=cert.DeviceParticulars(), hashes=cert.HashBlock(),
            record_description="x", hash_report_ref="y",
        )


# --- the enclosure never asserts a digest for something it is not handing over ----------


def test_an_intact_artefact_is_tendered_with_all_three_digests(tmp_path: Path) -> None:
    store, ev_id = seeded(tmp_path)
    add_artifact(store, "evidence_copy", b"clip-bytes" * 100)
    _, enclosure, warnings = rp.generate_certificate(store, ev_id)
    assert warnings == []
    row = next(r for r in enclosure if r["kind"] == "evidence_copy")
    assert row["MD5"] and row["SHA1"] and row["SHA256"]
    assert row["note"] == ""
    store.close()


def test_an_altered_artefact_is_withheld_not_tendered(tmp_path: Path) -> None:
    """The decision under test: disclose it, do not assert a digest for it."""
    store, ev_id = seeded(tmp_path)
    sha = add_artifact(store, "evidence_copy", b"original" * 100)
    store.artifact_path(sha).write_bytes(b"tampered" * 100)

    c, enclosure, warnings = rp.generate_certificate(store, ev_id)

    assert any("re-hashes to" in w for w in warnings)
    row = next(r for r in enclosure if r["kind"].startswith("WITHHELD"))
    assert row["SHA256"] == "", "no digest may be asserted for a withheld artefact"
    assert row["MD5"] == "" and row["SHA1"] == ""
    assert sha in row["note"], "the note must name the digest the case recorded"
    assert "WITHHELD FROM TENDER" in row["note"]
    # And the face of the certificate says the enclosure is incomplete.
    assert "WITHHELD" in c.hash_report_ref
    store.close()


def test_an_altered_artefact_does_not_block_the_intact_ones(tmp_path: Path) -> None:
    """Refusing outright would block a whole tender over one bad file."""
    store, ev_id = seeded(tmp_path)
    bad = add_artifact(store, "evidence_copy", b"original" * 100)
    add_artifact(store, "report", b"report-bytes" * 100)
    store.artifact_path(bad).write_bytes(b"tampered" * 100)

    _, enclosure, _ = rp.generate_certificate(store, ev_id)
    assert cert.tendered(enclosure), "the intact artefacts must still be tendered"
    assert len(cert.withheld(enclosure)) == 1
    store.close()


def test_a_missing_artefact_file_is_disclosed_rather_than_dropped(tmp_path: Path) -> None:
    store, ev_id = seeded(tmp_path)
    sha = add_artifact(store, "evidence_copy", b"clip" * 100)
    store.artifact_path(sha).unlink()

    _, enclosure, warnings = rp.generate_certificate(store, ev_id)
    assert any("absent from the artefact store" in w for w in warnings)
    row = next(r for r in enclosure if r["kind"].startswith("WITHHELD"))
    assert row["SHA256"] == ""
    assert "no corresponding file is present" in row["note"]
    store.close()


def test_withheld_rows_sort_together_instead_of_hiding_among_tendered_ones(
    tmp_path: Path,
) -> None:
    store, ev_id = seeded(tmp_path)
    bad = add_artifact(store, "evidence_copy", b"a" * 100)
    add_artifact(store, "report", b"b" * 100)
    store.artifact_path(bad).write_bytes(b"c" * 100)

    _, enclosure, _ = rp.generate_certificate(store, ev_id)
    kinds = [r["kind"] for r in enclosure]
    withheld_at = [i for i, k in enumerate(kinds) if k.startswith("WITHHELD")]
    assert withheld_at == list(range(withheld_at[0], withheld_at[0] + len(withheld_at)))
    store.close()


def test_the_evidence_image_leaves_sha1_blank_rather_than_inventing_one(
    tmp_path: Path,
) -> None:
    """A multi-TB re-hash to fill one checkbox is not worth the wait; a blank is honest."""
    store, ev_id = seeded(tmp_path)
    _, enclosure, _ = rp.generate_certificate(store, ev_id)
    row = next(r for r in enclosure if r["kind"] == "evidence_image")
    assert row["SHA256"], "the stored SHA-256 is reprinted"
    assert row["SHA1"] == ""
    store.close()


# --- the mismatch is permanently recorded, not just returned ----------------------------


def test_the_warnings_themselves_reach_the_audit_chain(tmp_path: Path) -> None:
    """A count records that something was wrong without recording what."""
    store, ev_id = seeded(tmp_path)
    sha = add_artifact(store, "evidence_copy", b"original" * 100)
    store.artifact_path(sha).write_bytes(b"tampered" * 100)
    rp.generate_certificate(store, ev_id)

    complete = [r for r in store.audit.records() if r.action == "report.certificate.complete"]
    assert complete
    recorded = complete[-1].params["warnings"]
    assert isinstance(recorded, list) and recorded, "the text must be in the chain"
    assert any("re-hashes to" in w for w in recorded)
    assert complete[-1].params["withheld_items"] == 1
    store.close()


def test_the_certificate_operation_leaves_the_audit_chain_verifiable(tmp_path: Path) -> None:
    store, ev_id = seeded(tmp_path)
    rp.generate_certificate(store, ev_id)
    assert store.audit.verify().ok
    store.close()
