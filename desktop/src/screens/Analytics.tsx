import { useState } from "react";

import { run, useCli } from "../api";
import { RecordingPicker } from "../components/RecordingPicker";
import { Badge, Button, CliHint, Empty, Field, KeyValues, Loading, Notice, Page, Panel, useAction } from "../components/ui";
import type { MotionResult, Recording } from "../types";
import { routeParam, useCase } from "../state";

const DISCLAIMER =
  "Machine-generated detection. Requires human verification against the source frame. Not an identification.";

function clock(ms: number): string {
  const s = Math.floor(ms / 1000);
  return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}.${String(ms % 1000).padStart(3, "0")}`;
}

export function Analytics() {
  const c = useCase();
  const recordings = useCli<Recording[]>({ kind: "listRecordings", case: c.dir, evidence: null }, `${c.dir}|${c.version}`);
  const [rec, setRec] = useState<string | null>(routeParam("rec"));
  const [sensitivity, setSensitivity] = useState(25);
  const [minArea, setMinArea] = useState(400);
  const [result, setResult] = useState<MotionResult | null>(null);
  const action = useAction(
    () => run<MotionResult>({ kind: "analyzeMotion", case: c.dir, recording: rec!, sensitivity, minArea }),
    (r) => {
      setResult(r);
      c.changed();
    },
  );

  return (
    <Page step={9} title="Analytics" lead="Motion first: it removes most of the footage from review. Everything here is a lead for a human to check, never an identification.">
      <Notice tone="warn" title="Leads, not identifications">
        There is no watchlist and no identity database in this tool, deliberately. Every hit carries its model name, model hash and confidence, and must be verified against the source frame before it enters a report.
      </Notice>
      <div className="grid-2">
        <Panel title="Motion / activity (FR-90)">
          {recordings.loading && !recordings.data ? (
            <Loading />
          ) : !recordings.data?.length ? (
            <Empty>No recordings to analyse.</Empty>
          ) : (
            <>
              <div className="form-grid">
                <Field label="Recording" wide>
                  <RecordingPicker rows={recordings.data} value={rec} onChange={setRec} />
                </Field>
                <Field label="Sensitivity" hint="Smallest pixel change counted (1–255), inclusive">
                  <input type="number" min={1} max={255} value={sensitivity} onChange={(e) => setSensitivity(Number(e.target.value))} />
                </Field>
                <Field label="Minimum area" hint="Changed pixels needed for a frame to count as motion">
                  <input type="number" min={0} value={minArea} onChange={(e) => setMinArea(Number(e.target.value))} />
                </Field>
              </div>
              {action.error && <Notice tone="error">{action.error}</Notice>}
              <div className="form-actions">
                <Button kind="primary" onClick={action.run} busy={action.busy} disabled={!rec}>Run motion analysis</Button>
              </div>
              <CliHint>{`spectra analyze motion ${rec ?? "REC-…"} --sensitivity ${sensitivity} --min-area ${minArea}`}</CliHint>
            </>
          )}
        </Panel>
        <Panel title="Objects, faces, ANPR">
          <Empty>
            Object detection and face detect-and-cluster run in the network-isolated worker in the finals build (FR-91, FR-92), once a model with a redistributable licence is chosen (doc 2 Q4).
          </Empty>
        </Panel>
      </div>
      {result && (
        <Panel title={`Motion · ${result.recording_id}`}>
          <div className="stat-row">
            <div className="stat"><div className="stat-value">{result.total_frames}</div><div className="stat-label">frames sampled</div></div>
            <div className="stat"><div className="stat-value">{result.motion_frames}</div><div className="stat-label">with motion</div></div>
            <div className="stat"><div className="stat-value">{result.segments.length}</div><div className="stat-label">activity segments</div></div>
            <div className="stat"><div className="stat-value">{result.annotations_count}</div><div className="stat-label">annotations stored beside the evidence</div></div>
          </div>
          <p className="muted small">{result.decode_note}</p>
          {result.segments.length === 0 ? (
            <Empty>No activity segments at these settings.</Empty>
          ) : (
            <table className="table">
              <thead><tr><th>#</th><th>From</th><th>To</th><th>Frames</th><th>Peak score</th><th>Model</th></tr></thead>
              <tbody>
                {result.segments.map((s, i) => (
                  <tr key={i}>
                    <td>{i + 1}</td>
                    <td>{clock(s.start_pts_ms)}</td>
                    <td>{clock(s.end_pts_ms)}</td>
                    <td>{s.start_frame}–{s.end_frame} ({s.motion_frame_count})</td>
                    <td>{s.peak_score.toFixed(2)}</td>
                    <td><Badge>spectra-motion-differencing</Badge></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <KeyValues rows={[["Disclaimer (printed with every hit)", DISCLAIMER]]} />
        </Panel>
      )}
    </Page>
  );
}
