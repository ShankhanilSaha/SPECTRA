"""`AuditChain` — append-only, hash-chained audit log (doc 3 §3.3, D3, FR-71, FR-72).

Each record's digest is `sha256(canonical_json(record without digest))` and every record
carries the previous record's digest, so any insertion, deletion, reordering or field edit
breaks the chain at a locatable point (AC-08, TC-IN-01..04). Records are written
synchronously, before and after each state-changing operation, so a crash leaves a visible
"started" record rather than a silent hole (TC-IN-07).

The SQLite UPDATE/DELETE triggers stop accidents. They do not stop a determined party with
sqlite3 — that is what the chain and the published head digest are for.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

GENESIS_DIGEST = "0" * 64
RESULTS = ("started", "ok", "error")

AUDIT_DDL = """
CREATE TABLE IF NOT EXISTS audit (
  seq INTEGER PRIMARY KEY, ts_utc TEXT NOT NULL, operator TEXT NOT NULL, action TEXT NOT NULL,
  target TEXT, params_json TEXT NOT NULL, result TEXT NOT NULL,
  hash_before TEXT, hash_after TEXT, prev_digest TEXT NOT NULL, digest TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit
BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit
BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;
"""


class CanonicalisationError(TypeError):
    """A value has no single canonical JSON form and is refused."""


def _normalise(value: Any, path: str) -> Any:
    if value is None or isinstance(value, bool | str):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CanonicalisationError(f"{path}: non-finite float has no canonical form")
        return 0.0 if value == 0 else value  # fold -0.0 into 0.0
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise CanonicalisationError(f"{path}: object keys must be strings")
            out[key] = _normalise(item, f"{path}.{key}")
        return out
    if isinstance(value, list | tuple):
        return [_normalise(item, f"{path}[{i}]") for i, item in enumerate(value)]
    raise CanonicalisationError(
        f"{path}: {type(value).__name__} is not a JSON type — convert it explicitly "
        "(hex for bytes, RFC 3339 UTC string for datetimes)"
    )


def canonical_json(value: Any) -> bytes:
    """Canonical JSON (doc 3 §3.3): sorted keys, no insignificant whitespace, UTF-8,
    floats as shortest round-trip repr with -0.0 folded and NaN/Inf refused.

    Integers above 2**53 are exact here but not in every JSON implementation; audit params
    should keep large values (e.g. byte offsets beyond 8 PiB) as strings.
    """
    normalised = _normalise(value, "$")
    text = json.dumps(
        normalised, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )
    return text.encode("utf-8")


def utc_now() -> datetime:
    return datetime.now(UTC)


def format_ts(moment: datetime) -> str:
    """RFC 3339, UTC, microsecond precision, `Z` suffix."""
    if moment.tzinfo is None:
        raise ValueError("audit timestamps must be timezone-aware")
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


@dataclass(frozen=True, slots=True)
class AuditRecord:
    seq: int
    ts_utc: str
    operator: str
    action: str
    target: str | None
    params: dict[str, Any]
    result: str
    hash_before: str | None
    hash_after: str | None
    prev_digest: str
    digest: str = ""

    def body(self) -> dict[str, Any]:
        data = asdict(self)
        del data["digest"]
        return data

    def compute_digest(self) -> str:
        return hashlib.sha256(canonical_json(self.body())).hexdigest()


@dataclass(frozen=True, slots=True)
class VerifyResult:
    ok: bool
    records_checked: int
    head_seq: int | None
    head_digest: str
    broken_at_seq: int | None = None
    reason: str = ""
    warnings: tuple[str, ...] = ()


@dataclass
class Operation:
    """Handle yielded by `AuditChain.operation`; set outcome fields before the block ends."""

    action: str
    target: str | None
    result_params: dict[str, Any] = field(default_factory=dict)
    hash_after: str | None = None
    start_record: AuditRecord | None = None


class AuditChain:
    """The case's audit log, stored in the `audit` table of `case.db`."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        operator: str,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        if not operator:
            raise ValueError("an operator identity is required for every audit record")
        self._conn = conn
        self.operator = operator
        self._clock = clock
        # Create the table and its triggers only for a new log. An existing log's schema is
        # never touched: silently re-creating dropped triggers would erase the evidence that
        # someone removed them, and verification must not write to the case (FR-85).
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='audit'"
        ).fetchone()
        if not exists:
            conn.executescript(AUDIT_DDL)

    # -- writing ----------------------------------------------------------------------
    def append(
        self,
        action: str,
        result: str,
        *,
        target: str | None = None,
        params: dict[str, Any] | None = None,
        hash_before: str | None = None,
        hash_after: str | None = None,
    ) -> AuditRecord:
        if result not in RESULTS:
            raise ValueError(f"result must be one of {RESULTS}")
        params = dict(params or {})
        canonical_json(params)  # refuse non-canonical params before touching the log
        conn = self._conn
        in_outer_txn = conn.in_transaction
        if not in_outer_txn:
            conn.execute("BEGIN IMMEDIATE")
        try:
            head_seq, head_digest = self.head()
            seq = 0 if head_seq is None else head_seq + 1
            prev = head_digest
            record = AuditRecord(
                seq=seq,
                ts_utc=format_ts(self._clock()),
                operator=self.operator,
                action=action,
                target=target,
                params=params,
                result=result,
                hash_before=hash_before,
                hash_after=hash_after,
                prev_digest=prev,
            )
            record = _with_digest(record)
            conn.execute(
                "INSERT INTO audit (seq, ts_utc, operator, action, target, params_json, result,"
                " hash_before, hash_after, prev_digest, digest)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    record.seq,
                    record.ts_utc,
                    record.operator,
                    record.action,
                    record.target,
                    canonical_json(record.params).decode("utf-8"),
                    record.result,
                    record.hash_before,
                    record.hash_after,
                    record.prev_digest,
                    record.digest,
                ),
            )
            if not in_outer_txn:
                conn.commit()
        except BaseException:
            if not in_outer_txn:
                conn.rollback()
            raise
        return record

    @contextmanager
    def operation(
        self,
        action: str,
        *,
        target: str | None = None,
        params: dict[str, Any] | None = None,
        hash_before: str | None = None,
    ) -> Iterator[Operation]:
        """Write `<action>.start`, run the block, then `<action>.complete` or `.error`."""
        op = Operation(action=action, target=target)
        op.start_record = self.append(
            f"{action}.start",
            "started",
            target=target,
            params=params,
            hash_before=hash_before,
        )
        try:
            yield op
        except BaseException as exc:
            error_params: dict[str, Any] = {
                "start_seq": op.start_record.seq,
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
            try:
                canonical_json(op.result_params)
                error_params = {**op.result_params, **error_params}
            except CanonicalisationError:
                error_params["result_params_dropped"] = "not canonicalisable"
            self.append(
                f"{action}.error", "error", target=target, params=error_params,
                hash_before=hash_before,
            )
            raise
        self.append(
            f"{action}.complete",
            "ok",
            target=target,
            params={"start_seq": op.start_record.seq, **op.result_params},
            hash_before=hash_before,
            hash_after=op.hash_after,
        )

    # -- reading ----------------------------------------------------------------------
    def records(self) -> Iterator[AuditRecord]:
        cursor = self._conn.execute(
            "SELECT seq, ts_utc, operator, action, target, params_json, result,"
            " hash_before, hash_after, prev_digest, digest FROM audit ORDER BY seq"
        )
        for row in cursor:
            yield AuditRecord(
                seq=row[0],
                ts_utc=row[1],
                operator=row[2],
                action=row[3],
                target=row[4],
                params=json.loads(row[5]),
                result=row[6],
                hash_before=row[7],
                hash_after=row[8],
                prev_digest=row[9],
                digest=row[10],
            )

    def head(self) -> tuple[int | None, str]:
        row = self._conn.execute(
            "SELECT seq, digest FROM audit ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        return (None, GENESIS_DIGEST) if row is None else (row[0], row[1])

    def digest_at(self, seq: int) -> str | None:
        row = self._conn.execute("SELECT digest FROM audit WHERE seq = ?", (seq,)).fetchone()
        return None if row is None else row[0]

    def verify(self) -> VerifyResult:
        """Walk from seq 0, recomputing every link. O(n), offline (NFR-10)."""
        return verify_records(self.records(), self._triggers_present())

    def _triggers_present(self) -> bool:
        names = {
            row[0]
            for row in self._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='audit'"
            )
        }
        return {"audit_no_update", "audit_no_delete"} <= names


def _with_digest(record: AuditRecord) -> AuditRecord:
    return AuditRecord(**{**record.body(), "digest": record.compute_digest()})


def verify_records(records: Iterator[AuditRecord], triggers_present: bool = True) -> VerifyResult:
    warnings: list[str] = []
    if not triggers_present:
        warnings.append("append-only triggers on the audit table are missing")
    expected_seq = 0
    prev_digest = GENESIS_DIGEST
    prev_ts: str | None = None
    checked = 0
    for record in records:
        if record.seq != expected_seq:
            kind = "missing" if record.seq > expected_seq else "duplicated/reordered"
            return VerifyResult(
                False, checked, expected_seq - 1 if expected_seq else None, prev_digest,
                broken_at_seq=record.seq,
                reason=f"sequence break: expected seq {expected_seq}, found {record.seq} "
                f"({kind} record)",
                warnings=tuple(warnings),
            )
        if record.prev_digest != prev_digest:
            return VerifyResult(
                False, checked, expected_seq - 1 if expected_seq else None, prev_digest,
                broken_at_seq=record.seq,
                reason=f"seq {record.seq}: prev_digest does not match the digest of seq "
                f"{record.seq - 1} (record inserted, removed or reordered before it)",
                warnings=tuple(warnings),
            )
        try:
            recomputed = record.compute_digest()
        except CanonicalisationError as exc:
            recomputed = f"<uncanonicalisable: {exc}>"
        if recomputed != record.digest:
            return VerifyResult(
                False, checked, expected_seq - 1 if expected_seq else None, prev_digest,
                broken_at_seq=record.seq,
                reason=f"seq {record.seq}: stored digest does not match the record's content "
                "(a field was modified)",
                warnings=tuple(warnings),
            )
        if record.result not in RESULTS:
            warnings.append(f"seq {record.seq}: unexpected result value {record.result!r}")
        if prev_ts is not None and record.ts_utc < prev_ts:
            warnings.append(
                f"seq {record.seq}: timestamp {record.ts_utc} is earlier than the previous "
                f"record's {prev_ts} (host clock moved backwards)"
            )
        prev_ts = record.ts_utc
        prev_digest = record.digest
        expected_seq += 1
        checked += 1
    return VerifyResult(
        True, checked, expected_seq - 1 if checked else None, prev_digest,
        warnings=tuple(warnings),
    )
