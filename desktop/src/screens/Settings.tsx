import { useEffect, useState } from "react";

import type { EnvironmentCheck, Settings } from "../../shared/api";
import { bridge } from "../api";
import { Button, Field, KeyValues, Loading, Notice, Page, Panel } from "../components/ui";
import { applySuggestion } from "./EngineSetup";

export function SettingsScreen(props: { onSaved(): Promise<void> }) {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [env, setEnv] = useState<EnvironmentCheck | null>(null);
  const [checking, setChecking] = useState(false);
  const [saved, setSaved] = useState(false);

  const check = async () => {
    setChecking(true);
    setEnv(await bridge.checkEnvironment());
    setChecking(false);
  };
  const reload = async () => {
    setSettings(await bridge.getSettings());
    await check();
    await props.onSaved();
  };
  useEffect(() => {
    void bridge.getSettings().then(setSettings);
    void check();
  }, []);

  if (!settings) return <Page title="Settings"><Loading /></Page>;
  const set = (key: keyof Settings) => (e: React.ChangeEvent<HTMLInputElement>) => {
    setSaved(false);
    setSettings({ ...settings, [key]: e.target.value });
  };

  return (
    <Page title="Settings" lead="Where the spectra engine and FFmpeg are, and who is operating. Nothing here reaches the network.">
      {env && !env.spectra.ok && (
        <Notice tone="error" title="spectra does not start with this Python">
          <pre className="engine-error">{env.spectra.error}</pre>
        </Notice>
      )}
      {env?.suggestion && (
        <Notice tone="info" title="Found on this machine">
          {env.suggestion.python && <p>A Python that runs spectra ({env.suggestion.version}): <code>{env.suggestion.python}</code></p>}
          {env.suggestion.ffmpeg && <p>An FFmpeg binary: <code>{env.suggestion.ffmpeg}</code></p>}
          <Button kind="primary" onClick={async () => { await applySuggestion(env.suggestion!); await reload(); }}>
            Use {env.suggestion.python && env.suggestion.ffmpeg ? "both" : "it"}
          </Button>
        </Notice>
      )}
      <Panel title="Engine">
        <div className="form-grid">
          <Field label="Python interpreter" hint="One that can import spectra: the repository's .venv after pip install -r requirements.txt" wide>
            <input value={settings.python} onChange={set("python")} />
          </Field>
          <Field label="spectra source directory" hint="The repository root, containing the spectra package" wide>
            <input value={settings.spectraRoot} onChange={set("spectraRoot")} />
          </Field>
          <Field label="FFmpeg binary" hint="Empty: look on PATH. Needed for the evidence MP4 and for motion analysis" wide>
            <input value={settings.ffmpeg} onChange={set("ffmpeg")} />
          </Field>
          <Field label="Operator" hint="Recorded as the operator on every audit record">
            <input value={settings.operator} onChange={set("operator")} />
          </Field>
          <Field label="New cases go in" hint="Changes to wherever you last created or opened a case">
            <input value={settings.caseParent} onChange={set("caseParent")} />
          </Field>
        </div>
        <div className="form-actions">
          <Button
            kind="primary"
            onClick={async () => {
              setSettings(await bridge.saveSettings(settings));
              setSaved(true);
              await check();
              await props.onSaved();
            }}
          >
            Save
          </Button>
          <Button onClick={check} busy={checking}>Check environment</Button>
          {saved && <span className="muted">Saved.</span>}
        </div>
      </Panel>
      {env && (
        <Panel title="Environment">
          <KeyValues
            rows={[
              ["spectra", env.spectra.ok ? env.spectra.version : "not available"],
              ["FFmpeg", env.ffmpeg.ok ? env.ffmpeg.path : "not found — export gives the elementary stream only, and motion analysis refuses"],
            ]}
          />
        </Panel>
      )}
    </Page>
  );
}
