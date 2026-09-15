"""`CaseStore` — one portable directory per case (doc 3 §3.4, §11; FR-70).

```
<case>/
├── case.db               SQLite (WAL), schema below
├── artifacts/ab/cd/<sha256>   content-addressed store
├── images/               acquired images (finals, Phase 2)
├── logs/                 tool logs, FFmpeg transcripts
└── case.manifest.json    head digest, versions, integrity summary
```

Schema additions over doc 3 §11 (needed to reopen evidence and to record FR-01/FR-02
transparency): `evidence.source_path/source_format/members_json/gaps_json`,
`identification.status/selection/probe_plan_json/observations_json/errors_json`,
`recording.stream/frame_count/notes_json`, `artifact.size_bytes/source_record_seq`.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import spectra
from spectra.core.audit import AuditChain, VerifyResult, utc_now
from spectra.core.hashing import Digests, Hasher, hash_case_file

SCHEMA_VERSION = 1
DB_NAME = "case.db"
MANIFEST_NAME = "case.manifest.json"
MANIFEST_FORMAT = "spectra-case-manifest/1"

SCHEMA = """
CREATE TABLE case_meta (
  case_id TEXT PRIMARY KEY, title TEXT, agency TEXT, fir_ref TEXT,
  authority_ref TEXT, examiner_name TEXT, examiner_designation TEXT,
  examiner_s79a_ref TEXT, created_utc TEXT
);
CREATE TABLE evidence (
  id TEXT PRIMARY KEY, case_id TEXT REFERENCES case_meta,
  kind TEXT, provenance_class TEXT CHECK (provenance_class IN ('A','B','C','D')),
  label TEXT, device_make TEXT, device_model TEXT, device_serial TEXT,
  disk_make TEXT, disk_model TEXT, disk_serial TEXT, capacity_bytes INTEGER,
  hpa_present INTEGER, dco_present INTEGER, hidden_sectors INTEGER,
  md5 TEXT, sha256 TEXT, acquired_utc TEXT, acquired_by TEXT,
  write_blocker TEXT, notes TEXT,
  source_path TEXT, source_format TEXT, members_json TEXT, gaps_json TEXT
);
CREATE TABLE identification (
  evidence_id TEXT PRIMARY KEY REFERENCES evidence,
  family TEXT, layout_version TEXT, confidence REAL,
  parse_supported INTEGER, matched_signature_hex TEXT, matched_offsets TEXT,
  candidates_json TEXT, brand_inferred TEXT, brand_evidence TEXT,
  status TEXT, selection TEXT, probe_plan_json TEXT, observations_json TEXT, errors_json TEXT
);
CREATE TABLE disk_layout (
  evidence_id TEXT PRIMARY KEY REFERENCES evidence,
  block_size INTEGER, block_count INTEGER, blocks_used INTEGER,
  index_extents_json TEXT, log_extents_json TEXT, format_utc TEXT,
  family TEXT, layout_version TEXT, note TEXT
);
CREATE TABLE recording (
  id TEXT PRIMARY KEY, evidence_id TEXT REFERENCES evidence,
  channel INTEGER, channel_name TEXT,
  stream TEXT, codec TEXT, width INTEGER, height INTEGER, fps REAL,
  t_device_raw TEXT, t_device_encoding TEXT,
  t_local_start TEXT, t_local_end TEXT,
  t_ref_start TEXT, t_ref_end TEXT, t_uncertainty_s REAL, t_method TEXT,
  extents_json TEXT, size_bytes INTEGER,
  recovery_tier TEXT, confidence REAL, source_note TEXT,
  frame_count INTEGER, notes_json TEXT
);
CREATE TABLE artifact (
  sha256 TEXT PRIMARY KEY, md5 TEXT, path TEXT, kind TEXT,
  recording_id TEXT REFERENCES recording, is_derivative INTEGER,
  created_utc TEXT, tool_versions_json TEXT, size_bytes INTEGER, source_record_seq INTEGER
);
CREATE TABLE coverage (
  evidence_id TEXT, offset INTEGER, length INTEGER,
  bucket TEXT CHECK (bucket IN ('parsed','carved','structural','unreadable','unaccounted'))
);
CREATE TABLE device_event (
  evidence_id TEXT, t_device TEXT, t_ref TEXT, kind TEXT, detail TEXT
);
CREATE TABLE annotation (
  id TEXT PRIMARY KEY, recording_id TEXT REFERENCES recording,
  frame_no INTEGER, t_ref TEXT, bbox_json TEXT, label TEXT, score REAL,
  source TEXT, model_name TEXT, model_sha256 TEXT, note TEXT
);
CREATE TABLE custody (
  seq INTEGER PRIMARY KEY, evidence_id TEXT, ts_utc TEXT,
  from_holder TEXT, to_holder TEXT, purpose TEXT, signature_ref TEXT
);
"""


class CaseError(Exception):
    """The case directory is missing, malformed, or the request conflicts with it."""


@dataclass(frozen=True, slots=True)
class CaseMeta:
    case_id: str
    title: str = ""
    agency: str = ""
    fir_ref: str = ""
    authority_ref: str = ""
    examiner_name: str = ""
    examiner_designation: str = ""
    examiner_s79a_ref: str = ""


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    sha256: str
    md5: str
    size: int
    path: Path
    kind: str


@dataclass
class ArtifactWriter:
    """Streams bytes to a temp file while hashing; finalised into the CAS on success."""

    handle: Any
    hasher: Hasher = field(default_factory=Hasher)

    def write(self, data: bytes | bytearray | memoryview) -> None:
        self.handle.write(data)
        self.hasher.update(data)


@dataclass(frozen=True, slots=True)
class CaseVerifyResult:
    ok: bool
    chain: VerifyResult
    artifacts_checked: int
    problems: tuple[str, ...]
    warnings: tuple[str, ...]


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=FULL")  # audit records are durable before we proceed
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


class CaseStore:
    def __init__(self, root: Path, conn: sqlite3.Connection, audit: AuditChain) -> None:
        self.root = root
        self.conn = conn
        self.audit = audit

    # -- lifecycle --------------------------------------------------------------------
    @classmethod
    def create(
        cls,
        root: Path | str,
        meta: CaseMeta,
        operator: str,
        clock: Callable[[], datetime] = utc_now,
    ) -> CaseStore:
        root = Path(root)
        if not meta.case_id.strip():
            raise CaseError("case ID is required")
        if root.exists() and any(root.iterdir()):
            raise CaseError(f"refusing to create a case in a non-empty directory: {root}")
        for sub in ("artifacts", "images", "logs"):
            (root / sub).mkdir(parents=True, exist_ok=True)
        conn = _connect(root / DB_NAME)
        conn.executescript(SCHEMA)
        conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        store = cls(root, conn, AuditChain(conn, operator, clock))
        with store.audit.operation(
            "case.create", target=meta.case_id, params={"meta": _meta_json(meta)}
        ) as op:
            assert op.start_record is not None
            conn.execute(
                "INSERT INTO case_meta VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    meta.case_id, meta.title, meta.agency, meta.fir_ref, meta.authority_ref,
                    meta.examiner_name, meta.examiner_designation, meta.examiner_s79a_ref,
                    op.start_record.ts_utc,
                ),
            )
            op.result_params = {"schema_version": SCHEMA_VERSION}
        store.write_manifest()
        return store

    @classmethod
    def open(
        cls,
        root: Path | str,
        operator: str,
        clock: Callable[[], datetime] = utc_now,
    ) -> CaseStore:
        root = Path(root)
        db = root / DB_NAME
        if not db.is_file():
            raise CaseError(f"not a SPECTRA case directory (no {DB_NAME}): {root}")
        conn = _connect(db)
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version != SCHEMA_VERSION:
            conn.close()
            raise CaseError(
                f"case schema version {version} is not supported (need {SCHEMA_VERSION})"
            )
        return cls(root, conn, AuditChain(conn, operator, clock))

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> CaseStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield self.conn
        except BaseException:
            self.conn.rollback()
            raise
        self.conn.commit()

    # -- metadata ---------------------------------------------------------------------
    @property
    def case_id(self) -> str:
        return str(self.conn.execute("SELECT case_id FROM case_meta").fetchone()[0])

    def meta(self) -> dict[str, Any]:
        cursor = self.conn.execute("SELECT * FROM case_meta")
        row = cursor.fetchone()
        return dict(zip([c[0] for c in cursor.description], row, strict=True))

    def next_id(self, prefix: str, table: str, width: int = 3) -> str:
        count = self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        return f"{prefix}-{count + 1:0{width}d}"

    # -- artefacts (content-addressed) ------------------------------------------------
    def artifact_path(self, sha256: str) -> Path:
        return self.root / "artifacts" / sha256[:2] / sha256[2:4] / sha256

    @contextmanager
    def new_artifact(
        self,
        kind: str,
        *,
        recording_id: str | None = None,
        is_derivative: bool = False,
        tool_versions: dict[str, str] | None = None,
        created_utc: str,
        source_record_seq: int | None = None,
        result: list[ArtifactRef] | None = None,
    ) -> Iterator[ArtifactWriter]:
        """Write an artefact by streaming. Nothing is recorded unless the block completes,
        so a failed export (e.g. disk full) never leaves a "complete" artefact (TC-RB-08).
        The finalised `ArtifactRef` is appended to `result` when given."""
        tmp_dir = self.root / "artifacts" / "tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=tmp_dir)
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                writer = ArtifactWriter(handle)
                yield writer
                handle.flush()
                os.fsync(handle.fileno())
            ref = self._finalise(
                tmp, writer.hasher.digests(), kind, recording_id, is_derivative,
                tool_versions or {}, created_utc, source_record_seq,
            )
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        if result is not None:
            result.append(ref)

    def add_artifact_file(
        self,
        path: Path,
        kind: str,
        *,
        created_utc: str,
        recording_id: str | None = None,
        is_derivative: bool = False,
        tool_versions: dict[str, str] | None = None,
        source_record_seq: int | None = None,
    ) -> ArtifactRef:
        """Copy a case-owned file into the CAS (never an evidence path)."""
        refs: list[ArtifactRef] = []
        with (
            open(path, "rb") as src,
            self.new_artifact(
                kind, recording_id=recording_id, is_derivative=is_derivative,
                tool_versions=tool_versions, created_utc=created_utc,
                source_record_seq=source_record_seq, result=refs,
            ) as writer,
        ):
            shutil.copyfileobj(src, writer)  # type: ignore[misc]
        return refs[0]

    def _finalise(
        self,
        tmp: Path,
        digests: Digests,
        kind: str,
        recording_id: str | None,
        is_derivative: bool,
        tool_versions: dict[str, str],
        created_utc: str,
        source_record_seq: int | None,
    ) -> ArtifactRef:
        final = self.artifact_path(digests.sha256)
        final.parent.mkdir(parents=True, exist_ok=True)
        if final.exists():
            tmp.unlink()  # identical content already stored — dedup by hash
        else:
            os.replace(tmp, final)
        rel = final.relative_to(self.root).as_posix()
        self.conn.execute(
            "INSERT OR IGNORE INTO artifact (sha256, md5, path, kind, recording_id, is_derivative,"
            " created_utc, tool_versions_json, size_bytes, source_record_seq)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                digests.sha256, digests.md5, rel, kind, recording_id, int(is_derivative),
                created_utc, json.dumps(tool_versions, sort_keys=True), digests.size,
                source_record_seq,
            ),
        )
        return ArtifactRef(digests.sha256, digests.md5, digests.size, final, kind)

    # -- manifest and verification ----------------------------------------------------
    def manifest(self) -> dict[str, Any]:
        head_seq, head_digest = self.audit.head()
        evidence = [
            {"id": row[0], "md5": row[1], "sha256": row[2], "size": row[3]}
            for row in self.conn.execute(
                "SELECT id, md5, sha256, capacity_bytes FROM evidence ORDER BY id"
            )
        ]
        artifact_count = self.conn.execute("SELECT COUNT(*) FROM artifact").fetchone()[0]
        return {
            "format": MANIFEST_FORMAT,
            "case_id": self.case_id,
            "schema_version": SCHEMA_VERSION,
            "spectra_version": spectra.__version__,
            "audit": {"head_seq": head_seq, "head_digest": head_digest},
            "evidence": evidence,
            "artifact_count": artifact_count,
        }

    def write_manifest(self) -> None:
        target = self.root / MANIFEST_NAME
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.manifest(), indent=2, sort_keys=True) + "\n", "utf-8")
        os.replace(tmp, target)

    def verify(self) -> CaseVerifyResult:
        """FR-72 plus artefact hashes: what `spectra case verify` runs."""
        chain = self.audit.verify()
        problems: list[str] = []
        warnings: list[str] = list(chain.warnings)
        if not chain.ok:
            problems.append(f"audit chain broken at seq {chain.broken_at_seq}: {chain.reason}")

        manifest_path = self.root / MANIFEST_NAME
        try:
            manifest = json.loads(manifest_path.read_text("utf-8"))
            m_seq = manifest["audit"]["head_seq"]
            m_digest = manifest["audit"]["head_digest"]
        except (OSError, ValueError, KeyError, TypeError) as exc:
            problems.append(f"case manifest unreadable: {exc}")
        else:
            head_seq, _ = self.audit.head()
            if m_seq is not None:
                stored = self.audit.digest_at(m_seq)
                if head_seq is None or m_seq > head_seq or stored is None:
                    problems.append(
                        f"manifest records audit head seq {m_seq} but the log ends at "
                        f"{head_seq} — records were removed from the end of the chain"
                    )
                elif stored != m_digest:
                    problems.append(
                        f"manifest head digest for seq {m_seq} does not match the audit log"
                    )
                elif m_seq < head_seq:
                    warnings.append(
                        f"manifest is behind the audit log (seq {m_seq} < {head_seq}); "
                        "an operation was interrupted before the manifest was rewritten"
                    )

        checked = 0
        for sha256, rel in self.conn.execute("SELECT sha256, path FROM artifact ORDER BY sha256"):
            checked += 1
            path = self.root / rel
            if not path.is_file():
                problems.append(f"artefact missing: {rel}")
                continue
            if hash_case_file(path).sha256 != sha256:
                problems.append(f"artefact content does not match its SHA-256: {rel}")
        return CaseVerifyResult(not problems, chain, checked, tuple(problems), tuple(warnings))


def _meta_json(meta: CaseMeta) -> dict[str, str]:
    return {
        "case_id": meta.case_id, "title": meta.title, "agency": meta.agency,
        "fir_ref": meta.fir_ref, "authority_ref": meta.authority_ref,
        "examiner_name": meta.examiner_name,
        "examiner_designation": meta.examiner_designation,
        "examiner_s79a_ref": meta.examiner_s79a_ref,
    }
