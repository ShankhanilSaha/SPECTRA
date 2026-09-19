import { useState } from "react";

import type { OffsetMethodLetter } from "../../shared/api";
import { run, useCli } from "../api";
import { Button, CliHint, Empty, Field, Loading, Notice, Page, Panel, useAction } from "../components/ui";
import { deviceTime, offset, utcTime } from "../format";
import type { TimeShow } from "../types";
import { useCase } from "../state";

const METHODS: { id: OffsetMethodLetter | "none"; title: string; text: string }[] = [
  { id: "B", title: "B — Reference-clock capture", text: "A GPS/NTP clock filmed at seizure (Form F-1). Enter the device time shown on screen and the true time on the reference clock. ±1 s." },
  { id: "A", title: "A — NTP synchronised", text: "Needs proof from the firmware config and the device log. ±1 s." },
  { id: "C", title: "C — External timestamped event", text: "A card swipe, POS transaction or call visible in frame with an independent record. ±2–60 s." },
  { id: "D", title: "D — Live RTC read", text: "The device's displayed time noted against true time before power-down. ±1 s at that instant." },
  { id: "none", title: "None available", text: "Record nothing. Times stay device-local and no absolute time is asserted anywhere (FR-53)." },
];

const ZONES = ["+05:30", "Z", "+00:00", "+08:00", "+05:45", "+04:00", "+03:00", "+01:00", "-05:00"];

