"""`findings.json` — the report's data spine (FR-86, AC-11, NFR-08, TC-RP-06).

AC-11 is a byte-level claim: two runs on the same image produce identical findings JSON.
These tests attack the three ways that claim usually breaks — a wall-clock read, an
unordered query, and a second canonicaliser.
"""

from __future__ import annotations

import ast
import json
from datetime import datetime
from pathlib import Path

from spectra.core.audit import canonical_json
from spectra.core.casestore import CaseMeta, CaseStore
from spectra.report import findings as fnd
from spectra.services import evidence as ev
from spectra.services import identify as ident
from spectra.services import parse as ps
from spectra.services import recover as rc
from spectra.services import report as rp
from tests import hikgen
from tests.test_audit import fixed_clock

RECS = [
    (1, datetime(2026, 3, 5, 14, 0, 0), datetime(2026, 3, 5, 14, 30, 0)),
    (2, datetime(2026, 3, 5, 14, 5, 0), datetime(2026, 3, 5, 14, 35, 0)),
]


def seeded(tmp_path: Path, name: str = "case") -> tuple[CaseStore, str]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / f"{name}.raw"
    path.write_bytes(hikgen.volume(recordings=RECS, orphan_indices=(1,)))
    store = CaseStore.create(
        tmp_path / name, CaseMeta("CASE-F-1", title="findings"), "examiner-1",
        clock=fixed_clock(),
    )
    ingest = ev.import_image(store, path, "A", label="disk")
    ident.run_identify(store, ingest.evidence_id)
    ps.parse(store, ingest.evidence_id)
    rc.recover(store, ingest.evidence_id, ("T2", "T3"))
    return store, ingest.evidence_id


# --- determinism ------------------------------------------------------------------------


def test_ac_11_two_builds_of_the_same_case_are_byte_identical(tmp_path: Path) -> None:
    store, _ = seeded(tmp_path)
    first = fnd.build(store, generated_utc="2026-03-05T14:00:00Z")
    second = fnd.build(store, generated_utc="2026-03-05T14:00:00Z")
    assert fnd.serialise(first) == fnd.serialise(second)
    assert fnd.digest(first) == fnd.digest(second)
    store.close()


def _called_names(module_path: str) -> set[str]:
    """Names actually invoked in a module, from its AST.

    Deliberately not a substring scan of the source: the first version of this test
    matched the docstring that *forbids* `datetime.now()` and failed on the warning
    rather than the offence. Prose about code is not code.
    """
    tree = ast.parse(Path(module_path).read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            names.add(ast.unparse(node.func))
    return names


def test_the_module_never_reads_a_clock() -> None:
    """`generated_utc` is a parameter precisely so this cannot regress.

    A wall-clock read anywhere in here would make byte-identical output impossible no
    matter how carefully everything downstream sorted its keys (CLAUDE.md rule 13).
    """
    called = _called_names("spectra/report/findings.py")
    for forbidden in (
        "datetime.now", "datetime.utcnow", "utc_now", "time.time", "date.today",
    ):
        assert forbidden not in called, f"{forbidden}() would break AC-11 by construction"


def test_every_query_in_the_module_is_explicitly_ordered() -> None:
    """SQLite row order without ORDER BY is an implementation detail, not a guarantee.

    Scans the SQL string literals rather than the source lines, so a query split across
    several lines is still checked as one query.
    """
    tree = ast.parse(Path("spectra/report/findings.py").read_text(encoding="utf-8"))
    queries = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and "SELECT" in node.value
    ]
    assert queries, "the test found no queries to check — has the module moved?"
    for sql in queries:
        # An aggregate returns exactly one row, so it has no order to fix.
        if "COUNT(" in sql:
            continue
        assert "ORDER BY" in sql, f"unordered query would break AC-11: {sql!r}"


