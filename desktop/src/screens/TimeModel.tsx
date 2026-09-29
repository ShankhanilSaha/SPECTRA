import type { OffsetMethodLetter } from "../../shared/api";
import { run, useCli } from "../api";
import { StepPage } from "../components/StepNav";
import { Button, CliHint, Field, Loading, Notice, Panel, useAction } from "../components/ui";
import { deviceTime, offset, utcTime } from "../format";
import type { TimeShow } from "../types";
import { go, useCase, useSessionState } from "../state";
import { markKey } from "../steps";

const METHODS: { id: OffsetMethodLetter | "none"; title: string; floor: string; text: string }[] = [
  { id: "B", title: "B — Reference-clock capture", floor: "±1 s", text: "A GPS/NTP clock filmed at seizure (Form F-1). Enter the device time shown on screen and the true time on the reference clock." },
  { id: "A", title: "A — NTP synchronised", floor: "±1 s", text: "Needs proof from the firmware config and the device log." },
  { id: "C", title: "C — External timestamped event", floor: "±2–60 s", text: "A card swipe, POS transaction or call visible in frame with an independent record." },
  { id: "D", title: "D — Live RTC read", floor: "±1 s at that instant", text: "The device's displayed time noted against true time before power-down." },
  { id: "none", title: "None available", floor: "device clock only", text: "Record nothing. Times stay device-local and no absolute time is asserted anywhere (FR-53)." },
];

const ZONES = ["+05:30", "Z", "+00:00", "+08:00", "+05:45", "+04:00", "+03:00", "+01:00", "-05:00"];

export function TimeModel() {
  const c = useCase();
  const ev = c.evidenceId;
  const shown = useCli<TimeShow>(ev ? { kind: "timeShow", case: c.dir, evidence: ev } : null, c.version);
  return (
    <StepPage
      step="time"
      lead="Set this before any analysis. A recorder's clock is usually wrong; SPECTRA measures by how much, or refuses to assert absolute time. Never enter a guessed offset to make the screen look complete."
    >
      <div className="grid-2">
        <Panel title="Established offset">
          {shown.loading ? (
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
          {ev && <CliHint>{`spectra time show --evidence ${ev}`}</CliHint>}
        </Panel>
        {ev && <OffsetForm evidence={ev} />}
      </div>
    </StepPage>
  );
}

function OffsetForm(props: { evidence: string }) {
  const c = useCase();
  const blank = { method: "B" as OffsetMethodLetter | "none", deviceAt: "", trueAt: "", zone: "+05:30", uncertainty: "", tz: "", note: "" };
  const [form, setForm] = useSessionState(`time:form:${props.evidence}`, blank);
  const set = (key: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>) =>
    setForm({ ...form, [key]: e.target.value });
  const withSeconds = (v: string) => (v.length === 16 ? `${v}:00` : v);
  const action = useAction(
    () =>
      run({
        kind: "timeSet", case: c.dir, evidence: props.evidence, method: form.method as OffsetMethodLetter,
        deviceTime: withSeconds(form.deviceAt), trueTime: `${withSeconds(form.trueAt)}${form.zone}`,
        uncertainty: form.uncertainty ? Number(form.uncertainty) : null, tzOffsetMinutes: form.tz ? Number(form.tz) : null,
        validFrom: null, validTo: null, note: form.note,
      }),
    () => {
      setForm({ ...form, note: "" });
      c.mark("skipped", markKey("time", props.evidence), false);
      c.changed();
    },
  );
  return (
    <Panel title="Record an offset observation">
      <div className="choice-list compact" role="radiogroup" aria-label="Offset method">
        {METHODS.map((m) => (
          <button
            key={m.id}
            type="button"
            role="radio"
            aria-checked={form.method === m.id}
            className={`choice ${form.method === m.id ? "active" : ""}`}
            onClick={() => setForm({ ...form, method: m.id })}
          >
            <span className="choice-title"><strong>{m.title}</strong><span>{m.floor}</span></span>
            {form.method === m.id && <span>{m.text}</span>}
          </button>
        ))}
      </div>
      {form.method === "none" ? (
        <>
          <Notice>“Offset undetermined” is a defensible finding; an unsupported timestamp is not. Nothing is recorded, and you can come back if evidence of the clock turns up.</Notice>
          <div className="form-actions">
            <Button
              kind="primary"
              onClick={() => {
                c.mark("skipped", markKey("time", props.evidence));
                go("recover");
              }}
            >
              Continue without an offset →
            </Button>
          </div>
        </>
      ) : (
        <>
          <div className="form-grid">
            <Field label="Device showed" hint="The recorder's own clock, as displayed (no timezone)" wide>
              <input type="datetime-local" step={1} value={form.deviceAt} onChange={set("deviceAt")} />
            </Field>
            <Field label="True time" hint="From the reference clock, with its timezone" wide>
              <div className="path-row">
                <input type="datetime-local" step={1} value={form.trueAt} onChange={set("trueAt")} />
                <select value={form.zone} onChange={set("zone")} aria-label="Timezone of the true time">
                  {ZONES.map((z) => <option key={z} value={z}>{z === "Z" ? "UTC" : `UTC${z}`}</option>)}
                </select>
              </div>
            </Field>
            <Field label="Uncertainty ± s" hint="Blank: the method's floor"><input type="number" min={0} value={form.uncertainty} onChange={set("uncertainty")} /></Field>
            <Field label="Device timezone (minutes)" hint="e.g. 330 for UTC+05:30, when known"><input type="number" value={form.tz} onChange={set("tz")} /></Field>
            <Field label="Derivation note" hint="Printed verbatim in the report — write it as you would want it read aloud in court." wide>
              <textarea rows={3} value={form.note} onChange={set("note")} />
            </Field>
          </div>
          {action.error && <Notice tone="error">{action.error}</Notice>}
          <div className="form-actions">
            <Button kind="primary" onClick={action.run} busy={action.busy} disabled={!form.deviceAt || !form.trueAt || !form.note.trim()}>
              Record observation and renormalise
            </Button>
            {(!form.deviceAt || !form.trueAt || !form.note.trim()) && <span className="muted small">Needs both times and a derivation note.</span>}
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
