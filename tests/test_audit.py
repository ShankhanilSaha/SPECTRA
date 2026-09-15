"""AuditChain tamper detection (FR-71, FR-72, AC-08, TC-IN-01..05, TC-IN-07)."""

from __future__ import annotations

import json
import random
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from spectra.core.audit import (
    GENESIS_DIGEST,
    AuditChain,
    AuditRecord,
    CanonicalisationError,
    _with_digest,
    canonical_json,
    format_ts,
    verify_records,
)


def fixed_clock(start: datetime = datetime(2026, 9, 15, 10, 0, tzinfo=UTC)):
    state = {"now": start}

    def tick() -> datetime:
        state["now"] += timedelta(seconds=1)
        return state["now"]

    return tick


def make_chain(n: int = 20) -> tuple[sqlite3.Connection, AuditChain]:
    conn = sqlite3.connect(":memory:", isolation_level=None)
    chain = AuditChain(conn, "examiner-1", clock=fixed_clock())
    for i in range(n):
        chain.append(
            f"test.step{i % 3}",
            ["started", "ok", "error"][i % 3],
            target=f"EV-{i:03d}",
            params={"i": i, "ratio": i / 7, "tags": ["a", "b"], "nested": {"z": 1, "a": None}},
            hash_before="aa" * 32 if i % 2 else None,
            hash_after="bb" * 32,
        )
    return conn, chain


def drop_triggers(conn: sqlite3.Connection) -> None:
    """Simulate a determined party with sqlite3 — the triggers only stop accidents."""
    conn.execute("DROP TRIGGER audit_no_update")
    conn.execute("DROP TRIGGER audit_no_delete")


# -- canonical JSON (TC-IN-05) -----------------------------------------------------------

def test_canonical_json_is_key_order_and_whitespace_independent():
    a = {"b": 1, "a": [1.5, {"y": 2, "x": "ü"}]}
    b = json.loads('{ "a" : [ 1.50 , { "x" : "ü", "y" : 2 } ], "b" : 1 }')
    assert canonical_json(a) == canonical_json(b) == '{"a":[1.5,{"x":"ü","y":2}],"b":1}'.encode()


def test_canonical_json_folds_negative_zero_and_refuses_ambiguous_values():
    assert canonical_json({"v": -0.0}) == canonical_json({"v": 0.0})
    for bad in (float("nan"), float("inf"), b"bytes", datetime.now(UTC), {1: "int key"}):
        with pytest.raises(CanonicalisationError):
            canonical_json({"v": bad})


# -- chain behaviour ---------------------------------------------------------------------

def test_intact_chain_verifies():
    _, chain = make_chain()
    result = chain.verify()
    assert result.ok, result.reason
    assert result.records_checked == 20
    assert result.head_seq == 19
    assert result.warnings == ()


def test_triggers_block_update_and_delete():
    conn, _ = make_chain(3)
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("UPDATE audit SET operator='x' WHERE seq=1")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("DELETE FROM audit WHERE seq=1")


def test_missing_triggers_are_reported():
    conn, chain = make_chain(3)
    drop_triggers(conn)
    result = chain.verify()
    assert result.ok
    assert any("triggers" in w for w in result.warnings)


def test_reopening_a_log_does_not_restore_dropped_triggers():
    conn, _ = make_chain(3)
    drop_triggers(conn)
    reopened = AuditChain(conn, "examiner-2", clock=fixed_clock())  # e.g. a later session
    assert any("triggers" in w for w in reopened.verify().warnings)


FIELDS = ["ts_utc", "operator", "action", "target", "params_json", "result",
          "hash_before", "hash_after", "prev_digest", "digest"]


def mutate(conn: sqlite3.Connection, rng: random.Random, seq: int, column: str) -> None:
    (value,) = conn.execute(f"SELECT {column} FROM audit WHERE seq=?", (seq,)).fetchone()
    if column == "params_json":
        params = json.loads(value)
        params["i"] = params["i"] + rng.randint(1, 1000)
        new = json.dumps(params)
    elif value is None:
        new = "injected"
    else:
        chars = list(value)
        pos = rng.randrange(len(chars))
        chars[pos] = "Z" if chars[pos] != "Z" else "Y"
        new = "".join(chars)
    conn.execute(f"UPDATE audit SET {column}=? WHERE seq=?", (new, seq))


