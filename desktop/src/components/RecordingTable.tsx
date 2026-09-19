import { Fragment, useState } from "react";

import { bytes, channelLabel, deviceTime, notesOf, utcTime } from "../format";
import type { Recording } from "../types";
import { TierBadge } from "./ui";

export function RecordingTable(props: { rows: Recording[]; onPick?(id: string): void; picked?: string | null }) {
  const [open, setOpen] = useState<string | null>(null);
  return (
    <table className="table recordings">
      <thead>
        <tr>
          <th>ID</th><th>Ch</th><th>Tier</th><th>Conf.</th><th>Codec</th>
          <th>Start (device-local)</th><th>End (device-local)</th><th>Start (UTC reference)</th>
          <th className="num">Frames</th><th className="num">Size</th><th />
        </tr>
      </thead>
      <tbody>
        {props.rows.map((r) => {
          const notes = notesOf(r.notes_json);
          return (
            <Fragment key={r.id}>
              <tr
                className={`${props.picked === r.id ? "row-selected" : ""} ${props.onPick ? "clickable" : ""}`}
                onClick={() => props.onPick?.(r.id)}
              >
                <td><strong>{r.id}</strong><div className="muted small">{r.evidence_id}</div></td>
                <td>{channelLabel(r.channel)}</td>
                <td><TierBadge tier={r.recovery_tier} /></td>
                <td className="num">{r.confidence.toFixed(2)}</td>
                <td>{r.codec ?? "—"}{r.width ? <div className="muted small">{r.width}×{r.height}</div> : null}</td>
                <td className={r.t_local_start ? "" : "muted"}>{deviceTime(r.t_local_start)}</td>
                <td className={r.t_local_end ? "" : "muted"}>{deviceTime(r.t_local_end)}</td>
                <td className={r.t_ref_start ? "" : "muted"}>
                  {r.t_ref_start ? <>{utcTime(r.t_ref_start)}<div className="muted small">±{r.t_uncertainty_s} s · {r.t_method}</div></> : "not established"}
                </td>
                <td className="num">{r.frame_count ?? "—"}</td>
                <td className="num">{bytes(r.size_bytes)}</td>
                <td>
                  {notes.length > 0 && (
                    <button type="button" className="link" onClick={(e) => { e.stopPropagation(); setOpen(open === r.id ? null : r.id); }}>
                      {open === r.id ? "hide" : `${notes.length} note${notes.length > 1 ? "s" : ""}`}
                    </button>
                  )}
                </td>
              </tr>
              {open === r.id && (
                <tr className="notes-row">
                  <td colSpan={11}>
                    <ul>{notes.map((n) => <li key={n}>{n}</li>)}</ul>
                    {r.source_note && <div className="muted small">{r.source_note}</div>}
                  </td>
                </tr>
              )}
            </Fragment>
          );
        })}
      </tbody>
    </table>
  );
}
