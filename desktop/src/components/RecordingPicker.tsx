import { useEffect } from "react";

import { channelLabel, deviceTime } from "../format";
import type { Recording } from "../types";

export function RecordingPicker(props: { rows: Recording[]; value: string | null; onChange(id: string): void }) {
  const { rows, value, onChange } = props;
  useEffect(() => {
    if (!value && rows.length) onChange(rows[0].id);
  }, [value, rows, onChange]);
  return (
    <select value={value ?? ""} onChange={(e) => onChange(e.target.value)} className="picker">
      {rows.map((r) => (
        <option key={r.id} value={r.id}>
          {r.id} · {r.evidence_id} · {channelLabel(r.channel)} · {r.recovery_tier} · {deviceTime(r.t_local_start)}
        </option>
      ))}
    </select>
  );
}