export function TimeModel() {
  const c = useCase();
  const shown = useCli<TimeShow>(c.evidenceId ? { kind: "timeShow", case: c.dir, evidence: c.evidenceId } : null, `${c.dir}|${c.evidenceId}|${c.version}`);
  return (
    <Page step={6} title="Time model" lead="Set this before any analysis. A recorder's clock is usually wrong; SPECTRA measures by how much, or refuses to assert absolute time.">
      {!c.evidenceId ? (
        <Empty>Add evidence first.</Empty>
      ) : (
        <div className="grid-2">
          <Panel title={`Established offset · ${c.evidenceId}`}>
            {shown.loading && !shown.data ? (
              <Loading />
            ) : shown.error ? (
              <Notice tone="error">{shown.error}</Notice>
            ) : shown.data && shown.data.segments.length ? (
              <>
                {shown.data.segments.map((s, i) => (
                  <div key={i} className="offset-card">
                    <OffsetFigures total={s.total_offset_s} tz={shown.data!.tz_offset_s} />
                    <div className="muted">± {s.uncertainty_s} s · method {s.method}</div>
                    <div className="small">{s.valid_from || s.valid_to ? `device-local ${s.valid_from ? deviceTime(s.valid_from) : "…"} → ${s.valid_to ? deviceTime(s.valid_to) : "…"}` : "applies to the whole evidence item"}</div>
                    <p className="derivation">{s.note}</p>
                  </div>
                ))}
                <h3>Observations</h3>
                <table className="table">
                  <thead><tr><th>ID</th><th>Method</th><th>Device showed</th><th>True time</th><th>±</th></tr></thead>
                  <tbody>
                    {shown.data.observations.map((o) => (
                      <tr key={o.id}>
                        <td>{o.id}</td><td>{o.method}</td><td>{deviceTime(o.device_local)}</td><td>{utcTime(o.true_utc)}</td><td>{o.uncertainty_s} s</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </>
            ) : (
              <Notice tone="warn" title="Absolute time not established">
                Every time for this evidence is the recorder's own wall clock and may be wrong by an unknown amount. The timeline shows it on the device's own axis, and the report says so on every page.
              </Notice>
            )}
            <CliHint>{`spectra time show --evidence ${c.evidenceId}`}</CliHint>
          </Panel>
          <OffsetForm />
        </div>
      )}
    </Page>
  );
}

function OffsetForm() {
  const c = useCase();
  const [method, setMethod] = useState<OffsetMethodLetter | "none">("B");
  const [deviceAt, setDeviceAt] = useState("");
  const [trueAt, setTrueAt] = useState("");
  const [zone, setZone] = useState("+05:30");
  const [uncertainty, setUncertainty] = useState("");
  const [tz, setTz] = useState("");
  const [note, setNote] = useState("");
  const withSeconds = (v: string) => (v.length === 16 ? `${v}:00` : v);
  const action = useAction(
    () =>
      run({
        kind: "timeSet", case: c.dir, evidence: c.evidenceId!, method: method as OffsetMethodLetter,
        deviceTime: withSeconds(deviceAt), trueTime: `${withSeconds(trueAt)}${zone}`,
        uncertainty: uncertainty ? Number(uncertainty) : null, tzOffsetMinutes: tz ? Number(tz) : null,
        validFrom: null, validTo: null, note,
      }),
    () => {
      setNote("");
      c.changed();
    },
  );
  return (
    <Panel title="Record an offset observation">
      <div className="choice-list">
        {METHODS.map((m) => (
          <button key={m.id} type="button" className={`choice ${method === m.id ? "active" : ""}`} onClick={() => setMethod(m.id)}>
            <strong>{m.title}</strong>
            <span>{m.text}</span>
          </button>
        ))}
      </div>
      {method === "none" ? (
        <Notice>Nothing is recorded. “Offset undetermined” is a defensible finding; an unsupported timestamp is not.</Notice>
      ) : (
        <>
          <div className="form-grid">
            <Field label="Device showed" hint="The recorder's own clock, as displayed (no timezone)">
              <input type="datetime-local" step={1} value={deviceAt} onChange={(e) => setDeviceAt(e.target.value)} />
            </Field>
            <Field label="True time" hint="From the reference clock">
              <div className="path-row">
                <input type="datetime-local" step={1} value={trueAt} onChange={(e) => setTrueAt(e.target.value)} />
                <select value={zone} onChange={(e) => setZone(e.target.value)} aria-label="Timezone of the true time">
                  {ZONES.map((z) => <option key={z} value={z}>{z === "Z" ? "UTC" : `UTC${z}`}</option>)}
                </select>
              </div>
            </Field>
            <Field label="Uncertainty ± s" hint="Blank: the method's floor"><input type="number" min={0} value={uncertainty} onChange={(e) => setUncertainty(e.target.value)} /></Field>
            <Field label="Device timezone (minutes)" hint="e.g. 330 for UTC+05:30, when known"><input type="number" value={tz} onChange={(e) => setTz(e.target.value)} /></Field>
            <Field label="Derivation note" hint="Printed verbatim in the report — write it as you would want it read aloud in court." wide>
              <textarea rows={3} value={note} onChange={(e) => setNote(e.target.value)} />
            </Field>
          </div>
          <Notice tone="warn">Do not enter a guessed offset to make the interface look complete.</Notice>
          {action.error && <Notice tone="error">{action.error}</Notice>}
          <div className="form-actions">
            <Button kind="primary" onClick={action.run} busy={action.busy} disabled={!deviceAt || !trueAt || !note.trim()}>
              Record observation and renormalise
            </Button>
          </div>
        </>
      )}
    </Panel>
  );
}

/** The documented split (timeline/timemodel.py): total = timezone + clock error. The clock
 * error is shown on its own only when the examiner has stated the timezone. */
function OffsetFigures(props: { total: number; tz: number | null }) {
  if (props.tz === null) {
    return (
      <>
        <div className="offset-value">{offset(props.total)}</div>
        <div className="small">device wall clock → UTC · timezone not stated, so it is not separated from the clock error</div>
      </>
    );
  }
  return (
    <div className="offset-split">
      <div>
        <div className="offset-value">{offset(props.total - props.tz)}</div>
        <div className="small">clock error</div>
      </div>
      <div>
        <div className="offset-sub">UTC{offset(props.tz).slice(0, 6)}</div>
        <div className="small">device timezone (stated)</div>
      </div>
      <div>
        <div className="offset-sub">{offset(props.total)}</div>
        <div className="small">device wall clock → UTC</div>
      </div>
    </div>
  );
}
