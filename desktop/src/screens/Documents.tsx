import { bridge, run } from "../api";
import { bytes } from "../format";
import type { Attachment } from "../types";
import { StepPage } from "../components/StepNav";
import { Button, Empty, Field, Hash, Notice, Panel, useAction } from "../components/ui";
import { useCase, useSessionState } from "../state";

export const DOC_KINDS: [string, string][] = [
  ["panchnama", "Panchnama / seizure memo"],
  ["seizure_video", "BNSS s. 105 seizure video"],
  ["seizure_form", "Form F-1 (seizure record)"],
  ["custody_form", "Form F-2 (chain of custody)"],
  ["acquisition_form", "Form F-3 (acquisition record)"],
  ["authorisation", "Authorisation / court order"],
  ["photograph", "Photograph (scene, cabling)"],
  ["correspondence", "Correspondence"],
  ["other", "Other"],
];

const KIND_LABEL = new Map(DOC_KINDS);

export function Documents() {
  const c = useCase();
  const docs = c.info?.progress.attachments ?? [];
  return (
    <StepPage
      step="documents"
      lead="Attach and hash the seizure memo, Form F-1 and the s. 105 seizure video before anything else: the custody chain then starts at the scene, not at your bench. F-1 also carries the reference-clock capture you will need in step 6."
    >
      <div className="grid-2">
        <AttachForm />
        <Panel title={`Attached (${docs.length})`}>
          {!docs.length ? (
            <Empty>Nothing attached yet. If the documents are not with you, skip this step and come back when they are.</Empty>
          ) : (
            <table className="table">
              <thead><tr><th>ID</th><th>Document</th><th className="num">Size</th><th>SHA-256</th></tr></thead>
              <tbody>
                {docs.map((d) => (
                  <tr key={d.id}>
                    <td>{d.id}{d.evidence_id && <div className="muted small">{d.evidence_id}</div>}</td>
                    <td>
                      {KIND_LABEL.get(d.kind) ?? d.kind}
                      <div className="muted small">{d.filename}{d.description ? ` · ${d.description}` : ""}</div>
                    </td>
                    <td className="num">{bytes(d.size_bytes)}</td>
                    <td><Hash value={d.sha256} short /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Panel>
      </div>
    </StepPage>
  );
}

function AttachForm() {
  const c = useCase();
  const [form, setForm] = useSessionState("documents:form", {
    file: "", docKind: "panchnama", description: "", providedBy: "", linked: false,
  });
  const [done, setDone] = useSessionState<Attachment | null>("documents:done", null);
  const action = useAction(
    () =>
      run<Attachment>({
        kind: "caseAttach", case: c.dir, file: form.file, docKind: form.docKind,
        description: form.description, providedBy: form.providedBy,
        evidence: form.linked ? c.evidenceId : null,
      }),
    (ref) => {
      setDone(ref);
      setForm({ ...form, file: "", description: "" });
      c.changed();
    },
  );
  const choose = async () => {
    const file = await bridge.pick({ title: "Document to attach", kind: "file" });
    if (file) setForm({ ...form, file });
  };
  return (
    <Panel title="Attach a document">
      <div className="form-grid">
        <Field label="File" wide>
          <div className="path-row">
            <input value={form.file} readOnly placeholder="Choose a file" onClick={choose} />
            <Button onClick={choose}>Choose…</Button>
          </div>
        </Field>
        <Field label="Kind">
          <select value={form.docKind} onChange={(e) => setForm({ ...form, docKind: e.target.value })}>
            {DOC_KINDS.map(([k, label]) => <option key={k} value={k}>{label}</option>)}
          </select>
        </Field>
        <Field label="Provided by"><input value={form.providedBy} onChange={(e) => setForm({ ...form, providedBy: e.target.value })} /></Field>
        <Field label="Description" wide><input value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} /></Field>
        {c.evidenceId && (
          <label className="check">
            <input type="checkbox" checked={form.linked} onChange={(e) => setForm({ ...form, linked: e.target.checked })} /> Concerns {c.evidenceId} only
          </label>
        )}
      </div>
      {action.error && <Notice tone="error">{action.error}</Notice>}
      {done && !action.error && <Notice tone="ok" title={`Attached ${done.id} · ${done.filename}`}><Hash label="SHA-256" value={done.sha256} /></Notice>}
      <div className="form-actions">
        <Button kind="primary" onClick={action.run} busy={action.busy} disabled={!form.file}>Attach and hash</Button>
      </div>
    </Panel>
  );
}
