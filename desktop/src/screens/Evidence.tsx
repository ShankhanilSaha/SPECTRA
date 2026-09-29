import { useState } from "react";

import type { Provenance } from "../../shared/api";
import { bridge, run, useCli } from "../api";
import { bytes } from "../format";
import type { CaseChain, Ingest } from "../types";
import { StepPage } from "../components/StepNav";
import { Badge, Button, Empty, Field, Hash, Loading, Notice, Panel, useAction } from "../components/ui";
import { go, useCase, useSessionState, useTask, type CaseState } from "../state";

const PROVENANCE: [Provenance, string, string][] = [
  ["A", "Physical image, hardware write-blocked", "Strongest: bit-for-bit, verifiably unaltered."],
  ["B", "Physical image, other protection", "The protection method and its justification are recorded."],
  ["C", "Live logical acquisition", "The source could not be independently verified."],
  ["D", "Third-party export", "Original storage not examined; completeness unverifiable."],
];

/**
 * Import, then identify straight away (doc 7 §4 step 4: identification runs as soon as the
 * evidence is in) and move on to the result — unless the examiner has gone elsewhere
 * meanwhile, in which case they are left where they are.
 */
async function ingestThenIdentify(c: CaseState, ingest: () => Promise<Ingest>): Promise<Ingest> {
  const result = await ingest();
  const id = result.evidence_id;
  c.session.set(`ingest:${id}`, result);
  c.setEvidenceId(id);
  c.changed(); // start reading the new item's row while it is being identified
  await c.tasks.run(`identify:${id}`, () => run({ kind: "identify", case: c.dir, evidence: id }), c.changed);
  if (window.location.hash.startsWith("#/evidence")) go("identify");
  return result;
}

