"""Where an examination stands, per evidence item and for the case (doc 7 §4 steps 2–11).

The desktop app's step list and its "next step" come from here, so they reflect what the
case records rather than what one window happens to remember. Read-only: a few queries
over `case.db`, nothing written and nothing audited, so it can be asked after every step.

It reports what has been run and what exists. It does not judge whether that is enough:
an evidence item with no clock offset is reported as having none, which is a legitimate
outcome (FR-53), not an unfinished one.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from spectra.core.casestore import CaseStore

TIERS = ("T1", "T2", "T3", "T4")


def case_progress(store: CaseStore) -> dict[str, Any]:
    conn = store.conn
    recovers = _last_complete(conn, "recover.complete")
    evidence = [
        _evidence_progress(conn, ev_id, recovers.get(ev_id))
        for (ev_id,) in conn.execute("SELECT id FROM evidence ORDER BY id")
    ]
    reports = conn.execute(
        "SELECT COUNT(*), MAX(ts_utc) FROM audit WHERE action = 'report.generate.complete'"
    ).fetchone()
    return {
        "attachments": _rows(
            conn,
            "SELECT id, kind, filename, description, provided_by, statutory_ref, sha256,"
            " size_bytes, evidence_id, attached_utc FROM attachment ORDER BY id",
        ),
        "evidence": evidence,
        "exported_recordings": _count(
            conn, "SELECT COUNT(DISTINCT recording_id) FROM artifact WHERE kind = 'es'"
        ),
        "annotations": _count(conn, "SELECT COUNT(*) FROM annotation"),
        "analysed_recordings": _count(
            conn, "SELECT COUNT(DISTINCT recording_id) FROM annotation"
        ),
        "reports_generated": reports[0],
        "last_report_utc": reports[1],
        "certificates_prepared": _count(
            conn, "SELECT COUNT(*) FROM audit WHERE action = 'report.certificate.complete'"
        ),
    }


def _evidence_progress(
    conn: sqlite3.Connection, ev_id: str, last_recover: dict[str, Any] | None
) -> dict[str, Any]:
    ident = conn.execute(
        "SELECT status, family, layout_version, parse_supported FROM identification"
        " WHERE evidence_id = ?",
        (ev_id,),
    ).fetchone()
    identification = None
    if ident is not None:
        status, family, layout, parse_supported = ident
        if family is None:
            support = "pending_selection" if status == "ambiguous" else "none"
        else:
            support = "parse" if parse_supported else "carve_only"
        identification = {"status": status, "support": support, "family": family,
                          "layout_version": layout}

    by_tier = dict.fromkeys(TIERS, 0)
    for tier, n in conn.execute(
        "SELECT recovery_tier, COUNT(*) FROM recording WHERE evidence_id = ?"
        " GROUP BY recovery_tier",
        (ev_id,),
    ):
        by_tier[tier] = n
    timed = conn.execute(
        "SELECT COUNT(t_local_start), COUNT(t_ref_start) FROM recording WHERE evidence_id = ?",
        (ev_id,),
    ).fetchone()

    return {
        "evidence_id": ev_id,
        "custody_entries": _count(conn, "SELECT COUNT(*) FROM custody WHERE evidence_id = ?",
                                  ev_id),
        "identification": identification,
        "parsed": _count(conn, "SELECT COUNT(*) FROM disk_layout WHERE evidence_id = ?",
                         ev_id) > 0,
        "time_observations": _count(
            conn, "SELECT COUNT(*) FROM time_observation WHERE evidence_id = ?", ev_id
        ),
        "recovered": last_recover is not None,
        "last_recover": last_recover,
        "recordings": {
            "total": sum(by_tier.values()),
            "by_tier": by_tier,
            "with_device_time": timed[0],
            "with_reference_time": timed[1],
        },
    }


def _last_complete(conn: sqlite3.Connection, action: str) -> dict[str, dict[str, Any]]:
    """The params of the latest `action` record for each target."""
    latest: dict[str, dict[str, Any]] = {}
    for target, params in conn.execute(
        "SELECT target, params_json FROM audit WHERE action = ? ORDER BY seq", (action,)
    ):
        latest[target] = json.loads(params)
    return latest


def _count(conn: sqlite3.Connection, sql: str, *args: Any) -> int:
    return int(conn.execute(sql, args).fetchone()[0])


def _rows(conn: sqlite3.Connection, sql: str) -> list[dict[str, Any]]:
    cursor = conn.execute(sql)
    names = [c[0] for c in cursor.description]
    return [dict(zip(names, row, strict=True)) for row in cursor]
