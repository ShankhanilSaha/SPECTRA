import { useState } from "react";

import type { EnvironmentCheck } from "../../shared/api";
import { bridge, errorText } from "../api";
import { Button, KeyValues, Loading, Notice, Page, Panel } from "../components/ui";

export function Starting() {
  return (
    <Page title="Starting the engine">
      <Loading what="Checking that the spectra engine runs on this machine" />
    </Page>
  );
}

/** Apply a fix the environment check found. Only on the examiner's click. */
export async function applySuggestion(suggestion: NonNullable<EnvironmentCheck["suggestion"]>): Promise<void> {
  const settings = await bridge.getSettings();
  await bridge.saveSettings({
    ...settings,
    python: suggestion.python ?? settings.python,
    ffmpeg: suggestion.ffmpeg ?? settings.ffmpeg,
  });
}

/**
 * Shown instead of everything else while the engine cannot run: without it no screen can
 * show anything true, and a window full of empty panels hides the one thing to fix.
 */
export function EngineSetup(props: { env: EnvironmentCheck; onFixed(): Promise<void> }) {
  const { env } = props;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fix = env.suggestion?.python ? env.suggestion : null;

  const apply = async () => {
    if (!fix) return;
    setBusy(true);
    setError(null);
    try {
      await applySuggestion(fix);
      await props.onFixed();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Page
      title="The spectra engine is not starting"
      lead="Every action in this window runs the spectra engine on this machine. Until it starts, there is nothing true to show."
    >
      <Notice tone="error" title="What the configured Python said">
        <pre className="engine-error">{env.spectra.error || "no output"}</pre>
      </Notice>
      {fix ? (
        <Panel title="A working engine was found">
          <KeyValues
            rows={[
              ["Python", <code>{fix.python}</code>],
              ["Engine", fix.version],
              ["FFmpeg", fix.ffmpeg ? <code>{fix.ffmpeg}</code> : env.ffmpeg.path ?? "not found — export gives the elementary stream only"],
            ]}
          />
          {error && <Notice tone="error">{error}</Notice>}
          <div className="form-actions">
            <Button kind="primary" onClick={apply} busy={busy}>Use this Python</Button>
            <a className="btn btn-ghost" href="#/settings">Settings…</a>
          </div>
        </Panel>
      ) : (
        <Panel title="How to fix it">
          <ol className="steps-list">
            <li>
              From the repository root, create the environment once:
              <pre>python -m venv .venv{"\n"}.venv\Scripts\python.exe -m pip install -r requirements.txt</pre>
            </li>
            <li>
              Open <a href="#/settings">Settings</a> and point <em>Python interpreter</em> at <code>.venv\Scripts\python.exe</code>, or
              start the app with <code>SPECTRA_PYTHON</code> set.
            </li>
          </ol>
          <div className="form-actions">
            <Button kind="primary" onClick={() => void props.onFixed()}>Check again</Button>
            <a className="btn btn-ghost" href="#/settings">Settings…</a>
          </div>
        </Panel>
      )}
    </Page>
  );
}
