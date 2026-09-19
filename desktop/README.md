# SPECTRA desktop

The examiner's GUI: an Electron + React shell over the `spectra` CLI. It follows the doc 7 §4
walkthrough, one screen per step: case → documents & custody → evidence → identify →
parse → time model → recover → timeline → analytics → review & export → report.

**It holds no forensic logic.** Every action runs `python -m spectra.cli … --json` as a child
process and renders what comes back (doc 3 §2, §16). So every click is an audited command
with the same result as typing it, and **View → Command Log** shows each one exactly as a
terminal would run it.

## Run it

Prerequisites: Node 22 or later, and a Python 3.11–3.13 environment that can import `spectra`.

```bash
# once, from the repository root
python -m venv .venv
.venv\Scripts\activate            # Linux/macOS: . .venv/bin/activate
pip install -r requirements.txt   # engine, tests, OpenCV, a dev FFmpeg (E01: see the file)

# once, in desktop/
npm install

# every time
npm start                         # builds, then opens the window
```

On Windows with an `AllSigned` execution policy, PowerShell refuses `npm` ("npm.ps1 is not
digitally signed") and the venv's `Activate.ps1`. Use `npm.cmd install` / `npm.cmd start`,
and call `.venv\Scripts\python.exe -m pip …` directly instead of activating. Neither needs a
policy change.

The app looks for Python in `SPECTRA_PYTHON`, then `python` on PATH, and for the package in
the repository root. Change either in **File → Settings**, which also shows whether the
engine and FFmpeg were found. Without FFmpeg, export gives the elementary stream only (no
MP4), and motion analysis refuses to run.

## How it is put together

```
desktop/
├── shared/api.ts        the typed request list — the only things the window can ask for
├── electron/
│   ├── main.ts          window, menu, IPC, session hardening
│   ├── cli.ts           request → fixed argv, spawned without a shell
│   ├── protocols.ts     spectra-app:// (the UI) and spectra-media:// (artefacts, by digest)
│   ├── settings.ts      Python / FFmpeg / operator settings, environment check
│   ├── preload.ts       the bridge exposed to the renderer
│   └── smoke.ts         screenshot every screen of a case (development check)
└── src/                 React renderer: screens/, components/, api.ts, state.tsx
```

Offline and read-only by construction:

- The renderer is sandboxed with context isolation and no Node. It can only call the named
  bridge functions in `preload.ts`.
- The window loads only from `spectra-app://` (the built UI) and `spectra-media://`.
  `spectra-media://` serves files from the open case's artefact store, by SHA-256 only.
  Every other request is cancelled, and `host-resolver-rules` makes every hostname fail
  to resolve.
- There is no dev server: the UI is built with Vite and loaded from disk, so nothing listens
  on a socket (doc 3 D5).
- The window never opens evidence. It passes paths to the CLI, which reads them through
  `EvidenceSource`.
- Values are validated in `electron/cli.ts` before they reach an argv, and options are
  passed as `--name=value`, so a value can never be read as an option.

## Check it renders

```bash
SPECTRA_UI_SMOKE_CASE=<case dir> SPECTRA_UI_SMOKE_OUT=<out dir> npm run smoke
```

This opens the case, visits every screen, and writes one PNG per screen plus the renderer
console to `console.log`. The window appears briefly: a hidden window cannot be captured on
Windows.

## Not in this prototype

- Acquisition from an attached disk, live acquisition and firmware import: the buttons are
  shown disabled and labelled.
- Object and face analytics. Synchronised multi-channel playback. Bookmarks.
- Packaging into one installer with a bundled Python (NFR-15).
- Hikvision recordings cannot yet be exported, so they cannot be played. The parser does
  not yet demux PES or set frame timing. That is an engine gap, not a UI one.