export function Evidence() {
  const c = useCase();
  const [mode, setMode] = useSessionState<"image" | "files">("evidence:mode", "image");
  const items = c.info?.evidence ?? [];
  return (
    <StepPage
      step="evidence"
      lead="SPECTRA reads evidence only through its read-only layer; nothing here writes to the image or the source files. Identification runs as soon as an item is in."
    >
      <div className="grid-2">
        <Panel title="Add evidence">
          <div className="segmented wide" role="tablist">
            <button type="button" role="tab" aria-selected={mode === "image"} className={mode === "image" ? "active" : ""} onClick={() => setMode("image")}>
              Disk image
            </button>
            <button type="button" role="tab" aria-selected={mode === "files"} className={mode === "files" ? "active" : ""} onClick={() => setMode("files")}>
              Export files
            </button>
          </div>
          {mode === "image" ? <ImportImage /> : <ImportFiles />}
          <p className="muted small not-yet">
            Not in this prototype: acquiring from an attached disk with write-blocker verification, live acquisition (FR-16), firmware dumps (FR-05).
          </p>
        </Panel>
        <Panel title={`In this case (${items.length})`}>
          {!c.info ? (
            <Loading />
          ) : !items.length ? (
            <Empty>No evidence yet. Import the image someone else made, or the owner's export files.</Empty>
          ) : (
            <ul className="evidence-list">
              {items.map((e) => (
                <li key={e.id}>
                  <button type="button" className={`evidence-item ${e.id === c.evidenceId ? "selected" : ""}`} onClick={() => c.setEvidenceId(e.id)}>
                    <span className="evidence-item-head">
                      <strong>{e.id}</strong> {e.label || <span className="muted">{e.kind}</span>}
                      <Badge tone="accent">class {e.provenance_class}</Badge>
                    </span>
                    <span className="muted small">{bytes(e.capacity_bytes)} · {e.source_path}</span>
                    <Hash value={e.sha256} short label="SHA-256" />
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Panel>
      </div>
      {c.evidenceId && <Custody />}
    </StepPage>
  );
}

function ImportImage() {
  const c = useCase();
  const [form, setForm] = useSessionState<{ file: string; provenance: Provenance | null; label: string; note: string }>(
    "evidence:image", { file: "", provenance: null, label: "", note: "" },
  );
  const task = useTask<Ingest>("import");
  const choose = async () => {
    const file = await bridge.pick({ title: "Evidence image", kind: "file", extensions: ["dd", "img", "raw", "001", "E01", "e01"] });
    if (file) setForm({ ...form, file });
  };
  const start = () =>
    task.run(async () => {
      const r = await ingestThenIdentify(c, () =>
        run<Ingest>({ kind: "importImage", case: c.dir, file: form.file, provenance: form.provenance!, label: form.label, note: form.note }),
      );
      setForm({ file: "", provenance: null, label: "", note: "" });
      return r;
    });
  return (
    <>
      <div className="form-grid">
        <Field label="Image file" hint="Raw, split raw (.001) or E01" wide>
          <div className="path-row">
            <input value={form.file} readOnly placeholder="Choose an image" onClick={choose} />
            <Button onClick={choose}>Choose…</Button>
          </div>
        </Field>
        <fieldset className="field-wide radio-group">
          <legend>How was it made? You state this; SPECTRA did not acquire the image and cannot know (FR-19).</legend>
          {PROVENANCE.map(([cls, title, text]) => (
            <label key={cls} className={`radio ${form.provenance === cls ? "active" : ""}`}>
              <input type="radio" name="prov" checked={form.provenance === cls} onChange={() => setForm({ ...form, provenance: cls })} />
              <span><strong>Class {cls}</strong> — {title}<br /><span className="muted small">{text}</span></span>
            </label>
          ))}
        </fieldset>
        <Field label="Label" hint="e.g. the badge on the chassis: 'CP Plus DVR, cash counter'"><input value={form.label} onChange={(e) => setForm({ ...form, label: e.target.value })} /></Field>
        <Field label="Note"><input value={form.note} onChange={(e) => setForm({ ...form, note: e.target.value })} /></Field>
      </div>
      {task.error && <Notice tone="error">{task.error}</Notice>}
      <div className="form-actions">
        <Button kind="primary" onClick={start} busy={task.busy} disabled={!form.file || !form.provenance}>
          {task.busy ? "Hashing and identifying…" : "Import, hash and identify"}
        </Button>
        {!form.provenance && form.file && <span className="muted small">Choose the provenance class first.</span>}
      </div>
    </>
  );
}

function ImportFiles() {
  const c = useCase();
  const [form, setForm] = useSessionState("evidence:files", { dir: "", label: "", note: "" });
  const task = useTask<Ingest>("import");
  const choose = async () => {
    const dir = await bridge.pick({ title: "Folder of export files", kind: "directory" });
    if (dir) setForm({ ...form, dir });
  };
  const start = () =>
    task.run(async () => {
      const r = await ingestThenIdentify(c, () =>
        run<Ingest>({ kind: "importFiles", case: c.dir, dir: form.dir, label: form.label, note: form.note }),
      );
      setForm({ dir: "", label: "", note: "" });
      return r;
    });
  return (
    <>
      <Notice>
        Export files are always provenance class D: the recorder's disk was not examined, so nothing can show what it did not export. Image the whole USB stick where you can, and recommend seizing the recorder (SOP-4).
      </Notice>
      <div className="form-grid">
        <Field label="Folder of export files" wide>
          <div className="path-row">
            <input value={form.dir} readOnly placeholder="Choose a folder" onClick={choose} />
            <Button onClick={choose}>Choose…</Button>
          </div>
        </Field>
        <Field label="Label"><input value={form.label} onChange={(e) => setForm({ ...form, label: e.target.value })} /></Field>
        <Field label="Note"><input value={form.note} onChange={(e) => setForm({ ...form, note: e.target.value })} /></Field>
      </div>
      {task.error && <Notice tone="error">{task.error}</Notice>}
      <div className="form-actions">
        <Button kind="primary" onClick={start} busy={task.busy} disabled={!form.dir}>
          {task.busy ? "Hashing and identifying…" : "Import, hash and identify"}
        </Button>
      </div>
    </>
  );
}

function Custody() {
  const c = useCase();
  const ev = c.evidenceId!;
  const chain = useCli<CaseChain>({ kind: "caseChain", case: c.dir, evidence: ev }, c.version);
  const [open, setOpen] = useSessionState(`custody:open:${ev}`, false);
  const entries = chain.data?.custody ?? [];
  return (
    <Panel
      title={`Chain of custody · ${ev}`}
      actions={!open && <Button onClick={() => setOpen(true)}>Record a transfer (Form F-2)</Button>}
    >
      {chain.error && <Notice tone="error">{chain.error}</Notice>}
      {chain.data?.chain_breaks.map((b) => <Notice key={b} tone="warn" title="Chain gap">{b}</Notice>)}
      {chain.loading ? (
        <Loading />
      ) : entries.length === 0 ? (
        <p className="muted">No transfers recorded for {ev}, so its physical chain is not established in the case.</p>
      ) : (
        <table className="table">
          <thead><tr><th>#</th><th>When (UTC)</th><th>From → To</th><th>Purpose</th><th>Seal</th></tr></thead>
          <tbody>
            {entries.map((e) => (
              <tr key={e.seq}>
                <td>{e.seq}</td>
                <td>{e.ts_utc}</td>
                <td>{e.from_holder} → {e.to_holder}</td>
                <td>{e.purpose}</td>
                <td>
                  {e.seal_intact === null ? <Badge>not sealed</Badge> : e.seal_intact ? <Badge tone="ok">intact {e.seal_number}</Badge> : <Badge tone="error">BROKEN {e.seal_number}</Badge>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {open && <CustodyForm evidence={ev} previous={entries[entries.length - 1]?.to_holder ?? ""} onDone={() => setOpen(false)} />}
    </Panel>
  );
}

function CustodyForm(props: { evidence: string; previous: string; onDone(): void }) {
  const c = useCase();
  const blank = { from: props.previous, to: "", purpose: "", seal: "", signature: "", note: "" };
  const [form, setForm] = useSessionState(`custody:form:${props.evidence}`, blank);
  const [seal, setSeal] = useState<"none" | "intact" | "broken">("none");
  const set = (key: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) => setForm({ ...form, [key]: e.target.value });
  const action = useAction(
    () =>
      run({
        kind: "caseCustody", case: c.dir, evidence: props.evidence, ...form,
        sealIntact: seal === "none" ? null : seal === "intact",
      }),
    () => {
      setForm(blank);
      c.changed();
      props.onDone();
    },
  );
  return (
    <div className="subform">
      <div className="form-grid">
        <Field label="From" hint={props.previous ? "Prefilled with the last holder, so the chain joins up" : undefined}><input value={form.from} onChange={set("from")} /></Field>
        <Field label="To"><input value={form.to} onChange={set("to")} autoFocus /></Field>
        <Field label="Purpose" wide><input value={form.purpose} onChange={set("purpose")} /></Field>
        <Field label="Seal number"><input value={form.seal} onChange={set("seal")} /></Field>
        <Field label="Seal on receipt" hint="'Not sealed' and 'broken' are different facts">
          <select value={seal} onChange={(e) => setSeal(e.target.value as typeof seal)}>
            <option value="none">Not sealed</option>
            <option value="intact">Intact</option>
            <option value="broken">Broken</option>
          </select>
        </Field>
        <Field label="Signature reference"><input value={form.signature} onChange={set("signature")} /></Field>
        <Field label="Note"><input value={form.note} onChange={set("note")} /></Field>
      </div>
      {action.error && <Notice tone="error">{action.error}</Notice>}
      <div className="form-actions">
        <Button kind="primary" onClick={action.run} busy={action.busy} disabled={!form.from || !form.to || !form.purpose}>
          Record transfer
        </Button>
        <Button kind="ghost" onClick={props.onDone}>Cancel</Button>
      </div>
    </div>
  );
}
