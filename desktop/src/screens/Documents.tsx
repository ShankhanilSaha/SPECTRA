import { useState } from "react";

import { bridge, run, useCli } from "../api";
import { bytes } from "../format";
import type { Attachment, CaseChain } from "../types";
import { Badge, Button, Empty, Field, Hash, Loading, Notice, Page, Panel, useAction } from "../components/ui";
import { useCase } from "../state";

const KINDS: [string, string][] = [
  ["panchnama", "Panchnama / seizure memo"],
  ["seizure_video", "BNSS s. 105 seizure video"],
  ["authorisation", "Authorisation / court order"],
  ["seizure_form", "Form F-1 (seizure record)"],
  ["custody_form", "Form F-2 (chain of custody)"],
  ["acquisition_form", "Form F-3 (acquisition record)"],
  ["photograph", "Photograph (scene, cabling)"],
  ["correspondence", "Correspondence"],
  ["other", "Other"],
];

export function Documents() {
  const c = useCase();
  const chain = useCli<CaseChain>(
    c.evidenceId ? { kind: "caseChain", case: c.dir, evidence: c.evidenceId } : null,
    `${c.dir}|${c.evidenceId}|${c.version}`,
  );
  return (
    <Page
      step={2}
      title="Documents & custody"
      lead="Attach and hash the seizure memo, Form F-1 and the s. 105 seizure video first: the custody chain then starts at the scene, not at your bench."
    >
      <div className="grid-2">
        <AttachForm />
        <CustodyForm />
      </div>
      {!c.evidenceId ? (
        <Notice>Custody transfers are recorded per evidence item — add evidence to see its chain.</Notice>
      ) : chain.loading && !chain.data ? (
        <Loading />
      ) : chain.error ? (
        <Notice tone="error">{chain.error}</Notice>
      ) : chain.data ? (
        <>
          <Panel title={`Custody chain · ${chain.data.evidence_id}`}>
            {chain.data.chain_breaks.map((b) => <Notice key={b} tone="warn" title="Chain gap">{b}</Notice>)}
            {chain.data.custody.length === 0 ? (
              <Empty>No transfers recorded — the chain is not established.</Empty>
            ) : (
              <table className="table">
                <thead><tr><th>#</th><th>When (UTC)</th><th>From → To</th><th>Purpose</th><th>Seal</th></tr></thead>
                <tbody>
                  {chain.data.custody.map((e) => (
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
          </Panel>
          <Panel title="Attached documents">
            <AttachmentTable rows={chain.data.attachments} />
          </Panel>
        </>
      ) : null}
    </Page>
  );
}

function AttachmentTable(props: { rows: Attachment[] }) {
  if (!props.rows.length) return <Empty>No documents attached yet.</Empty>;
  return (
    <table className="table">
      <thead><tr><th>ID</th><th>Kind</th><th>File</th><th>Size</th><th>SHA-256</th><th>Under</th></tr></thead>
      <tbody>
        {props.rows.map((d) => (
          <tr key={d.id}>
            <td>{d.id}</td>
            <td>{d.kind}</td>
            <td>{d.filename}{d.description && <div className="muted small">{d.description}</div>}</td>
            <td className="num">{bytes(d.size_bytes)}</td>
            <td><Hash value={d.sha256} /></td>
            <td className="small">{d.statutory_ref}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function AttachForm() {
  const c = useCase();
  const [file, setFile] = useState("");
  const [docKind, setDocKind] = useState("panchnama");
  const [description, setDescription] = useState("");
  const [providedBy, setProvidedBy] = useState("");
  const [linked, setLinked] = useState(true);
  const [done, setDone] = useState<Attachment | null>(null);
  const action = useAction(
    () =>
      run<Attachment>({
        kind: "caseAttach", case: c.dir, file, docKind, description, providedBy,
        evidence: linked ? c.evidenceId : null,
      }),
    (ref) => {
      setDone(ref);
      setFile("");
      setDescription("");
      c.changed();
    },
  );
  return (
    <Panel title="Attach a document">
      <div className="form-grid">
        <Field label="File" wide>
          <div className="path-row">
            <input value={file} readOnly placeholder="Choose a file" />
            <Button onClick={async () => setFile((await bridge.pick({ title: "Document to attach", kind: "file" })) ?? file)}>Choose…</Button>
          </div>
        </Field>
        <Field label="Kind">
          <select value={docKind} onChange={(e) => setDocKind(e.target.value)}>
            {KINDS.map(([k, label]) => <option key={k} value={k}>{label}</option>)}
          </select>
        </Field>
        <Field label="Provided by"><input value={providedBy} onChange={(e) => setProvidedBy(e.target.value)} /></Field>
        <Field label="Description" wide><input value={description} onChange={(e) => setDescription(e.target.value)} /></Field>
        {c.evidenceId && (
          <label className="check">
            <input type="checkbox" checked={linked} onChange={(e) => setLinked(e.target.checked)} /> Link to {c.evidenceId}
          </label>
        )}
      </div>
      {action.error && <Notice tone="error">{action.error}</Notice>}
      {done && <Notice tone="ok" title={`Attached ${done.id}`}><Hash label="SHA-256" value={done.sha256} /></Notice>}
      <div className="form-actions">
        <Button kind="primary" onClick={action.run} busy={action.busy} disabled={!file}>Attach and hash</Button>
      </div>
    </Panel>
  );
}

function CustodyForm() {
  const c = useCase();
  const [form, setForm] = useState({ from: "", to: "", purpose: "", seal: "", signature: "", note: "" });
  const [seal, setSeal] = useState<"none" | "intact" | "broken">("none");
  const set = (key: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) => setForm({ ...form, [key]: e.target.value });
  const action = useAction(
    () =>
      run({
        kind: "caseCustody", case: c.dir, evidence: c.evidenceId!, ...form,
        sealIntact: seal === "none" ? null : seal === "intact",
      }),
    () => {
      setForm({ from: "", to: "", purpose: "", seal: "", signature: "", note: "" });
      c.changed();
    },
  );
  return (
    <Panel title="Record a custody transfer (Form F-2)">
      {!c.evidenceId ? (
        <Empty>Add an evidence item first.</Empty>
      ) : (
        <>
          <div className="form-grid">
            <Field label="From"><input value={form.from} onChange={set("from")} /></Field>
            <Field label="To"><input value={form.to} onChange={set("to")} /></Field>
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
              Record transfer for {c.evidenceId}
            </Button>
          </div>
        </>
      )}
    </Panel>
  );
}
