import { useState } from "react";

import type { Provenance } from "../../shared/api";
import { bridge, run } from "../api";
import { bytes } from "../format";
import type { Ingest } from "../types";
import { Badge, Button, Field, Hash, KeyValues, Notice, Page, Panel, useAction } from "../components/ui";
import { useCase } from "../state";

const PROVENANCE: [Provenance, string, string][] = [
  ["A", "Physical image, hardware write-blocked", "Strongest: bit-for-bit, verifiably unaltered."],
  ["B", "Physical image, other protection", "The protection method and its justification are recorded."],
  ["C", "Live logical acquisition", "The source could not be independently verified."],
  ["D", "Third-party export", "Original storage not examined; completeness unverifiable."],
];

export function Evidence() {
  const c = useCase();
  const [mode, setMode] = useState<"image" | "files">("image");
  const [last, setLast] = useState<Ingest | null>(null);
  return (
    <Page step={3} title="Evidence" lead="Add evidence. SPECTRA reads it through its read-only layer only; nothing here writes to the image or the source files.">
      <div className="grid-2">
        <Panel title="Add evidence">
          <div className="choice-list">
            <Choice active={mode === "image"} onClick={() => setMode("image")} title="Import existing image" text="Someone else imaged it: raw, split-raw (.001) or E01." />
            <Choice active={mode === "files"} onClick={() => setMode("files")} title="Import export files" text="The owner handed over .dav files. Recorded as provenance class D." />
            <Choice disabled title="Acquire from attached disk" text="Physical acquisition with write-blocker verification — finals (doc 8, phase 2)." />
            <Choice disabled title="Live acquisition" text="Recorder cannot be powered down — finals (FR-16)." />
            <Choice disabled title="Import firmware dump" text="SPI/NAND flash — finals (FR-05)." />
          </div>
        </Panel>
        {mode === "image" ? <ImportImage onDone={setLast} /> : <ImportFiles onDone={setLast} />}
      </div>
      {last && (
        <Panel title={`Ingested ${last.evidence_id}`}>
          {last.gaps.length > 0 && <Notice tone="warn">{last.gaps.length} unreadable range(s) were zero-filled and recorded.</Notice>}
          {last.embedded_hash_check.status === "MISMATCH" && (
            <Notice tone="error">The MD5 stored in the E01 does not match its media — the image is corrupt or altered. This is recorded on the evidence item.</Notice>
          )}
          <KeyValues rows={[["Size", bytes(last.size)], ["MD5", <Hash value={last.md5} />], ["SHA-256", <Hash value={last.sha256} />], ["Format", last.source_format]]} />
          <div className="form-actions">
            <Button kind="primary" onClick={() => { c.setEvidenceId(last.evidence_id); window.location.hash = "#/identify"; }}>
              Identify {last.evidence_id} →
            </Button>
          </div>
        </Panel>
      )}
    </Page>
  );
}

function Choice(props: { title: string; text: string; active?: boolean; disabled?: boolean; onClick?: () => void }) {
  return (
    <button type="button" className={`choice ${props.active ? "active" : ""}`} disabled={props.disabled} onClick={props.onClick}>
      <strong>{props.title}</strong>
      <span>{props.text}</span>
      {props.disabled && <Badge>not in this prototype</Badge>}
    </button>
  );
}

function ImportImage(props: { onDone(r: Ingest): void }) {
  const c = useCase();
  const [file, setFile] = useState("");
  const [provenance, setProvenance] = useState<Provenance | null>(null);
  const [label, setLabel] = useState("");
  const [note, setNote] = useState("");
  const action = useAction(
    () => run<Ingest>({ kind: "importImage", case: c.dir, file, provenance: provenance!, label, note }),
    (r) => { props.onDone(r); c.changed(); },
  );
  return (
    <Panel title="Import existing image">
      <div className="form-grid">
        <Field label="Image file" wide>
          <div className="path-row">
            <input value={file} readOnly placeholder="Choose an image" />
            <Button onClick={async () => setFile((await bridge.pick({ title: "Evidence image", kind: "file", extensions: ["dd", "img", "raw", "001", "E01", "e01"] })) ?? file)}>Choose…</Button>
          </div>
        </Field>
        <fieldset className="field-wide radio-group">
          <legend>Provenance class — you state it; SPECTRA did not acquire this image and cannot know (FR-19)</legend>
          {PROVENANCE.map(([cls, title, text]) => (
            <label key={cls} className={`radio ${provenance === cls ? "active" : ""}`}>
              <input type="radio" name="prov" checked={provenance === cls} onChange={() => setProvenance(cls)} />
              <span><strong>Class {cls}</strong> — {title}<br /><span className="muted small">{text}</span></span>
            </label>
          ))}
        </fieldset>
        <Field label="Label" hint="e.g. the device's badge: 'CP Plus DVR, cash counter'"><input value={label} onChange={(e) => setLabel(e.target.value)} /></Field>
        <Field label="Note"><input value={note} onChange={(e) => setNote(e.target.value)} /></Field>
      </div>
      {action.error && <Notice tone="error">{action.error}</Notice>}
      <div className="form-actions">
        <Button kind="primary" onClick={action.run} busy={action.busy} disabled={!file || !provenance}>Import and hash</Button>
      </div>
    </Panel>
  );
}

function ImportFiles(props: { onDone(r: Ingest): void }) {
  const c = useCase();
  const [dir, setDir] = useState("");
  const [label, setLabel] = useState("");
  const [note, setNote] = useState("");
  const action = useAction(
    () => run<Ingest>({ kind: "importFiles", case: c.dir, dir, label, note }),
    (r) => { props.onDone(r); c.changed(); },
  );
  return (
    <Panel title="Import export files (class D)">
      <Notice>Image the whole USB stick where you can, and recommend seizing the source recorder (SOP-4). Export files alone cannot show what the recorder did not export.</Notice>
      <div className="form-grid">
        <Field label="Folder of export files" wide>
          <div className="path-row">
            <input value={dir} readOnly placeholder="Choose a folder" />
            <Button onClick={async () => setDir((await bridge.pick({ title: "Folder of export files", kind: "directory" })) ?? dir)}>Choose…</Button>
          </div>
        </Field>
        <Field label="Label"><input value={label} onChange={(e) => setLabel(e.target.value)} /></Field>
        <Field label="Note"><input value={note} onChange={(e) => setNote(e.target.value)} /></Field>
      </div>
      {action.error && <Notice tone="error">{action.error}</Notice>}
      <div className="form-actions">
        <Button kind="primary" onClick={action.run} busy={action.busy} disabled={!dir}>Import and hash</Button>
      </div>
    </Panel>
  );
}