def test_tc_in_01_random_field_tamper_detected_100_of_100():
    rng = random.Random(0x5EC7)
    detected = 0
    for _ in range(100):
        conn, chain = make_chain()
        drop_triggers(conn)
        seq = rng.randrange(20)
        column = rng.choice(FIELDS)
        mutate(conn, rng, seq, column)
        result = chain.verify()
        if not result.ok and result.broken_at_seq in (seq, seq + 1):
            detected += 1
    assert detected == 100


def test_tc_in_02_deleting_a_middle_record_is_detected():
    conn, chain = make_chain()
    drop_triggers(conn)
    conn.execute("DELETE FROM audit WHERE seq=7")
    result = chain.verify()
    assert not result.ok
    assert result.broken_at_seq == 8


def test_tc_in_03_inserting_a_forged_record_is_detected():
    conn, chain = make_chain()
    drop_triggers(conn)
    rows = conn.execute("SELECT * FROM audit WHERE seq >= 10 ORDER BY seq DESC").fetchall()
    for row in rows:  # shift seq 10.. up by one to make room
        conn.execute("UPDATE audit SET seq=? WHERE seq=?", (row[0] + 1, row[0]))
    forged = conn.execute("SELECT * FROM audit WHERE seq=9").fetchone()
    conn.execute(
        "INSERT INTO audit VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (10, forged[1], "attacker", "export.complete", *forged[4:]),
    )
    result = chain.verify()
    assert not result.ok
    assert result.broken_at_seq == 10


def test_tc_in_04_swapping_two_records_is_detected():
    conn, chain = make_chain()
    drop_triggers(conn)
    a = conn.execute("SELECT * FROM audit WHERE seq=4").fetchone()
    b = conn.execute("SELECT * FROM audit WHERE seq=5").fetchone()
    conn.execute("DELETE FROM audit WHERE seq IN (4, 5)")
    conn.execute("INSERT INTO audit VALUES (?,?,?,?,?,?,?,?,?,?,?)", (4, *b[1:]))
    conn.execute("INSERT INTO audit VALUES (?,?,?,?,?,?,?,?,?,?,?)", (5, *a[1:]))
    result = chain.verify()
    assert not result.ok
    assert result.broken_at_seq == 4


def test_tc_in_07_crash_mid_operation_leaves_started_record_and_chain_verifies():
    conn = sqlite3.connect(":memory:", isolation_level=None)
    chain = AuditChain(conn, "examiner-1", clock=fixed_clock())
    with pytest.raises(RuntimeError), chain.operation("export", target="REC-0001") as op:
        op.result_params["bytes_written"] = 4096
        raise RuntimeError("disk full")
    # A hard crash is simulated by a start record with no terminal record at all.
    chain.append("parse.start", "started", target="EV-001")
    actions = [(r.action, r.result) for r in chain.records()]
    assert actions == [
        ("export.start", "started"),
        ("export.error", "error"),
        ("parse.start", "started"),
    ]
    errors = [r for r in chain.records() if r.result == "error"]
    assert errors[0].params["error"] == "disk full"
    assert errors[0].params["start_seq"] == 0
    assert chain.verify().ok


def test_operation_writes_start_and_complete_with_outcome():
    conn = sqlite3.connect(":memory:", isolation_level=None)
    chain = AuditChain(conn, "examiner-1", clock=fixed_clock())
    with chain.operation("identify", target="EV-001", params={"plan": "default"}) as op:
        op.result_params = {"family": "dahua"}
        op.hash_after = "cc" * 32
    start, complete = list(chain.records())
    assert (start.action, start.result) == ("identify.start", "started")
    assert (complete.action, complete.result) == ("identify.complete", "ok")
    assert complete.params == {"start_seq": 0, "family": "dahua"}
    assert complete.hash_after == "cc" * 32


