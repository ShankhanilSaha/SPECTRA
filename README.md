# SPECTRA — Unified Vendor-Agnostic DVR/NVR Forensic Analysis Platform

**Surveillance Platform for Evidence Carving, Timeline Reconstruction & Analysis**

SIH 2026 · Problem Statement: *Unified vendor-agnostic DVR/NVR forensic analysis platform* · Organisation: NTRO

---

## The one-paragraph version

CCTV is the single most commonly seized class of digital evidence in Indian
investigations and the single least standardised. Every DVR/NVR OEM writes its own
filesystem directly to raw disk, wraps H.264/H.265 in its own container, and keeps
its own clock. Investigators today run three to five vendor tools per case, hand-copy
timestamps, and lose recoverable footage because no tool carves the *unlinked* index
entries that a circular-overwrite recorder leaves behind. SPECTRA is one tool that
identifies the recorder from the disk itself, images it forensically, parses the
proprietary filesystem through a plugin per vendor, decodes the proprietary frame
container to standard MP4, carves deleted and overwritten-adjacent footage, normalises
every timestamp to a single UTC reference, hashes everything at every hop, correlates
events across cameras on one timeline, runs offline AI analytics over the result, and
emits a court-ready report with an unbroken chain of custody.

---

## Documentation index

| # | Document | What it is | Who reads it |
|---|---|---|---|
| 1 | [Problem Analysis & Technical Writeup](docs/01-problem-analysis.md) | The full domain writeup: why DVR forensics is hard, the file-format reality per vendor, legal framework, state of the art, gap analysis, proposed approach | Everyone. Start here. |
| 2 | [Product Requirements Document](docs/02-prd.md) | Personas, goals/non-goals, FR/NFR with IDs and priorities, acceptance criteria, metrics, milestones, risks | PM, dev leads, evaluators |
| 3 | [System Architecture](docs/03-architecture.md) | Layers, plugin contract, data model, pipelines, sequence diagrams, tech stack, security model | Engineers |
| 4 | [OEM Comparative Analysis](docs/04-oem-comparative-analysis.md) | Per-vendor filesystem/container/timestamp/recovery matrix with confidence ratings and a reverse-engineering plan for the gaps | Engineers, evaluators (named deliverable) |
| 5 | [Standard Operating Procedures](docs/05-sop.md) | Field seizure SOP, lab acquisition SOP, live-device SOP, analysis SOP, reporting SOP, with forms | Investigators (named deliverable) |
| 6 | [Validation & Test Plan](docs/06-validation-plan.md) | NIST-CFTT-style validation methodology, ground-truth dataset construction, test cases, pass/fail criteria, report template | QA, evaluators (named deliverable) |
| 7 | [User Manual](docs/07-user-manual.md) | Install, GUI walkthrough, full CLI reference, troubleshooting, FAQ | End users (named deliverable) |
| 8 | [Demo Plan & Deliverables Tracker](docs/08-demo-and-deliverables.md) | 6-minute judging demo script, deliverable-to-document mapping, team split, build order | Team, evaluators |

---

## Repository layout (target)

```
SPECTRA/
├── README.md
├── docs/                    # this documentation set
├── spectra/
│   ├── core/                # case DB, hashing, chain of custody, audit log
│   ├── identify/            # signature scanner, device fingerprinting
│   ├── acquire/             # imaging (raw/E01/AFF4), write-block verification
│   ├── plugins/             # ONE MODULE PER VENDOR — the whole point
│   │   ├── base.py          # the plugin contract
│   │   ├── hikvision.py
│   │   ├── dahua.py         # also covers CP Plus, most Godrej, some Honeywell
│   │   ├── uniview.py
│   │   ├── tplink_vigi.py
│   │   ├── matrix.py
│   │   └── generic_ext.py   # ext2/3/4 + FAT/exFAT fallback
│   ├── decode/              # container → MP4 remux, frame-header parsing
│   ├── recover/             # carving, orphan index recovery, GOP reassembly
│   ├── timeline/            # timestamp normalisation, multi-camera correlation
│   ├── ml/                  # offline face / object / motion analytics
│   ├── report/              # HTML/PDF report generation
│   └── cli.py
├── desktop/                 # Electron GUI — a client of the spectra CLI (desktop/README.md)
├── tests/
│   └── corpus/              # synthetic + ground-truth images (see doc 6)
└── tools/                   # dataset generators, signature dumpers
```

## Status

A working prototype, not a finished product. Built so far:

- the read-only evidence layer, dual hashing and the hash-chained audit log;
- identification;
- Dahua `.dav` export parsing (raw Dahua disks are carve-only until the layout is verified
  on hardware) and the Hikvision parser;
- recovery tiers T2–T4 with the coverage map, and the time model and timeline;
- motion analysis;
- the twelve-section report and the BSA s. 63(4) certificate;
- an Electron desktop app over the CLI.

Not yet built: physical acquisition, the other vendors, object and face analytics,
packaging. Hikvision recordings parse but cannot be exported yet. See
[doc 8](docs/08-demo-and-deliverables.md) for the build order and `CLAUDE.md` §13 for
detailed progress.

## Running it

You need Python 3.11 or later and Node 22 or later. The desktop app lives in `desktop/`,
which is on the `feat/desktop-prototype` branch until that is merged. Tested on Windows 11.

### Windows (PowerShell)

These commands call `python.exe` and `npm.cmd` directly, so they also work on machines
with an `AllSigned` execution policy. No venv activation is needed.

First time only, from the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
cd desktop
npm.cmd install
```

Every time, from `desktop\`:

```powershell
$env:SPECTRA_PYTHON = (Resolve-Path ..\.venv\Scripts\python.exe).Path
$env:SPECTRA_FFMPEG = & $env:SPECTRA_PYTHON -c "import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())"
npm.cmd start
```

### Linux / macOS

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cd desktop && npm install
export SPECTRA_PYTHON="$PWD/../.venv/bin/python"
export SPECTRA_FFMPEG="$("$SPECTRA_PYTHON" -c 'import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())')"
npm start
```

### In the app

- **File → New Case** or **File → Open Case** (a case is a folder containing `case.db`).
- **File → Settings** saves the Python and FFmpeg paths, so later you only need
  `npm.cmd start` (or `npm start`). The settings page also checks that both are found.
- **View → Command Log** shows every `spectra` command the app has run.

### Engine and tests only (no app)

```powershell
.\.venv\Scripts\spectra.exe --help
.\.venv\Scripts\python.exe -m pytest     # FFmpeg tests need SPECTRA_FFMPEG, set as above
```

### If something goes wrong

| Message | Fix |
|---|---|
| `npm.ps1 … is not digitally signed` | Use `npm.cmd` instead of `npm` (PowerShell `AllSigned` policy). |
| `Activate.ps1 … cannot be loaded` | Skip activation; call `.\.venv\Scripts\python.exe` directly, as above. |
| `Could not read package.json` | Run npm inside `desktop\`, not the repository root. |
| "The spectra engine could not be started" | `SPECTRA_PYTHON` or **Settings → Python** must point at the venv's `python.exe`. |
| Export gives no MP4, or motion analysis refuses | FFmpeg was not found: set `SPECTRA_FFMPEG` or **Settings → FFmpeg**. |
| E01 images will not import | `pip install -e ".[ewf]"`. libewf does not build on Python 3.14 yet, so this needs Python 3.11–3.13. |

## Licence & handling

Contains no case data, no seized-device images, and no vendor firmware. All vendor
format details in doc 4 are derived from public research and clean-room analysis of
lawfully obtained hardware; anything unverified is explicitly labelled as such.
