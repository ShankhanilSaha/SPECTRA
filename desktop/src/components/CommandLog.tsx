import { useState } from "react";

import { bridge, useCommandLog, type LogEntry } from "../api";

/** Every CLI command the window has run, exactly as a terminal would run it. */
export function CommandLog(props: { compact?: boolean }) {
  const entries = useCommandLog();
  const shown = props.compact ? entries.slice(0, 6) : entries;
  if (!shown.length) return <div className="empty">No commands run yet.</div>;
  return (
    <ol className="cmdlog">
      {shown.map((entry) => (
        <Row key={entry.jobId} entry={entry} compact={props.compact} />
      ))}
    </ol>
  );
}

function Row(props: { entry: LogEntry; compact?: boolean }) {
  const { entry } = props;
  const [open, setOpen] = useState(false);
  const r = entry.result;
  const state = r === null ? "running" : r.cancelled ? "cancelled" : r.ok ? "ok" : "failed";
  return (
    <li className={`cmd cmd-${state}`}>
      <div className="cmd-line">
        <span className="cmd-state">{state === "running" ? <span className="spinner" /> : state}</span>
        <code className="cmd-text" onClick={() => !props.compact && setOpen(!open)}>
          {entry.display}
        </code>
        {r && <span className="cmd-time">{(r.durationMs / 1000).toFixed(1)} s</span>}
        {state === "running" && (
          <button type="button" className="link" onClick={() => void bridge.cancel(entry.jobId)}>
            cancel
          </button>
        )}
      </div>
      {open && r && (
        <div className="cmd-output">
          {r.stderr.trim() && <pre className="stderr">{r.stderr.trim()}</pre>}
          <pre>{r.stdout.trim() || "(no output)"}</pre>
        </div>
      )}
    </li>
  );
}