def test_append_input_checks_leave_the_log_untouched():
    conn = sqlite3.connect(":memory:", isolation_level=None)
    with pytest.raises(ValueError, match="operator identity"):
        AuditChain(conn, "")
    chain = AuditChain(conn, "examiner-1", clock=fixed_clock())
    with pytest.raises(ValueError, match="result must be one of"):
        chain.append("x", "done")
    with pytest.raises(CanonicalisationError):
        chain.append("x", "ok", params={"when": datetime.now(UTC)})
    assert list(chain.records()) == []
    with pytest.raises(ValueError, match="timezone-aware"):
        format_ts(datetime(2026, 9, 15, 10, 0))


def test_append_joins_the_callers_transaction():
    conn = sqlite3.connect(":memory:", isolation_level=None)
    chain = AuditChain(conn, "examiner-1", clock=fixed_clock())
    conn.execute("BEGIN IMMEDIATE")
    chain.append("inside", "ok")
    assert conn.in_transaction  # append did not commit someone else's transaction
    conn.rollback()
    assert list(chain.records()) == []


def test_failed_insert_rolls_back_and_leaves_no_open_transaction():
    conn = sqlite3.connect(":memory:", isolation_level=None)
    chain = AuditChain(conn, "examiner-1", clock=fixed_clock())
    conn.execute("CREATE TRIGGER fail_insert BEFORE INSERT ON audit "
                 "BEGIN SELECT RAISE(ABORT, 'disk I/O error'); END")
    with pytest.raises(sqlite3.DatabaseError, match="disk I/O error"):
        chain.append("x", "ok")
    assert not conn.in_transaction
    conn.execute("DROP TRIGGER fail_insert")
    assert chain.append("y", "ok").seq == 0


def test_error_record_survives_non_canonical_outcome_params():
    conn = sqlite3.connect(":memory:", isolation_level=None)
    chain = AuditChain(conn, "examiner-1", clock=fixed_clock())
    with pytest.raises(KeyError), chain.operation("parse", target="EV-001") as op:
        op.result_params = {"started_at": datetime.now(UTC)}  # not JSON
        raise KeyError("boom")
    error = list(chain.records())[-1]
    assert error.action == "parse.error"
    assert error.params["result_params_dropped"] == "not canonicalisable"
    assert error.params["error_type"] == "KeyError"
    assert chain.verify().ok


def test_params_that_no_longer_canonicalise_count_as_modified():
    conn, chain = make_chain(3)
    drop_triggers(conn)
    conn.execute("UPDATE audit SET params_json='{\"i\": NaN}' WHERE seq=1")
    result = chain.verify()
    assert not result.ok and result.broken_at_seq == 1
    assert "modified" in result.reason


def test_unexpected_result_value_with_valid_digest_is_flagged():
    first = _with_digest(AuditRecord(0, "2026-09-15T10:00:00.000000Z", "e", "a", None, {},
                                     "maybe", None, None, GENESIS_DIGEST))
    result = verify_records(iter([first]))
    assert result.ok
    assert any("unexpected result value 'maybe'" in w for w in result.warnings)


def test_clock_regression_is_a_warning_not_a_silent_fix():
    times = iter([datetime(2026, 9, 15, 10, 0, 5, tzinfo=UTC),
                  datetime(2026, 9, 15, 10, 0, 1, tzinfo=UTC)])
    conn = sqlite3.connect(":memory:", isolation_level=None)
    chain = AuditChain(conn, "examiner-1", clock=lambda: next(times))
    chain.append("a", "ok")
    chain.append("b", "ok")
    result = chain.verify()
    assert result.ok
    assert any("earlier than the previous" in w for w in result.warnings)
    # The record keeps the clock reading it actually got; nothing is fabricated.
    assert [r.ts_utc for r in chain.records()][1] == "2026-09-15T10:00:01.000000Z"
