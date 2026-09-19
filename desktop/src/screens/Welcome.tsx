import { useEffect, useState } from "react";

import type { EnvironmentCheck, Settings } from "../../shared/api";
import { bridge, errorText, run } from "../api";
import { Button, Field, Notice, Page, Panel } from "../components/ui";

export function Welcome(props: {
  mode: "open" | "new";
  setMode(mode: "open" | "new"): void;
  onOpen(dir: string): Promise<void>;
  error: string | null;
}) {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [env, setEnv] = useState<EnvironmentCheck | null>(null);
  useEffect(() => {
    void bridge.getSettings().then(setSettings);
    void bridge.checkEnvironment().then(setEnv);
  }, []);

  const pickAndOpen = async () => {
    const dir = await bridge.pick({ title: "Open a SPECTRA case directory", kind: "directory" });
    if (dir) await props.onOpen(dir);
  };

  return (
    <Page
      title="SPECTRA"
      lead="Vendor-agnostic DVR/NVR forensic analysis. Every action in this window is an audited spectra command, run offline on this machine."
    >
      {env && !env.spectra.ok && (
        <Notice tone="error" title="The spectra engine could not be started">
          <p>{env.spectra.error}</p>
          <p>Open <a href="#/settings">Settings</a> and point it at a Python environment where spectra is installed.</p>
        </Notice>
      )}
      {props.error && <Notice tone="error" title="Could not open that case">{props.error}</Notice>}
      <div className="tabs" role="tablist">
        <button type="button" role="tab" aria-selected={props.mode === "open"} className={props.mode === "open" ? "active" : ""} onClick={() => props.setMode("open")}>
          Open a case
        </button>
        <button type="button" role="tab" aria-selected={props.mode === "new"} className={props.mode === "new" ? "active" : ""} onClick={() => props.setMode("new")}>
          New case
        </button>
      </div>
      {props.mode === "open" ? (
        <Panel title="Open a case" actions={<Button kind="primary" onClick={pickAndOpen}>Open case folder…</Button>}>
          {settings?.recentCases.length ? (
            <ul className="recent">
              {settings.recentCases.map((dir) => (
                <li key={dir}>
                  <button type="button" className="link" onClick={() => void props.onOpen(dir)}>{dir}</button>
                </li>
              ))}
            </ul>
          ) : (
            <p className="muted">No recent cases. A case is a folder containing case.db.</p>
          )}
        </Panel>
      ) : (
        <NewCase onCreated={props.onOpen} />
      )}
      {env && (
        <p className="muted small">
          Engine: {env.spectra.ok ? env.spectra.version : "not available"} · FFmpeg:{" "}
          {env.ffmpeg.ok ? env.ffmpeg.path : "not found — export gives the ES only, and motion analysis refuses"}
        </p>
      )}
    </Page>
  );
}

function slug(value: string): string {
  return value.trim().replace(/[^A-Za-z0-9._-]+/g, "-").replace(/^-+|-+$/g, "") || "case";
}

function NewCase(props: { onCreated(dir: string): Promise<void> }) {
  const [form, setForm] = useState({
    id: "", title: "", agency: "", fir: "", authority: "", examiner: "", designation: "", s79a: "",
  });
  const [parent, setParent] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const sep = parent.includes("\\") ? "\\" : "/";
  const dir = parent ? `${parent.replace(/[\\/]+$/, "")}${sep}${slug(form.id)}` : "";
  const set = (key: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setForm({ ...form, [key]: e.target.value });

  const create = async () => {
    setBusy(true);
    setError(null);
    try {
      await run({ kind: "caseNew", dir, ...form });
      await props.onCreated(dir);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Panel title="New case" actions={<span className="muted small">doc 7 §4 step 1</span>}>
      <div className="form-grid">
        <Field label="Case ID" hint="Your agency's case number"><input value={form.id} onChange={set("id")} /></Field>
        <Field label="Title"><input value={form.title} onChange={set("title")} /></Field>
        <Field label="Agency / unit" hint="Printed on the report"><input value={form.agency} onChange={set("agency")} /></Field>
        <Field label="FIR reference"><input value={form.fir} onChange={set("fir")} /></Field>
        <Field label="Authority reference"><input value={form.authority} onChange={set("authority")} /></Field>
        <Field label="Examiner"><input value={form.examiner} onChange={set("examiner")} /></Field>
        <Field label="Designation"><input value={form.designation} onChange={set("designation")} /></Field>
        <Field label="IT Act s. 79A notification" hint="If you are a notified Examiner of Electronic Evidence"><input value={form.s79a} onChange={set("s79a")} /></Field>
        <Field label="Case directory" hint="Use fast local storage. The folder is created; it must not already hold a case." wide>
          <div className="path-row">
            <input value={dir} readOnly placeholder="Choose where the case folder goes" />
            <Button onClick={async () => setParent((await bridge.pick({ title: "Folder to create the case in", kind: "directory", create: true })) ?? parent)}>
              Choose…
            </Button>
          </div>
        </Field>
      </div>
      {error && <Notice tone="error">{error}</Notice>}
      <div className="form-actions">
        <Button kind="primary" onClick={create} busy={busy} disabled={!form.id.trim() || !parent}>Create case</Button>
      </div>
    </Panel>
  );
}