def test_the_digest_ignores_pretty_printing(tmp_path: Path) -> None:
    """Indentation is a presentation choice and must never change what is hashed."""
    store, _ = seeded(tmp_path)
    doc = fnd.build(store, generated_utc="2026-03-05T14:00:00Z")
    before = fnd.digest(doc)
    assert json.loads(fnd.readable(doc)) == json.loads(fnd.serialise(doc))
    assert fnd.digest(doc) == before
    store.close()


def test_it_uses_the_same_canonicaliser_as_the_audit_chain(tmp_path: Path) -> None:
    """One canonicaliser, so the determinism guarantee cannot fork."""
    store, _ = seeded(tmp_path)
    doc = fnd.build(store, generated_utc="2026-03-05T14:00:00Z")
    assert fnd.serialise(doc) == canonical_json(doc) + b"\n"
    store.close()


# --- content ----------------------------------------------------------------------------


def test_the_document_carries_every_section_the_report_renders(tmp_path: Path) -> None:
    store, _ = seeded(tmp_path)
    doc = fnd.build(store, generated_utc="2026-03-05T14:00:00Z")
    for key in (
        "case", "evidence", "identification", "disk_layout", "coverage", "recordings",
        "time", "device_events", "annotations", "artifacts", "custody",
        "negative_findings", "negative_summary", "integrity",
    ):
        assert key in doc, f"the report has no source for its {key} section"
    store.close()


def test_coverage_states_whether_the_buckets_tile_the_image(tmp_path: Path) -> None:
    """The §14 contract: the five buckets must sum to the image size, and say so."""
    store, _ = seeded(tmp_path)
    doc = fnd.build(store, generated_utc="2026-03-05T14:00:00Z")
    assert doc["coverage"], "a parsed image must have coverage"
    for entry in doc["coverage"]:
        assert entry["tiles_image"], "coverage that does not tile is a broken contract"
        assert sum(entry["totals_bytes"].values()) == entry["image_size_bytes"]
    store.close()


def test_absolute_time_is_reported_as_not_established_without_an_offset(
    tmp_path: Path,
) -> None:
    """FR-53: no offset evidence means the report may not print a UTC time anywhere."""
    store, _ = seeded(tmp_path)
    doc = fnd.build(store, generated_utc="2026-03-05T14:00:00Z")
    assert doc["time"]["absolute_established"] is False
    assert doc["time"]["recordings_with_reference_time"] == 0
    assert doc["time"]["recordings_total"] > 0
    store.close()


def test_integrity_carries_the_audit_head_a_third_party_verifies(tmp_path: Path) -> None:
    store, _ = seeded(tmp_path)
    doc = fnd.build(store, generated_utc="2026-03-05T14:00:00Z")
    assert doc["integrity"]["audit_ok"] is True
    assert len(doc["integrity"]["audit_head_digest"]) == 64
    assert doc["integrity"]["audit_records"] > 0
    store.close()


def test_a_broken_chain_is_reported_in_the_document_not_hidden(tmp_path: Path) -> None:
    """The report must state that its own integrity check failed."""
    store, _ = seeded(tmp_path)
    store.conn.execute("DROP TRIGGER audit_no_update")
    store.conn.execute("UPDATE audit SET operator = 'someone else' WHERE seq = 1")
    store.conn.commit()
    doc = fnd.build(store, generated_utc="2026-03-05T14:00:00Z")
    assert doc["integrity"]["audit_ok"] is False
    assert doc["integrity"]["audit_broken_at_seq"] is not None
    store.close()


def test_a_malformed_json_column_is_surfaced_rather_than_swallowed(tmp_path: Path) -> None:
    store, ev_id = seeded(tmp_path)
    store.conn.execute(
        "UPDATE recording SET extents_json = '{not json' WHERE evidence_id = ?", (ev_id,)
    )
    store.conn.commit()
    doc = fnd.build(store, generated_utc="2026-03-05T14:00:00Z")
    bad = [r for r in doc["recordings"] if isinstance(r["extents"], dict)]
    assert bad and "unparsable_json" in bad[0]["extents"]
    store.close()


