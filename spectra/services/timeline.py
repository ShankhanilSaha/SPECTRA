"""TimelineService — offset evidence in, normalised times and gap findings out
(FR-50..FR-53, FR-58, FR-60).

`time set` is the only door: it records the observation, rebuilds the device's clock
model from every observation on file, and renormalises every recording and device event of
that evidence item in one audited, atomic operation. Nothing else writes `t_ref_*`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from spectra.core.casestore import CaseStore
from spectra.core.models import OffsetMethod, ReferenceTime
from spectra.services import ServiceError
from spectra.timeline import anomalies as anom
from spectra.timeline import correlate as correlate_mod
from spectra.timeline import gaps as gaps_mod
from spectra.timeline.offset import observe
from spectra.timeline.timemodel import (
    REFUSAL_BANNER,
    ClockModel,
    OffsetObservation,
    TimeModelError,
    not_established,
)


@dataclass(frozen=True, slots=True)
class TimeSetSummary:
    evidence_id: str
    observation_id: str
    offset_note: str
    recordings_normalised: int
    recordings_refused: int


def _parse_local(value: str, name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ServiceError(f"{name}: {exc}") from exc
    if parsed.tzinfo is not None:
        raise ServiceError(f"{name} is device wall-clock time — give it without a timezone")
    return parsed


def _parse_true(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ServiceError(f"true time: {exc}") from exc
    if parsed.tzinfo is None:
        raise ServiceError(
            "true time must carry a timezone (e.g. trailing Z or +05:30) — it is the "
            "absolute reference everything is normalised to"
        )
    return parsed.astimezone(UTC)


def set_offset(
    store: CaseStore,
    evidence_id: str,
    method: OffsetMethod,
    device_time: str,
    true_time: str,
    uncertainty_s: float | None = None,
    tz_offset_s: int | None = None,
    valid_from: str | None = None,
    valid_to: str | None = None,
    note: str = "",
) -> TimeSetSummary:
    """Record one offset observation and renormalise the evidence item (FR-51, FR-50)."""
    if not store.conn.execute("SELECT 1 FROM evidence WHERE id=?", (evidence_id,)).fetchone():
        raise ServiceError(f"no evidence item {evidence_id}")
    try:
        observation = observe(
            method,
            _parse_local(device_time, "device time"),
            _parse_true(true_time),
            uncertainty_s,
            note,
            _parse_local(valid_from, "valid_from") if valid_from else None,
            _parse_local(valid_to, "valid_to") if valid_to else None,
        )
    except TimeModelError as exc:
        raise ServiceError(str(exc)) from exc

    observation_id = store.next_id("TOB", "time_observation")
    params: dict[str, Any] = {
        "observation_id": observation_id, "method": method,
        "device_local": observation.device_local.isoformat(),
        "true_utc": observation.true_utc.isoformat(),
        "uncertainty_s": observation.uncertainty_s, "tz_offset_s": tz_offset_s,
        "total_offset_s": observation.total_offset_s,
    }
    with store.audit.operation("time.set", target=evidence_id, params=params) as op:
        store.conn.execute("BEGIN IMMEDIATE")
        try:
            store.conn.execute(
                "INSERT INTO time_observation (id, evidence_id, method, device_local,"
                " true_utc, uncertainty_s, tz_offset_s, valid_from, valid_to, note)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    observation_id, evidence_id, method,
                    observation.device_local.isoformat(), observation.true_utc.isoformat(),
                    observation.uncertainty_s, tz_offset_s,
                    observation.valid_from.isoformat() if observation.valid_from else None,
                    observation.valid_to.isoformat() if observation.valid_to else None,
                    note,
                ),
            )
            model = _model_from_rows(store, evidence_id)
            normalised, refused = _renormalise(store, evidence_id, model)
            store.conn.commit()
        except TimeModelError as exc:
            store.conn.rollback()
            raise ServiceError(str(exc)) from exc
        except BaseException:
            store.conn.rollback()
            raise
        op.result_params = {"recordings_normalised": normalised,
                            "recordings_refused": refused}
    store.write_manifest()
    offset_note = model.segments[-1].note if model.segments else REFUSAL_BANNER
    return TimeSetSummary(evidence_id, observation_id, offset_note, normalised, refused)


def observation_rows(store: CaseStore, evidence_id: str | None = None) -> list[dict[str, Any]]:
    where, args = ("WHERE evidence_id=?", [evidence_id]) if evidence_id else ("", [])
    cursor = store.conn.execute(
        f"SELECT * FROM time_observation {where} ORDER BY id", args  # noqa: S608
    )
    names = [c[0] for c in cursor.description]
    return [dict(zip(names, row, strict=True)) for row in cursor]


def clock_model(store: CaseStore, evidence_id: str) -> ClockModel:
    rows = observation_rows(store, evidence_id)
    observations = [
        OffsetObservation(
            method=row["method"],
            device_local=datetime.fromisoformat(row["device_local"]),
            true_utc=datetime.fromisoformat(row["true_utc"]),
            uncertainty_s=row["uncertainty_s"],
            note=row["note"] or "",
            valid_from=(datetime.fromisoformat(row["valid_from"])
                        if row["valid_from"] else None),
            valid_to=datetime.fromisoformat(row["valid_to"]) if row["valid_to"] else None,
        )
        for row in rows
    ]
    tz_values = {row["tz_offset_s"] for row in rows if row["tz_offset_s"] is not None}
    if len(tz_values) > 1:
        raise ServiceError(f"{evidence_id} has conflicting stated timezones: {sorted(tz_values)}")
    try:
        return ClockModel.from_observations(observations,
                                            tz_values.pop() if tz_values else None)
    except TimeModelError as exc:
        raise ServiceError(str(exc)) from exc


_model_from_rows = clock_model


def _renormalise(store: CaseStore, evidence_id: str, model: ClockModel) -> tuple[int, int]:
    normalised = refused = 0
    rows = store.conn.execute(
        "SELECT id, t_local_start, t_local_end FROM recording WHERE evidence_id=? ORDER BY id",
        (evidence_id,),
    ).fetchall()
    for rec_id, local_start, local_end in rows:
        start = model.normalise(datetime.fromisoformat(local_start)) if local_start else (
            not_established("no device time on this item"))
        end = model.normalise(datetime.fromisoformat(local_end)) if local_end else (
            not_established("no device time on this item"))
        established = start.utc is not None and end.utc is not None
        method = start.method if established else "none"
        store.conn.execute(
            "UPDATE recording SET t_ref_start=?, t_ref_end=?, t_uncertainty_s=?, t_method=?"
            " WHERE id=?",
            (
                start.utc.isoformat() if start.utc else None,
                end.utc.isoformat() if end.utc else None,
                max(start.uncertainty_s, end.uncertainty_s) if established else None,
                method, rec_id,
            ),
        )
        normalised += established
        refused += not established
    store.conn.execute(
        "UPDATE device_event SET t_ref=NULL WHERE evidence_id=? AND t_device IS NULL",
        (evidence_id,),
    )
    for rowid, t_device in store.conn.execute(
        "SELECT rowid, t_device FROM device_event WHERE evidence_id=? AND t_device IS NOT NULL"
        " ORDER BY rowid", (evidence_id,),
    ).fetchall():
        ref = model.normalise(datetime.fromisoformat(t_device))
        store.conn.execute("UPDATE device_event SET t_ref=? WHERE rowid=?",
                           (ref.utc.isoformat() if ref.utc else None, rowid))
    return normalised, refused


# -- read models for CLI, UI and report -------------------------------------------------------

def _segments_for(
    store: CaseStore, evidence_ids: list[str], basis: str
) -> list[gaps_mod.CoverageSegment]:
    start_col, end_col = (("t_ref_start", "t_ref_end") if basis == "reference"
                          else ("t_local_start", "t_local_end"))
    segments = []
    for evidence_id in evidence_ids:
        for rec_id, channel, start, end, uncertainty in store.conn.execute(
            f"SELECT id, channel, {start_col}, {end_col}, t_uncertainty_s FROM recording"  # noqa: S608
            " WHERE evidence_id=? ORDER BY id", (evidence_id,),
        ).fetchall():
            if start and end:
                segments.append(gaps_mod.CoverageSegment(
                    channel, datetime.fromisoformat(start), datetime.fromisoformat(end),
                    uncertainty or 0.0, rec_id,
                ))
    return segments


def _evidence_ids(store: CaseStore, evidence_id: str | None) -> list[str]:
    if evidence_id:
        return [evidence_id]
    return [row[0] for row in
            store.conn.execute("SELECT id FROM evidence ORDER BY id").fetchall()]


def timeline_data(store: CaseStore, evidence_id: str | None = None) -> dict[str, Any]:
    """Lanes + unplaced items for the timeline view and report §9 (FR-57 data, FR-60)."""
    rows: list[tuple[str, str, int | None, ReferenceTime, ReferenceTime]] = []
    for ev_id in _evidence_ids(store, evidence_id):
        model = clock_model(store, ev_id)
        for rec_id, channel, local_start, local_end in store.conn.execute(
            "SELECT id, channel, t_local_start, t_local_end FROM recording"
            " WHERE evidence_id=? ORDER BY id", (ev_id,),
        ).fetchall():
            start = (model.normalise(datetime.fromisoformat(local_start)) if local_start
                     else not_established("no device time on this item"))
            end = (model.normalise(datetime.fromisoformat(local_end)) if local_end
                   else not_established("no device time on this item"))
            rows.append((ev_id, rec_id, channel, start, end))
    correlation = correlate_mod.correlate(rows)
    return {
        "basis": "reference",
        "lanes": [
            {
                "label": lane.label,
                "evidence_id": lane.evidence_id,
                "channel": lane.channel,
                "segments": [
                    {
                        "recording_id": s.recording_id,
                        "start": s.start.isoformat(),
                        "end": s.end.isoformat(),
                        "uncertainty_s": s.uncertainty_s,
                    }
                    for s in lane.segments
                ],
            }
            for lane in correlation.lanes
        ],
        "unplaced": [
            {"evidence_id": u.evidence_id, "recording_id": u.recording_id,
             "channel": u.channel, "reason": u.reason}
            for u in correlation.unplaced
        ],
    }


def gap_report(
    store: CaseStore, evidence_id: str | None = None, min_gap_s: float = 1.0
) -> dict[str, Any]:
    """Per-channel and synchronised gaps (FR-58), on the best defensible basis per item.

    Reference basis needs offset evidence; without it the per-item analysis runs on
    device-local time and says so — relative gaps within one device clock are still
    honest findings under the FR-53 banner.
    """
    reports = []
    for ev_id in _evidence_ids(store, evidence_id):
        has_ref = bool(store.conn.execute(
            "SELECT 1 FROM recording WHERE evidence_id=? AND t_ref_start IS NOT NULL LIMIT 1",
            (ev_id,),
        ).fetchone())
        basis = "reference" if has_ref else "device-local"
        segments = _segments_for(store, [ev_id], basis)
        entry: dict[str, Any] = {
            "evidence_id": ev_id,
            "basis": basis,
            "channel_gaps": [_gap_json(g) for g in
                             gaps_mod.channel_gaps(segments, min_gap_s)],
            "synchronised_gaps": [_gap_json(g) for g in
                                  gaps_mod.synchronised_gaps(segments, min_gap_s)],
        }
        if basis == "device-local":
            entry["banner"] = REFUSAL_BANNER
        reports.append(entry)
    return {"min_gap_s": min_gap_s, "evidence": reports}


def _gap_json(gap: gaps_mod.Gap) -> dict[str, Any]:
    return {
        "channel": gap.channel,
        "start": gap.start.isoformat(),
        "end": gap.end.isoformat(),
        "duration_s": gap.duration_s,
        "uncertainty_s": gap.uncertainty_s,
        "synchronised": gap.synchronised,
        "after_recording": gap.after_recording,
        "before_recording": gap.before_recording,
    }


def show(store: CaseStore, evidence_id: str) -> dict[str, Any]:
    """Observations and the resulting clock model, for `spectra time show` and report §8."""
    model = clock_model(store, evidence_id)
    return {
        "evidence_id": evidence_id,
        "tz_offset_s": model.tz_offset_s,
        "observations": [
            {k: row[k] for k in ("id", "method", "device_local", "true_utc",
                                 "uncertainty_s", "valid_from", "valid_to", "note")}
            for row in observation_rows(store, evidence_id)
        ],
        "segments": [
            {
                "valid_from": s.valid_from.isoformat() if s.valid_from else None,
                "valid_to": s.valid_to.isoformat() if s.valid_to else None,
                "total_offset_s": s.total_offset_s,
                "uncertainty_s": s.uncertainty_s,
                "method": s.method,
                "note": s.note,
            }
            for s in model.segments
        ],
    }


def to_json(data: dict[str, Any]) -> str:
    """Deterministic serialisation for AC-11 comparisons."""
    return json.dumps(data, indent=2, sort_keys=True)


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def time_anomalies(store: CaseStore, evidence_id: str) -> list[anom.Anomaly]:
    """Where the recorder's own recorded times contradict each other (FR-55).

    Runs on device-local time, which is the axis the contradictions live on: a recorder
    disagreeing with itself is not something a clock offset can explain away.
    """
    cur = store.conn.execute(
        "SELECT id, channel, t_local_start, t_local_end, extents_json FROM recording"
        " WHERE evidence_id = ? ORDER BY id",
        (evidence_id,),
    )
    segments = []
    for position, row in enumerate(cur.fetchall()):
        rec_id, channel, start, end, extents = row
        segments.append(
            anom.Segment(
                recording_id=rec_id,
                channel=channel,
                start=_parse_dt(start),
                end=_parse_dt(end),
                index_position=position,
                extent_key=extents or "",
            )
        )

    layout = store.conn.execute(
        "SELECT format_t_local FROM disk_layout WHERE evidence_id = ?", (evidence_id,)
    ).fetchone()
    evidence = store.conn.execute(
        "SELECT acquired_utc FROM evidence WHERE id = ?", (evidence_id,)
    ).fetchone()
    return anom.detect(
        segments,
        format_time=_parse_dt(layout[0]) if layout else None,
        acquired=_parse_dt(evidence[0]) if evidence else None,
    )
