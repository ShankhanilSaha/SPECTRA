"""CaseStore (FR-70, FR-72) — directory layout, CAS, verification."""

from __future__ import annotations

import json
import sqlite3

import pytest

from spectra.core.casestore import CaseError, CaseMeta, CaseStore
from tests.test_audit import fixed_clock

META = CaseMeta(case_id="CASE-2026-0142", title="Market Rd CCTV", agency="District Cyber Cell")


def new_case(tmp_path):
    return CaseStore.create(tmp_path / "case", META, "examiner-1", clock=fixed_clock())


def test_create_lays_out_directory_and_audits_creation(tmp_path):
    store = new_case(tmp_path)
    root = tmp_path / "case"
    for name in ("case.db", "case.manifest.json", "artifacts", "images", "logs"):
        assert (root / name).exists()
    assert store.case_id == "CASE-2026-0142"
    actions = [r.action for r in store.audit.records()]
    assert actions == ["case.create.start", "case.create.complete"]
    manifest = json.loads((root / "case.manifest.json").read_text("utf-8"))
    assert manifest["audit"]["head_seq"] == 1
    assert store.meta()["created_utc"] == next(store.audit.records()).ts_utc
    assert store.verify().ok
    store.close()


def test_create_refuses_non_empty_directory(tmp_path):
    (tmp_path / "case").mkdir()
    (tmp_path / "case" / "stray.txt").write_text("x")
    with pytest.raises(CaseError, match="non-empty"):
        new_case(tmp_path)


def test_open_rejects_non_case_directory(tmp_path):
    with pytest.raises(CaseError):
        CaseStore.open(tmp_path, "examiner-1")


def test_artifacts_are_content_addressed_and_verified(tmp_path):
    store = new_case(tmp_path)
    refs = []
    with store.new_artifact("es", created_utc="2026-09-15T10:00:00.000000Z", result=refs) as w:
        w.write(b"hello ")
        w.write(memoryview(b"world"))
    ref = refs[0]
    assert ref.path == store.artifact_path(ref.sha256)
    assert ref.path.read_bytes() == b"hello world"
    assert store.verify().ok

    ref.path.write_bytes(b"hello w0rld")  # tamper with a stored artefact
    result = store.verify()
    assert not result.ok
    assert any("does not match its SHA-256" in p for p in result.problems)
    store.close()


def test_failed_artifact_write_leaves_nothing_recorded(tmp_path):
    store = new_case(tmp_path)
    with (
        pytest.raises(OSError),
        store.new_artifact("es", created_utc="2026-09-15T10:00:00.000000Z") as w,
    ):
        w.write(b"partial")
        raise OSError(28, "No space left on device")
    assert store.conn.execute("SELECT COUNT(*) FROM artifact").fetchone()[0] == 0
    assert list((tmp_path / "case" / "artifacts" / "tmp").iterdir()) == []
    store.close()


def test_truncating_the_chain_tail_is_caught_by_the_manifest(tmp_path):
    store = new_case(tmp_path)
    store.audit.append("note.add", "ok", params={"text": "x"})
    store.write_manifest()
    store.conn.execute("DROP TRIGGER audit_no_delete")
    store.conn.execute("DELETE FROM audit WHERE seq = 2")
    result = store.verify()
    assert result.chain.ok  # the remaining chain is internally consistent …
    assert not result.ok  # … but the published head digest exposes the truncation
    assert any("removed from the end" in p for p in result.problems)
    store.close()


def test_reopen_and_keep_appending(tmp_path):
    new_case(tmp_path).close()
    store = CaseStore.open(tmp_path / "case", "examiner-2", clock=fixed_clock())
    store.audit.append("case.open", "ok")
    records = list(store.audit.records())
    assert records[-1].operator == "examiner-2"
    assert store.audit.verify().ok
    store.close()


def test_create_requires_a_case_id(tmp_path):
    with pytest.raises(CaseError, match="case ID is required"):
        CaseStore.create(tmp_path / "case", CaseMeta(case_id="  "), "examiner-1")


def test_open_refuses_an_unsupported_schema_version(tmp_path):
    new_case(tmp_path).close()
    conn = sqlite3.connect(tmp_path / "case" / "case.db")
    conn.execute("PRAGMA user_version=99")
    conn.close()
    with pytest.raises(CaseError, match="schema version 99"):
        CaseStore.open(tmp_path / "case", "examiner-1")


def test_identical_artifacts_are_stored_once(tmp_path):
    store = new_case(tmp_path)
    source = tmp_path / "clip.bin"
    source.write_bytes(b"same bytes" * 1000)
    a = store.add_artifact_file(source, "es", created_utc="t1")
    b = store.add_artifact_file(source, "es", created_utc="t2")
    assert a.path == b.path and a.sha256 == b.sha256
    assert store.conn.execute("SELECT COUNT(*) FROM artifact").fetchone()[0] == 1
    assert list((tmp_path / "case" / "artifacts" / "tmp").iterdir()) == []
    store.close()


def test_verify_reports_a_missing_artifact(tmp_path):
    store = new_case(tmp_path)
    with store.new_artifact("es", created_utc="t", result=(refs := [])) as writer:
        writer.write(b"payload")
    refs[0].path.unlink()
    result = store.verify()
    assert not result.ok
    assert any(p.startswith("artefact missing") for p in result.problems)
    store.close()


def test_verify_detects_a_manifest_head_digest_that_does_not_match(tmp_path):
    store = new_case(tmp_path)
    manifest_path = tmp_path / "case" / "case.manifest.json"
    manifest = json.loads(manifest_path.read_text("utf-8"))
    manifest["audit"]["head_digest"] = "f" * 64
    manifest_path.write_text(json.dumps(manifest), "utf-8")
    result = store.verify()
    assert not result.ok
    assert any("does not match the audit log" in p for p in result.problems)
    store.close()


def test_verify_warns_when_the_manifest_is_behind_an_interrupted_operation(tmp_path):
    store = new_case(tmp_path)
    store.audit.append("export.start", "started", target="REC-0001")  # crash before manifest
    result = store.verify()
    assert result.ok
    assert any("manifest is behind" in w for w in result.warnings)
    store.close()


def test_verify_reports_an_unreadable_manifest(tmp_path):
    store = new_case(tmp_path)
    (tmp_path / "case" / "case.manifest.json").write_text("{ not json", "utf-8")
    result = store.verify()
    assert not result.ok
    assert any("manifest unreadable" in p for p in result.problems)
    store.close()


def test_transaction_rolls_back_on_error(tmp_path):
    store = new_case(tmp_path)
    with pytest.raises(RuntimeError), store.transaction() as conn:
        conn.execute("INSERT INTO evidence (id, provenance_class) VALUES ('EV-001', 'D')")
        raise RuntimeError("abort")
    assert store.conn.execute("SELECT COUNT(*) FROM evidence").fetchone()[0] == 0
    assert not store.conn.in_transaction
    store.close()


def test_schema_rejects_invalid_provenance_class(tmp_path):
    store = new_case(tmp_path)
    with pytest.raises(sqlite3.IntegrityError):
        store.conn.execute("INSERT INTO evidence (id, provenance_class) VALUES ('EV-001', 'E')")
    store.close()
