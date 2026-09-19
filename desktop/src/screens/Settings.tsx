import { useEffect, useState } from "react";

import type { EnvironmentCheck, Settings } from "../../shared/api";
import { bridge } from "../api";
import { Button, Field, KeyValues, Notice, Page, Panel } from "../components/ui";

export function SettingsScreen() {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [env, setEnv] = useState<EnvironmentCheck | null>(null);
  const [checking, setChecking] = useState(false);
  const [saved, setSaved] = useState(false);

  const check = async () => {
    setChecking(true);
    setEnv(await bridge.checkEnvironment());
    setChecking(false);
  };
  useEffect(() => {
    void bridge.getSettings().then(setSettings);
    void check();
  }, []);

  if (!settings) return null;
  const set = (key: keyof Settings) => (e: React.ChangeEvent<HTMLInputElement>) => {
    setSaved(false);
    setSettings({ ...settings, [key]: e.target.value });
  };

  return (
    <Page title="Settings" lead="Where the spectra engine and FFmpeg are. Nothing here reaches the network.">
      <Panel title="Engine">
        <div className="form-grid">
          <Field label="Python interpreter" hint="One that can import spectra (pip install -e '.[dev,ml]')" wide>
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
        </div>
        <div className="form-actions">
          <Button
            kind="primary"
            onClick={async () => {
              setSettings(await bridge.saveSettings(settings));
              setSaved(true);
              await check();
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
          {!env.spectra.ok && <Notice tone="error" title="spectra did not start">{env.spectra.error}</Notice>}
          <KeyValues
            rows={[
              ["spectra", env.spectra.ok ? env.spectra.version : "not available"],
              ["FFmpeg", env.ffmpeg.ok ? env.ffmpeg.path : "not found"],
            ]}
          />
        </Panel>
      )}
    </Page>
  );
}