# --- the service --------------------------------------------------------------------


def test_the_service_takes_its_timestamp_from_the_audit_record(tmp_path: Path) -> None:
    """Not from the clock — that is what makes a re-run reproducible."""
    store, _ = seeded(tmp_path)
    doc, digest = rp.generate_findings(store)
    starts = [r for r in store.audit.records() if r.action == "report.findings.start"]
    assert doc["generated_utc"] == starts[-1].ts_utc
    assert digest == fnd.digest(doc)
    store.close()


def test_the_service_stores_findings_as_a_hashed_artefact(tmp_path: Path) -> None:
    store, _ = seeded(tmp_path)
    _, digest = rp.generate_findings(store)
    row = store.conn.execute(
        "SELECT sha256, kind FROM artifact WHERE kind = 'findings'"
    ).fetchone()
    assert row is not None, "findings.json must be in the case, not just returned"
    assert store.artifact_path(row[0]).exists()
    assert digest == row[0], "the digest the report prints is the artefact's own"
    store.close()


def test_the_findings_digest_reaches_the_audit_chain(tmp_path: Path) -> None:
    store, _ = seeded(tmp_path)
    _, digest = rp.generate_findings(store)
    complete = [r for r in store.audit.records() if r.action == "report.findings.complete"]
    assert complete[-1].hash_after == digest
    store.close()


def test_ac_11_two_independent_examinations_of_one_image_agree(tmp_path: Path) -> None:
    """The claim as a court would test it: two labs, same disk, compare one number.

    Each case has its own audit chain, its own operator and its own timestamps, so the
    whole-file digests differ and should. What must match is what the tool concluded
    about the disk.
    """
    lab_a, _ = seeded(tmp_path / "a", "lab-a")
    lab_b, _ = seeded(tmp_path / "b", "lab-b")
    doc_a = fnd.build(lab_a, generated_utc="2026-03-05T14:00:00Z")
    doc_b = fnd.build(lab_b, generated_utc="2026-09-18T09:30:00Z")

    assert doc_a["conclusions_digest"] == doc_b["conclusions_digest"]
    assert fnd.digest(doc_a) != fnd.digest(doc_b), (
        "the whole-file digests must differ — they cover each lab's own history"
    )
    lab_a.close()
    lab_b.close()


def test_regenerating_a_report_does_not_change_the_conclusions(tmp_path: Path) -> None:
    """Generating a report is itself an audited event, so the file digest moves.

    A reader who runs the command twice and sees two file digests must still be able to
    see that nothing about the disk changed.
    """
    store, _ = seeded(tmp_path)
    first, first_file = rp.generate_findings(store)
    second, second_file = rp.generate_findings(store)

    assert first["conclusions_digest"] == second["conclusions_digest"]
    assert first_file != second_file, "the audit chain grew, and the document says so"
    assert second["integrity"]["audit_head_seq"] > first["integrity"]["audit_head_seq"]
    store.close()


def test_the_conclusions_digest_excludes_this_examination_s_own_history() -> None:
    """Pin the split: a volatile key here would silently break the cross-lab comparison."""
    for volatile in ("generated_utc", "integrity", "artifacts", "custody", "case"):
        assert volatile not in fnd.CONCLUSION_KEYS


def test_a_changed_conclusion_does_change_the_conclusions_digest(tmp_path: Path) -> None:
    """The digest has to be sensitive to what it claims to cover, or it proves nothing."""
    store, ev_id = seeded(tmp_path)
    before = fnd.build(store, generated_utc="2026-03-05T14:00:00Z")["conclusions_digest"]
    store.conn.execute(
        "UPDATE recording SET channel = 9 WHERE evidence_id = ?", (ev_id,)
    )
    store.conn.commit()
    after = fnd.build(store, generated_utc="2026-03-05T14:00:00Z")["conclusions_digest"]
    assert before != after
    store.close()
