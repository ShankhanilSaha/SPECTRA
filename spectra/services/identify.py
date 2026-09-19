"""IdentifyService — run identification, persist it, and record operator selections."""

from __future__ import annotations

import json
from typing import Any

from spectra.core.casestore import CaseStore
from spectra.core.models import ProbeResult
from spectra.identify.engine import IdentificationResult, identify, plan_to_json, probe_to_json
from spectra.plugins.base import VendorPlugin
from spectra.plugins.registry import REGISTRY
from spectra.services import ServiceError
from spectra.services.evidence import evidence_row, open_evidence


def run_identify(
    store: CaseStore, evidence_id: str, plugins: tuple[type[VendorPlugin], ...] = REGISTRY
) -> IdentificationResult:
    evidence_row(store, evidence_id)
    with store.audit.operation("identify", target=evidence_id,
                               params={"plugins": _plugin_versions(plugins)}) as op:
        with open_evidence(store, evidence_id) as src:
            result = identify(src, plugins)
        selection = "single_candidate" if result.status == "identified" else None
        _persist(store, evidence_id, result, result.selected, selection)
        op.result_params = {
            "status": result.status,
            "support": result.support,
            "selected_family": result.selected.family if result.selected else None,
            "candidates": [(c.family, c.layout_version, c.confidence, c.parse_supported)
                           for c in result.candidates],
            "probe_errors": len(result.errors),
        }
    if result.status == "ambiguous":
        store.audit.append(
            "identify.ambiguous", "ok", target=evidence_id,
            params={"candidates": [probe_to_json(c) for c in result.candidates]},
        )
    store.write_manifest()
    return result


def select_family(store: CaseStore, evidence_id: str, family: str, reason: str) -> ProbeResult:
    """Operator selection among the reported candidates. Audited with the stated reason."""
    if not reason.strip():
        raise ServiceError("a reason is required for a family selection (it is audited)")
    row = identification_row(store, evidence_id)
    candidates = json.loads(row["candidates_json"])
    chosen = next((c for c in candidates if c["family"] == family), None)
    if chosen is None:
        raise ServiceError(
            f"{family!r} was not a candidate for {evidence_id}; candidates were: "
            + (", ".join(c["family"] for c in candidates) or "none")
            + ". The family comes from the disk, never from the chassis label."
        )
    with store.audit.operation("identify.select", target=evidence_id,
                               params={"family": family, "reason": reason,
                                       "previous_status": row["status"]}):
        with store.transaction() as conn:
            conn.execute(
                "UPDATE identification SET family=?, layout_version=?, confidence=?,"
                " parse_supported=?, matched_signature_hex=?, matched_offsets=?, selection=?"
                " WHERE evidence_id=?",
                (chosen["family"], chosen["layout_version"], chosen["confidence"],
                 int(chosen["parse_supported"]), _first_hex(chosen["matches"]),
                 json.dumps([m["offset"] for m in chosen["matches"]]), "operator", evidence_id),
            )
    store.write_manifest()
    return ProbeResult(
        family=chosen["family"], layout_version=chosen["layout_version"],
        confidence=chosen["confidence"], parse_supported=chosen["parse_supported"],
        matches=(), plugin_version=chosen["plugin_version"], note=chosen["note"],
    )


def identification_row(store: CaseStore, evidence_id: str) -> dict[str, Any]:
    cursor = store.conn.execute("SELECT * FROM identification WHERE evidence_id=?", (evidence_id,))
    row = cursor.fetchone()
    if row is None:
        raise ServiceError(f"{evidence_id} has not been identified yet — run 'spectra identify'")
    return dict(zip([c[0] for c in cursor.description], row, strict=True))


def identification_view(store: CaseStore, evidence_id: str) -> dict[str, Any]:
    """The stored identification, decoded for display. Read-only: nothing is re-probed and
    nothing is audited, so a UI can show it as often as it likes."""
    row = identification_row(store, evidence_id)
    if row["family"] is None:
        support = "pending_selection" if row["status"] == "ambiguous" else "none"
    else:
        support = "parse" if row["parse_supported"] else "carve_only"
    return {
        "evidence_id": evidence_id,
        "status": row["status"],
        "support": support,
        "selected_family": row["family"],
        "selected_layout_version": row["layout_version"],
        "selection": row["selection"],
        "candidates": json.loads(row["candidates_json"] or "[]"),
        "observations": json.loads(row["observations_json"] or "[]"),
        "errors": json.loads(row["errors_json"] or "[]"),
    }


def _persist(
    store: CaseStore,
    evidence_id: str,
    result: IdentificationResult,
    selected: ProbeResult | None,
    selection: str | None,
) -> None:
    data = result.to_json()
    with store.transaction() as conn:
        conn.execute("DELETE FROM identification WHERE evidence_id=?", (evidence_id,))
        conn.execute(
            "INSERT INTO identification (evidence_id, family, layout_version, confidence,"
            " parse_supported, matched_signature_hex, matched_offsets, candidates_json, status,"
            " selection, probe_plan_json, observations_json, errors_json)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                evidence_id,
                selected.family if selected else None,
                selected.layout_version if selected else None,
                selected.confidence if selected else None,
                int(selected.parse_supported) if selected else None,
                selected.matches[0].data.hex() if selected and selected.matches else None,
                json.dumps([m.offset for m in selected.matches]) if selected else None,
                json.dumps(data["candidates"]),
                result.status,
                selection,
                json.dumps(plan_to_json(result.plan)),
                json.dumps(data["observations"]),
                json.dumps(data["errors"]),
            ),
        )


def _first_hex(matches: list[dict[str, Any]]) -> str | None:
    return matches[0]["hex"] if matches else None


def _plugin_versions(plugins: tuple[type[VendorPlugin], ...]) -> dict[str, str]:
    return {p.family: p.plugin_version for p in plugins}
