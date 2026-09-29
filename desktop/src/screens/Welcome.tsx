import { useEffect, useState, type FormEvent } from "react";

import type { EnvironmentCheck, Settings } from "../../shared/api";
import { bridge, errorText, run } from "../api";
import { Button, Field, Notice, Page, Panel } from "../components/ui";
import { applySuggestion } from "./EngineSetup";

export function Welcome(props: {
  env: EnvironmentCheck;
  onOpen(dir: string): Promise<void>;
  onFixed(): Promise<void>;
  error: string | null;
}) {
  const [settings, setSettings] = useState<Settings | null>(null);
  useEffect(() => {
    void bridge.getSettings().then(setSettings);
  }, []);

  const browse = async () => {
    const dir = await bridge.pick({ title: "Open a SPECTRA case folder", kind: "directory", defaultPath: settings?.caseParent });
    if (dir) await props.onOpen(dir);
  };

  return (
    <Page
      title="Start an examination"
      lead="Every action in this window is an audited spectra command, run offline on this machine. Nothing leaves it."
    >
      {props.error && <Notice tone="error" title="Could not open that case">{props.error}</Notice>}
      <FfmpegNotice env={props.env} onFixed={props.onFixed} />
      <div className="welcome">
        {settings && <NewCase settings={settings} onCreated={props.onOpen} />}
        <Panel title="Continue a case">
          {settings?.recentCases.length ? (
            <ul className="recent">
              {settings.recentCases.map((dir) => {
                const parts = dir.split(/[\\/]/);
                return (
                  <li key={dir}>
                    <button type="button" className="recent-item" onClick={() => void props.onOpen(dir)}>
                      <strong>{parts.pop()}</strong>
                      <span className="muted small">{parts.join("\\")}</span>
                    </button>
                  </li>
                );
              })}
            </ul>
          ) : (
            <p className="muted">No recent cases on this machine. A case is a folder containing <code>case.db</code>.</p>
          )}
          <div className="form-actions">
            <Button onClick={browse}>Open another case folder…</Button>
          </div>
        </Panel>
      </div>
      <p className="muted small env-line">
        Engine {props.env.spectra.version} · FFmpeg {props.env.ffmpeg.ok ? props.env.ffmpeg.path : "not found"}
      </p>
    </Page>
  );
}

function FfmpegNotice(props: { env: EnvironmentCheck; onFixed(): Promise<void> }) {
  const [busy, setBusy] = useState(false);
  if (props.env.ffmpeg.ok) return null;
  const found = props.env.suggestion?.ffmpeg;
  return (
    <Notice tone="warn" title="FFmpeg not found">
      Without it, export gives the elementary stream only (no playable MP4) and motion analysis refuses to run.{" "}
      {found ? (
        <>
          One was found at <code>{found}</code>.{" "}
          <Button
            busy={busy}
            onClick={async () => {
              setBusy(true);
              await applySuggestion({ python: null, version: null, ffmpeg: found });
              await props.onFixed();
              setBusy(false);
            }}
          >
            Use it
          </Button>
        </>
      ) : (
        <>Set it in <a href="#/settings">Settings</a>.</>
      )}
    </Notice>
  );
}

/** A folder a sync client uploads. Only a hint from the path: it cannot prove a folder is
 * not synced, so it warns and never blocks. */
const SYNCED = /[\\/](OneDrive[^\\/]*|Dropbox|Google Drive|My Drive|iCloud ?Drive|Box)([\\/]|$)/i;

function slug(value: string): string {
  return value.trim().replace(/[^A-Za-z0-9._-]+/g, "-").replace(/^-+|-+$/g, "");
}

function NewCase(props: { settings: Settings; onCreated(dir: string): Promise<void> }) {
  const [form, setForm] = useState({
    id: "", title: "", agency: "", fir: "", authority: "", examiner: "", designation: "", s79a: "",
  });
  const [parent, setParent] = useState(props.settings.caseParent);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const sep = parent.includes("\\") ? "\\" : "/";
  const name = slug(form.id);
  const dir = name ? `${parent.replace(/[\\/]+$/, "")}${sep}${name}` : "";
  const set = (key: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setForm({ ...form, [key]: e.target.value });

  const create = async (e: FormEvent) => {
    e.preventDefault();
    if (!dir) return;
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
    <Panel title="New case">
      <form onSubmit={create}>
        <div className="form-grid">
          <Field label="Case ID" hint="Your agency's case number. Names the case folder.">
            <input value={form.id} onChange={set("id")} autoFocus required />
          </Field>
          <Field label="Title"><input value={form.title} onChange={set("title")} /></Field>
          <Field label="Agency / unit" hint="Printed on the report"><input value={form.agency} onChange={set("agency")} /></Field>
          <Field label="FIR reference"><input value={form.fir} onChange={set("fir")} /></Field>
          <Field label="Examiner"><input value={form.examiner} onChange={set("examiner")} /></Field>
          <Field label="Designation"><input value={form.designation} onChange={set("designation")} /></Field>
          <Field label="Authority reference"><input value={form.authority} onChange={set("authority")} /></Field>
          <Field label="IT Act s. 79A notification" hint="If you are a notified examiner"><input value={form.s79a} onChange={set("s79a")} /></Field>
        </div>
        <div className="case-location">
          <span className="muted small">Case folder</span>
          <code>{dir || `${parent}${sep}<case ID>`}</code>
          <button
            type="button"
            className="link small"
            onClick={async () => setParent((await bridge.pick({ title: "Folder to create the case in", kind: "directory", create: true, defaultPath: parent })) ?? parent)}
          >
            change…
          </button>
        </div>
        {SYNCED.test(parent) && (
          <Notice tone="warn" title="This folder looks cloud-synced">
            A case folder here would be uploaded by the sync client, and case data must not leave this machine. Choose a local folder.
          </Notice>
        )}
        {error && <Notice tone="error">{error}</Notice>}
        <div className="form-actions">
          <Button kind="primary" type="submit" busy={busy} disabled={!name}>Create case and start</Button>
          <span className="muted small">Use fast local storage. The folder must be new or empty.</span>
        </div>
      </form>
    </Panel>
  );
}
