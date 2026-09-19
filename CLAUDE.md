# CLAUDE.md — SPECTRA

Working reference for Claude Code sessions (and any teammate) in this repository.

**How to use this file.** It is a self-contained briefing: enough to make correct
decisions without re-reading all eight docs, with a pointer to the exact doc section
wherever depth is needed. Read §1–§5 before any task; read the section matching the task
(architecture, formats, legal, quality, work split) before starting it.

**Authority.** `docs/` > this file > everything else. If this file and a doc disagree,
the doc wins — fix this file in the same change. If a doc is wrong, raise it; don't
silently diverge.

---

## Contents

1. [Project at a glance](#1-project-at-a-glance)
2. [Domain primer — why this problem is hard](#2-domain-primer--why-this-problem-is-hard)
3. [Users, goals, and scope](#3-users-goals-and-scope)
4. [Sources of truth](#4-sources-of-truth)
5. [Non-negotiable rules (and why)](#5-non-negotiable-rules-and-why)
6. [Architecture](#6-architecture)
7. [Format families](#7-format-families)
8. [Legal and procedural framework](#8-legal-and-procedural-framework)
9. [Quality bar — NFRs, acceptance criteria, validation](#9-quality-bar--nfrs-acceptance-criteria-validation)
10. [Vocabulary, ID schemes, glossary](#10-vocabulary-id-schemes-glossary)
11. [Build phases and staging](#11-build-phases-and-staging)
12. [Work split — six sections P1–P6](#12-work-split--six-sections-p1p6)
13. [Shankhanil — P1 + P2 task list](#13-shankhanil--p1--p2-task-list)
14. [Team contracts](#14-team-contracts--frozen-at-end-of-phase-1)
15. [Engineering and documentation conventions](#15-engineering-and-documentation-conventions)
16. [Risks](#16-risks)
17. [Open issues not yet in the docs](#17-open-issues-not-yet-in-the-docs)
18. [Notes for Claude](#18-notes-for-claude)

---

## 1. Project at a glance

| | |
|---|---|
| **Product** | **SPECTRA** — Unified Vendor-Agnostic DVR/NVR Forensic Analysis Platform (*Surveillance Platform for Evidence Carving, Timeline Reconstruction & Analysis*) |
| **Context** | Smart India Hackathon (SIH) 2026 · problem statement **SIH26150** · organisation **NTRO** |
| **Naming** | One name everywhere: repo/folder `SPECTRA`, product **SPECTRA** in prose, Python package `spectra/`, CLI command `spectra`. Renamed from the earlier working name "SENTINEL" on 2026-09-15 — any remaining occurrence is a leftover to fix |
| **Status (as of 2026-09-18)** | **Phases 0, 4, 5 and 7 built; 1 and 3 partly built; 6 partly built.** Added since 2026-09-15: `plugins/hikvision.py` (Phase 4), `recover/` T1–T4 + coverage + merge (Phase 5), `report/` with `findings.json`, the BSA s. 63(4) certificate and the twelve-section report (Phase 7), `timeline/anomalies.py` (FR-55), custody and attachments (FR-73, FR-74). `acquire/` (Phase 2) and `ui/` do not exist yet. Dahua on-disk parsing is still blocked on a verified superblock layout (§17 item 11). Check `git log` / `git status` for current state |
| **Team** | 6 people, 6 work sections P1–P6 (§12). **Shankhanil (git `ShankhanilSaha`) owns P1 and P2** (§13). Other owners not yet recorded |
| **SIH stages** | **Pre-selection** = everything built before the team is shortlisted (demo-visible pipeline + deck). **Finals** = everything built after shortlisting, for the grand finale. See §11 |
| **Dev machine** | Windows 11, PowerShell primary, Git Bash available. Product targets Linux (primary) and Windows 10/11 (NFR-06) — code must be portable |

**The one-paragraph version.** CCTV is the most commonly seized class of digital
evidence in Indian investigations and the least standardised. Every DVR/NVR OEM writes
its own filesystem straight onto raw disk, wraps H.264/H.265 in its own container, and
keeps its own (usually wrong) clock. Investigators run 3–5 vendor tools per case,
hand-copy timestamps into Excel, and lose recoverable footage because nothing looks at
the index entries a circular-overwrite recorder has freed but not yet overwritten.
SPECTRA is one offline desktop tool that:

1. identifies the recorder's **format family** from the disk bytes,
2. images the disk forensically with hashes at every hop,
3. parses the proprietary filesystem through **one plugin per family**,
4. decodes the vendor frame container and **remuxes (never transcodes)** to MP4,
5. **recovers deleted and partially overwritten footage** (tiers T1–T4),
6. normalises every timestamp to UTC with a **measured** offset and uncertainty — or
   refuses to assert absolute time,
7. correlates channels and devices on one timeline, with gaps treated as findings,
8. runs **offline** analytics that produce leads, never identifications,
9. emits a court-ready report with auto-generated negative findings, a **BSA s. 63(4)
   certificate**, and a hash-chained audit log.

**Positioning (doc 2 §1):** *DVR Examiner's job, done openly, with Indian legal output
and deleted-footage recovery that actually looks where the footage still is.*

---

## 2. Domain primer — why this problem is hard

Summarised from doc 1 §1–§4. Every design decision in the project traces back here.

### 2.1 What a DVR/NVR is

- **DVR** encodes analogue camera input (CVBS, HDCVI/TVI/AHD) to H.264/H.265 in
  hardware and writes continuously to internal SATA disks. **NVR** receives already
  encoded IP-camera streams (RTSP/ONVIF) and mostly muxes and writes. Hybrids do both.
- Hardware: a low-power SoC (HiSilicon dominates Chinese OEMs; also Novatek, Grain Media,
  Ambarella) running stripped Linux, 2–64 GB firmware flash, 1–48 TB SATA storage.
- **Two storage domains, handled separately:**
  - *Firmware/config flash* (SPI-NOR/NAND): device identity, **camera-name → channel
    map**, **timezone/NTP config and RTC settings**, user accounts, **system event log**
    (power cycles, logins, formats, video loss). Most workflows ignore it — a mistake.
  - *Video disk*: the proprietary filesystem and the footage itself.

### 2.2 The disk is not a normal filesystem

The video disk has **no partition table, no ext4/NTFS superblock** — a workstation sees
"unallocated" or offers to format it. Vendors write a fixed-geometry block allocator for
sustained sequential writes and power-loss tolerance. Nearly all converge on:

```
SUPERBLOCK   vendor magic · capacity · format version/time · block size (64 MB–1 GB) ·
             block count · offsets of index and log areas
SYSTEM LOG   ring of fixed-size records: power, disk error, format, login, video loss
INDEX        (often duplicated) entry = {data_block_id, channel, start, end,
             flags in-use/free/locked/event, stream type, resolution}
DATA BLOCKS  fixed size, one channel's contiguous stream of vendor-framed frames;
             allocation wraps around to reuse the oldest block
```

That shape reduces every vendor to **four questions** — the basis of the plugin contract:

1. Where is the superblock and what is its magic? → identification (`probe`, `superblock`)
2. How do I walk the index? → enumerating recordings (`enumerate`)
3. How do I turn (block, byte range) into frames? → the container (`frames`)
4. What do the timestamp fields mean? → epoch, packing, endianness, timezone (`decode_time`)

### 2.3 The container layer

The codec is standard; the **container is proprietary**.

- **Dahua family** — every frame starts `DHAV` and ends `dhav`, with frame type,
  channel, sequence counter, length, and packed date-time in the header. **Self-describing
  per frame**: a data block can be parsed with the index destroyed and every frame still
  carries its own channel and time. This is the single most useful property in the project.
- **Hikvision family** — payload close to MPEG-2 Program Stream (`00 00 01 BA` pack,
  `00 00 01 E0` PES) with vendor private headers carrying time/channel; per-block
  frame-offset table. Carving works; channel attribution of orphaned blocks sometimes lost.
- **TP-Link VIGI / modern entrants** — trend to ext4 + near-standard MP4/fMP4. Easiest.
- **Uniview / Matrix / Honeywell native** — poorly documented; reverse engineering needed.
- Vendor codec variants (Hikvision H.264+/H.265+, Dahua Smart H.264/H.265) use reference
  and background-model tricks some decoders mishandle.

### 2.4 Why deleted-footage recovery works

Recorders never "delete" — when full they mark the oldest block's index entry free and
overwrite that block. Two consequences:

1. **A freed entry is not yet a destroyed block.** Between freeing and physical
   overwrite, the video is intact and invisible to vendor software. On a disk pulled from
   service (the forensic case) nothing writes any more, so that window is **permanent**.
2. **Partial overwrite leaves partial GOPs.** A half-overwritten 256 MB block still holds
   ~128 MB of the old recording; resume from the first intact I-frame after the boundary.

No commonly available tool carves these regions. This is the headline differentiator.

### 2.5 Why timestamps are the most legally dangerous part

Six independent, compounding error sources: (1) **RTC drift** — dead backup batteries,
units 20–90 min wrong, OSD confidently shows the wrong time; (2) **timezone** — firmware
defaults of UTC+8 or UTC+0 never corrected; (3) **epoch and packing** — Unix LE32 vs
bit-packed calendar vs vendor epoch, and a wrong guess yields a *plausible but wrong*
date; (4) **cross-device skew** — three premises, three wrong clocks; (5) **frame vs
index time** diverge after power events (frame time authoritative for seeking, index time
for enumeration); (6) **OSD vs metadata** diverge after manual clock changes. Hence the
three-layer time model (§6.6) and the refusal to fabricate.

### 2.6 The leverage: eight brands, fewer formats

The eight OEMs named in the problem statement — **Dahua, Hikvision, CP Plus, Godrej,
Honeywell, Uniview, TP-Link, Matrix** — are not eight formats. CP Plus ships Dahua-ODM
hardware; Godrej and Honeywell source from several ODMs by SKU and year. They collapse
into ~6 *format families* (§7). Build Dahua and Hikvision well and most seized units are
covered. **This is why parser selection keys on disk bytes, never the badge.**

### 2.7 Today's broken workflow (the pitch)

FTK Imager → DVR Examiner (commercial, per-seat) → vendor SmartPlayer (unlogged) → manual
FFmpeg → VLC → **hand-typed Excel timeline** → HashCalc (hashing *after* conversion) →
Word report. Seven tools, four with no audit trail; every hand-off is a question defence
counsel asks and nobody can answer. Vendor players also don't hash, don't log, don't show
what they couldn't read, often transcode silently on export, need working vendor hardware,
and a live recorder writes to itself during its own export.

**State of the art gap (doc 1 §6):** DVR Examiner is closed, costly, foreign, weak on
Indian brands, no BSA certificate, no cross-device time reconciliation. Amped FIVE
enhances/authenticates but doesn't parse DVR disks. FTK/EnCase/Autopsy/X-Ways have no DVR
parsers. Foremost/Scalpel/PhotoRec don't know DVR frame signatures. Academic papers cover
one format each and aren't maintained tools.

---

## 3. Users, goals, and scope

### 3.1 Personas (doc 2 §3) — note these use P1–P5, unrelated to work sections

| Persona | Who | Success looks like |
|---|---|---|
| P1 Field Investigating Officer | District cyber cell SI; seizes at the scene; not a specialist | Device reaches the lab with a valid seizure record and a measured clock offset |
| **P2 Forensic Examiner** (primary user) | CFSL / State FSL / notified s. 79A examiner; signs and defends the report | Two-recorder, 16-channel, 30-day case in one working day; can answer "how do you know?" for every number |
| P3 Investigation Lead | Non-technical case officer | Defensible narrative timeline; prevented from treating ML hits as identifications |
| P4 Tool Developer | Adds a plugin when a new recorder appears | New family in < 1 week, one new file + one registry line |
| P5 Court / Defence Expert | Adversarial reader who never runs the tool | Finds nothing undisclosed |

### 3.2 User journeys (doc 2 §4)

UJ1 seizure → first clip (headline: identify, E01 image, parse, set offset from
reference capture, motion-gate, export, report) · UJ2 deleted-footage recovery on a
24-day-old incident · UJ3 live device that can't be powered down (provenance class C) ·
UJ4 owner-provided USB `.dav` export (class D) · UJ5 three recorders with three wrong
clocks on one axis · UJ6 new OEM plugin via signature dossier.

### 3.3 Goals (doc 2 §2)

| # | Goal | Measured by |
|---|---|---|
| G1 | Eliminate multi-tool workflows | ~7 external tools → 0 |
| G2 | ≥ 6 families full-parse, ≥ 8 brands at carve level | Validation matrix |
| G3 | Recover what vendor tools can't | ≥ 30 % more recording-minutes over T1 |
| G4 | Defensible timestamps | 100 % carry method + uncertainty; 0 fabricated |
| G5 | Cut analysis time | ≥ 80 % fewer operator hours to first relevant clip |
| G6 | Legally usable output | Report + s. 63(4) certificate pass examiner/legal dry run |
| G7 | Provable integrity | Third party verifies hash chain from the report alone |

### 3.4 Non-goals (doc 1 §8) — reject scope creep into these

| Non-goal | Why |
|---|---|
| Face **recognition** against watchlists / identity databases | Accuracy, privacy, admissibility — detection + clustering only |
| Video enhancement (super-resolution, deblurring) | Different discipline; generative enhancement is evidentially dangerous |
| Pixel-level deepfake detection | Separate research problem. *Structural* tamper signs (gaps, index inconsistency, container re-encode traces) **are** in scope |
| Physical platter recovery | Cleanroom hardware; SPECTRA consumes the resulting image |
| Firmware exploitation / password bypass | Legally fraught; not needed when you have the disk |
| Live monitoring / VMS features | Different product |
| Cloud storage, cloud analytics, telemetry | Deliberate architectural exclusion |
| Defeating real vendor encryption | Detect and report honestly |

---

## 4. Sources of truth

### 4.1 The docs

| Doc | What it is | Read it when |
|---|---|---|
| [README.md](README.md) | One-paragraph pitch, doc index, target layout | Orientation |
| [01 Problem analysis](docs/01-problem-analysis.md) | Domain writeup | *Why* anything is designed as it is. §3 storage anatomy · §4 nine challenges → design responses · §5 legal framework · §6 state of the art · §7 approach + principles · §8 non-goals · §9 risks · §10 glossary |
| [02 PRD](docs/02-prd.md) | Requirements | Requirement IDs and priorities. §3 personas · §4 journeys · §5 FR-01..FR-97 · §6 NFR-01..16 · §7 AC-01..13 · §8 milestones M0–M10 · §9 dependencies · §10 open questions Q1–Q5 · §11 traceability |
| [03 Architecture](docs/03-architecture.md) | System design | Implementing anything. §1 drivers D1–D5 · §2 layers · §3 foundation · §4 plugin contract · §5 acquisition · §6 parsing + coverage · §7 recovery · §8 timeline · §9 analytics · §10 reporting · §11 SQL schema · §12 sequence diagrams · §13 stack · §14 security · §15 directories · §16 decisions |
| [04 OEM analysis](docs/04-oem-comparative-analysis.md) | Per-vendor format knowledge (named deliverable) | Writing a parser. §0 confidence labels · §2 master matrix · §3–§8 per family · §9 cross-cutting comparison · §10 coverage strategy · §11 RE procedure · §12 claims needing hardware · §13 sources/method statement |
| [05 SOPs](docs/05-sop.md) | Field + lab procedures (named deliverable) | Touching seizure, acquisition, examination, reporting. SOP-0..7, Forms F-1/F-2/F-3 |
| [06 Validation plan](docs/06-validation-plan.md) | NIST-CFTT-style test plan (named deliverable) | Writing tests. §3 corpus · §4 test cases · §6 release gate · §7 report template · §8 known limitations |
| [07 User manual](docs/07-user-manual.md) | Install, GUI walkthrough, CLI (named deliverable) | UX and command surface. §3 concepts · §4 GUI · §5 CLI · §6 troubleshooting |
| [08 Demo & deliverables](docs/08-demo-and-deliverables.md) | Deliverable tracker, build order, team split, demo | §1 deliverables · §2 build order · **§3 team split (mirrors §12 here)** · §4 six-minute demo · §5 done criteria · §6 honest risk callout |

**Named deliverables of the problem statement (doc 8 §1):** OEM comparative analysis
(doc 4) · forensic image (Tier S/R corpus) · architecture docs (doc 3) · functional
prototype (`spectra/`) · SOPs (doc 5) · validation reports (doc 6) · user manuals (doc 7) ·
final project report (assembled at submission).

### 4.2 The WhatsApp planning diagrams (superseded)

Two images dated 2026-09-10 — **"Tech Flow"** (six pipeline stages) and **"Work Split —
6 People"** — were reviewed on 2026-09-15. The team chose the docs over them. §17 lists
the useful corrections carried over and the ideas deliberately *not* adopted.

**Their P-numbers mean something different.** In "Tech Flow", P1/P2/P3 were priority
tiers. In "Work Split", P1–P6 were people with different scopes than today's sections.
If someone says "P3" and means the diagram, translate:

| Diagram section | Diagram scope | Where that work lives now |
|---|---|---|
| P1 Foundation & app shell | I/O layer, pyewf shim, audit log, case store, desktop shell | **P1** (desktop shell → **P5**) |
| P2 Acquisition & imaging | Imaging, hashing, write-blocker workflow, live acquisition, synthetic images | **P4** (synthetic images → **P6**) |
| P3 Identification & parser core | Signature table, DHFS/Dahua parse, degrade-to-carve | **P2** |
| P4 Hikvision & recovery | HIKBTREE parser, T1–T3, carving, coverage map | **P3** |
| P5 Extraction, time & timeline | Remux export, time model, timeline, correlation | Time/timeline → **P4**; remux (`MediaTool`) → **P1** |
| P6 Report, legal & deliverables | Report engine, certificate, custody export | **P5** |
| Analytics (P2 + P6) | Motion → objects → faces → ANPR | **P6** |
| Breadth (P3 + P4) | WFS, Honeywell, Uniview/Matrix/VIGI | VIGI/generic FS → **P2**; Uniview/Matrix → **P3** |

---

## 5. Non-negotiable rules (and why)

From doc 1 §7.1 and doc 3 §1. Any code or doc change that breaks one is wrong, however
convenient.

| # | Rule | Why |
|---|---|---|
| 1 | **Read-only by construction (D1).** Evidence is read only through `EvidenceSource`, which has no write method. No `open()` on an evidence path anywhere else. Proven by strace/Procmon showing zero write syscalls (NFR-11, AC-03) | One write to evidence and the case is compromised; "we don't write" must be structural and provable, not a promise |
| 2 | **Never fabricate.** Unknown time → "time unknown"; unparsed region → reported; no offset evidence → `ReferenceTime.utc = None` and no absolute time anywhere (FR-53); a carved item with no header time gets physical order, never an inferred time (FR-45) | A blank in a forensic report is a finding; a guess is a liability. An unexplained timestamp is the easiest attack on CCTV evidence |
| 3 | **Format family, not brand.** Parser chosen from disk bytes only; brand inferred and displayed separately (FR-06) | Rebrands are most of the market; brand-keyed parsers break on every one |
| 4 | **Remux, never transcode, for the evidence copy** (`-c copy`); transcodes only as labelled derivatives (FR-31, FR-36) | A transcoded file is not the evidence, and presenting it as such loses the case |
| 5 | **Everything hashed, everything logged** in one append-only hash-chained audit log, written synchronously before *and* after every state-changing operation (D3, FR-71) | Chain of custody must be cryptographic, not paper; a crash leaves a visible "started" record, not a silent hole |
| 6 | **Offline always (D5).** No network egress during analysis, enforced by process isolation (FR-95). No cloud SDKs, telemetry, services | Evidence handling and NTRO context; "cannot" beats "does not" |
| 7 | **Degrade loudly.** Unknown layout version → `parse_supported=False` → carving mode, and say so (FR-03) | Silent mis-parsing produces a confident wrong timeline a lab will sign |
| 8 | **The plugin is the product (D2).** New family = one plugin file + one registry line + one fixture; zero core changes (FR-20, AC-12) | Vendor-agnostic is the claim; core must know nothing about vendors |
| 9 | **Extents, not buffers (D4).** `Recording` holds byte ranges; `Frame.payload` is a `memoryview`; ≤ 4 GB RSS on any image (NFR-04) | 1–48 TB images, 30 GB recordings, 8 GB machines |
| 10 | **Surface ambiguity.** Multiple probe matches → all candidates shown with confidence and matched bytes; operator choice is audited | Silent resolution hides the one decision a defence expert will question |
| 11 | **Negative findings are generated and non-deletable** — examiner may add, never remove (FR-81) | The tool knows what it failed to read; a report listing only successes isn't forensic |
| 12 | **Analytics are leads, never identifications.** No watchlist, identity DB, or face recognition. Every hit carries model name, model SHA-256, confidence, fixed disclaimer (FR-92, FR-96) | Accuracy and privacy attacks; the value is review reduction, which is uncontroversial |
| 13 | **Determinism.** Same image + version + parameters ⇒ byte-identical artefacts and identical `findings.json` (NFR-08, AC-11). Sort everything; UTC; fixed locale; times from audit records, not `now()` | Two examiners must get the same answer, or the tool is attackable |
| 14 | **Nothing unverified is presented as fact.** Doc 4 claims keep [C]/[R]/[H]/[U] labels until a fixture proves them | Vendor formats are undocumented; overclaiming is the failure mode this project exists to prevent |
| 15 | **No real case data, seized-device images, or vendor firmware in the repo. Ever.** | Legal handling, privacy, vendor IP (doc 4 §13, doc 6 §5) |

**A confident wrong answer is the only truly disqualifying failure mode** (doc 6 §1). When
in doubt, refuse correctly rather than answer plausibly.

---

## 6. Architecture

Summarised from doc 3. Go there for code-level detail and sequence diagrams.

### 6.1 Drivers (doc 3 §1)

| # | Driver | Structural consequence |
|---|---|---|
| D1 | Evidence must never be written to | Single read-only I/O layer every module goes through |
| D2 | New vendors must not require core changes | Plugin registry with a narrow, versioned contract |
| D3 | Everything provable after the fact | One append-only hash-chained audit log, written synchronously |
| D4 | Images 1–48 TB, RAM 8 GB | Streaming / `mmap`; extents, not buffers |
| D5 | No network during analysis | No services or daemons; desktop app + local job runner; bundled models |

### 6.2 Layers

```
PRESENTATION   desktop/ (Electron)  ──runs──▶  spectra.cli (Typer)
               the app runs `spectra … --json` for every action — no logic in the UI
SERVICES       CaseService · IdentifyService · AcquireService · ParseService ·
               RecoverService · TimelineService · AnalyticsService · ReportService
               each a thin orchestrator: validate → audit start → run job → audit complete
DOMAIN         identify (signature scanner) · acquire (imager, verifier) ·
               parse (plugin dispatch → REGISTRY) · recover (T1–T4) · timeline · ml
FOUNDATION     EvidenceSource · Hasher · AuditChain · CaseStore · JobRunner · MediaTool · Provenance
```

Every capability must be reachable from the CLI — validation (doc 6) runs headless.

### 6.3 Foundation (`spectra/core/`)

| Component | What it does |
|---|---|
| `EvidenceSource` (`source.py`) | The only way to read evidence. `size`, `identity` (serial, model, capacity, image format, hashes), `read(offset, length)`, `map(offset, length) -> memoryview`, `readable_ranges()` (excludes known-bad sectors). **No write method.** Impls: `RawDeviceSource` (`/dev/sdX` or `\\.\PhysicalDriveN`, O_RDONLY / FILE_SHARE_READ, Linux BLKROSET set and logged) · `RawImageSource` (`.dd`/`.img`, split `.001…`) · `EwfImageSource` (E01/Ex01 via pyewf) · `Aff4ImageSource` (priority P2) · `FileSetSource` (loose vendor exports as one addressable set). Failed read → retry per policy → record in bad-sector map → zero-fill **with the gap recorded** so parsers know it's a hole, not real zeros |
| `Hasher` (`hashing.py`) | MD5 + SHA-256 in one pass. SHA-256 is the integrity assertion; MD5 exists for legacy records and the certificate format, and the report says so in words. Hash points: source device, acquired image, each extracted artefact, each generated report |
| `AuditChain` (`audit.py`) | Append-only, hash-linked. `AuditRecord{seq, ts_utc (RFC 3339 UTC), operator, action ("acquire.start", "export.complete"…), target, params (canonicalised), result: ok/error/started, hash_before, hash_after, prev_digest}`; `digest = sha256(canonical_json(record))`. Canonical JSON: sorted keys, no whitespace, UTF-8, fixed float representation. `verify()` walks from seq 0 and locates the exact break. Stored in SQLite `audit` table with UPDATE/DELETE triggers (stops accidents; the hash chain stops determined parties) |
| `CaseStore` (`casestore.py`) | One portable directory per case: `case.db` (SQLite WAL) · `artifacts/ab/cd/<sha256>` content-addressed store · `images/` · `logs/` (tool logs, FFmpeg + protocol transcripts) · `case.manifest.json` (head digest, tool versions, integrity summary) |
| `JobRunner` (`jobs.py`) | Long operations (acquire, carve, analytics) as checkpointed jobs in worker **processes** (carve is CPU-bound; the GIL is real). Checkpoints every N s / M bytes; a killed 90 % carve resumes at 90 % (NFR-09) |
| `MediaTool` (`media.py`) | Pinned bundled FFmpeg as a subprocess. `remux()` = `-c copy` **evidence copy**; `derive()` = re-encode **derivative**, marked as such everywhere. Every invocation's command line + stderr to `logs/`, hashed; FFmpeg version recorded (FR-84) |
| `models.py` | Shared types: `Extent`, `Recording`, `Frame`, `DeviceTime`, `ReferenceTime`, `ProbeResult`, `DiskLayout` |

### 6.4 Plugin contract (`spectra/plugins/base.py`, doc 3 §4)

```python
class VendorPlugin(Protocol):
    family: str                  # "dahua", "hikvision", ...
    layout_versions: list[str]   # on-disk format versions this plugin understands
    plugin_version: str          # recorded in the report

    @classmethod
    def probe(cls, src) -> ProbeResult | None:
        """Q1. Bounded reads at known offsets. Returns family, layout_version,
        confidence 0..1, exact matched bytes + offsets, parse_supported.
        Unknown layout version → confidence set but parse_supported=False."""
    def superblock(self, src) -> DiskLayout:
        """Q2. block_size, block_count, index extents, log extents, format time."""
    def enumerate(self, src, layout, include_orphans: bool) -> Iterator[Recording]:
        """Q3. T1 when include_orphans=False; T1+T2 when True. Streams."""
    def frames(self, src, extent) -> Iterator[Frame]:
        """Q4. Parse vendor container; payload is a memoryview, not a copy."""
    def carve_signatures(self) -> list[Signature]:
        """T3. Byte patterns + validator callback that rejects false positives."""
    def decode_time(self, raw, layout) -> DeviceTime:
        """Vendor encoding → t_device. Known-good vectors per layout version."""
    # optional
    def system_log(self, src, layout) -> Iterator[DeviceEvent]: ...   # FR-07
    def firmware_facts(self, fw) -> FirmwareFacts: ...                # FR-05
```

Normalised, vendor-independent types (FR-28):

```python
Recording{channel, stream: main|sub, t_start, t_end: DeviceTime, extents: list[Extent],
          codec: h264|h265|mjpeg|unknown, resolution, fps, size_bytes,
          recovery_tier: T1|T2|T3|T4, confidence: 0..1, source_note}
Frame{kind: I|P|B|audio|unknown, channel | None, t_device | None,
      payload: memoryview, codec_hint}
```

**Dispatch** (`select_plugin`): probe every registered plugin → none match → generic /
"unknown" → carving; several match → pick highest confidence **and**
`audit("identify.ambiguous", candidates)`; operator can override, and the override is an
audit record.

**Adding a family (AC-12):** `tools/dump_signature.py IMAGE` → identify superblock and
index → write `plugins/<family>.py` → fixture + `ground_truth.json` in `tests/corpus/` →
one `REGISTRY` line → `pytest tests/test_plugins.py -k <family>` (generic conformance
suite runs against every plugin). No core file edited; verified by diff scope.

### 6.5 Parsing pipeline and coverage map (doc 3 §6)

```
EvidenceSource → IdentifyEngine → ProbeResult
   parse_supported? no → carving mode only
   yes → plugin.superblock() → DiskLayout
       → plugin.enumerate() → Recording[] (T1, +T2) → case.db
       → CoverageMap
       → on export: plugin.frames() → ES writer → Hasher → artifacts/
                    → MediaTool.remux(-c copy) → Hasher → artifacts/, t_device as PTS
```

**Coverage map (FR-29)** — every byte of the image in exactly one bucket:

| Bucket | Meaning |
|---|---|
| `parsed` | Claimed by a T1/T2 recording |
| `carved` | Recovered at T3/T4 |
| `structural` | Superblock, index, logs — understood metadata |
| `unreadable` | Bad sectors |
| `unaccounted` | **Bytes the tool could not explain.** High entropy here = data not recovered → negative finding |

Precedence where tiers overlap is a team contract (§14).

### 6.6 Recovery engine (doc 1 §4.4, doc 3 §7)

| Tier | Method | Recovers | Key detail |
|---|---|---|---|
| **T1** | Walk the valid index | What the vendor tool would show | Baseline |
| **T2** | Orphan index entries (in-use flag clear) | Recently deleted, full metadata | **Validate before trusting:** block begins with family magic, frame times inside the entry's window, frame channel matches. Any failure → downgrade to carve candidate. Stops stale entries pointing at overwritten blocks |
| **T3** | Signature carve over the whole image | Index gone, partially overwritten blocks, damaged disks | 64 MB chunks with `MAX_FRAME_HEADER` overlap (stops boundary misses); validator rejects false positives; walk the frame chain to a discontinuity; trim to first I-frame; channel/time from headers where present |
| **T4** | Bad-sector tolerant T3 | Failing disks | Uses `readable_ranges()`; each survivable span is its own clip; gap positions recorded |

Merge + dedup by extent overlap + content hash; keep the highest tier; record that
duplicates existed (FR-44).

**T3 confidence:** 0.9 headers with channel + time, ≥ 2 consecutive GOPs, monotonic ·
0.7 time but no channel · **0.4 valid GOPs, no time → presented as "time unknown"** ·
0.2 single fragment, decodes, no chain.

### 6.7 Time model (doc 1 §4.5, doc 3 §8)

```
t_device     raw value exactly as stored + its encoding name   (never discarded, always shown)
   │ + tz_offset             (firmware config or operator-supplied)
t_local      wall-clock time as the device believed it
   │ + clock_offset ± uncertainty   (measured, never guessed)
t_reference  normalised UTC — the ONLY time used for correlation; may legitimately be None
```

```python
DeviceTime{raw: int | bytes, encoding: "unix_le32" | "packed_bcd_v2" | ..., local: datetime | None}
ReferenceTime{utc: datetime | None, uncertainty_s (always present),
              method: A_ntp | B_reference_capture | C_external_event | D_live_rtc | none,
              tz_offset_s, clock_offset_s, derivation_note (printed verbatim in report)}
```

| Rank | Offset method | Typical uncertainty |
|---|---|---|
| A | NTP synchronisation proved from firmware config + system log | ±1 s |
| B | **Reference-clock capture** at seizure — GPS/NTP clock held in front of a live camera (SOP-1 Step 6); examiner enters true time and marks the frame | ±1 s |
| C | Externally timestamped event in frame (card swipe, POS, call) | ±2–60 s |
| D | Device RTC read from live UI at a noted instant before power-down | ±1 s at that instant, extrapolated |
| none | No evidence | `utc=None`; device-local only, "absolute time not established" banner, no absolute time anywhere |

- **Uncertainty propagates:** ±1 s and ±30 s devices → ±31 s on relative ordering. Don't
  assert an ordering that isn't safe at the stated uncertainty.
- **Manual clock change** in the device log ⇒ per-segment offsets, never one global offset.
- **Correlation (FR-57..FR-62):** N lanes on one `t_reference` axis; gap analysis treats
  absence as a finding (synchronised gap across all channels = power event or deliberate
  interruption); synchronised playback seeks by `t_reference` with offsets applied; entity
  threading clusters detections into ranked, labelled leads.

### 6.8 Acquisition (doc 3 §5)

- **Pre-flight:** write-blocker verified **by capability** (a harmless write attempt must
  be rejected with `EROFS`/`ACCESS_DENIED`, plus blocker ID where exposed) · HPA/DCO
  detection with hidden-sector counts (vendors may keep a backup superblock there) ·
  SMART + drive identity. Provably writable → refuse, unless an explicit logged override
  that downgrades provenance A → B.
- **Imaging loop:** 1 MB chunks, checkpointed, resumable; read error → retry → log LBA →
  zero-fill; bad-sector map persisted and reported. Never abandon a failing disk — a
  partial image with a documented map is evidence.
- **Provenance classes (FR-19)**, printed in the report and immutable after acquisition:

| Class | Meaning | Report language |
|---|---|---|
| A | Physical image, hardware write-blocked | Strongest; bit-for-bit, verifiably unaltered |
| B | Physical image, other protection, justified | Protection method and justification recorded |
| C | Live logical acquisition | Source could not be independently verified |
| D | Third-party export files | Original storage not examined; completeness unverifiable |

- **Live logical (FR-16):** vendor retrieval interface or ONVIF Profile G, streamed while
  hashing, full protocol transcript to `logs/`. Never issues configuration changes, PTZ
  commands, or deletes.

### 6.9 Analytics (doc 3 §9)

```
Recording (read-only) → decode to frames in memory only
  Stage 1  MOTION   OpenCV frame differencing + region mask → activity segments
                    (always first; removes 90–99 % of compute; the 80 % time saving)
  Stage 2  OBJECTS  ONNX Runtime, pinned model: person / vehicle / two-wheeler / bag
                    (motion segments only)
  Stage 3  FACE detect + cluster · ANPR (Indian plates) · ReID    (no watchlist, no identity DB)
  → Annotations {frame_no, t_reference, bbox, label, score, model_name, model_sha256,
                 disclaimer} stored ALONGSIDE evidence, never inside it (FR-97)
```

- **No egress:** analytics worker runs without networking (Linux network namespace with
  loopback only; Windows per-process firewall rule).
- **Pinned models:** model SHA-256 in the case DB and report — a 2026 result reproducible
  in 2031.
- **Fixed disclaimer** rendered verbatim next to every hit: *"Machine-generated detection.
  Confidence <s>. Requires human verification against the source frame. Not an
  identification."*

### 6.10 Reporting (doc 1 §4.8, doc 3 §10)

`case.db` + artefacts + audit chain → deterministic `findings.json` (FR-86) → Jinja2 →
HTML → WeasyPrint → **PDF/A**. Twelve sections:

1. Case and authority, examiner identity and qualification
2. Evidence received — device and disk make/model/serial/capacity
3. Acquisition — method, tool version, write blocker, HPA/DCO, hashes
4. Identification — how the family was determined, **raw signature bytes shown**
5. Filesystem findings — layout, block size, blocks, format date
6. Recordings — by channel and time, recovery tier per item
7. **Negative findings** — generated from coverage `unaccounted`, bad-sector map, unparsed
   structure versions, empty channels, timeline gaps. Examiner can add, not delete
8. Timestamp analysis — model, method, uncertainty, derivation note
9. Timeline and correlation
10. Analytics — every hit with model hash + disclaimer, labelled machine-generated leads
11. Integrity — full hash table, audit head digest, commands a third party runs to verify
12. Appendices — SOP and validation references, glossary, examiner's declaration,
    **Annexure: BSA s. 63(4) certificate**

Determinism: sort everything, fixed locale, UTC, analysis timestamps from audit records.

### 6.11 Data model (doc 3 §11)

| Table | Holds |
|---|---|
| `case_meta` | Case ID, title, agency, FIR/authority refs, examiner name/designation/s. 79A ref |
| `evidence` | Kind (disk/image/export_files/firmware/document), **provenance class**, device + disk identity, HPA/DCO, MD5/SHA-256, acquisition time/by, write blocker |
| `identification` | Family, layout version, confidence, parse_supported, matched signature + offsets, all candidates JSON, inferred brand + evidence |
| `disk_layout` | Block size/count/used, index + log extents, format time |
| `recording` | Normalised recording: channel (+ name from firmware), stream, codec, resolution, fps, t_device raw/encoding, t_local and t_ref start/end, uncertainty, method, extents, size, tier, confidence, source note |
| `artifact` | Content-addressed exports (SHA-256 PK, MD5, kind, recording, is_derivative, tool versions) |
| `coverage` | (offset, length, bucket) per evidence item |
| `device_event` | Device system log events with t_device / t_ref |
| `annotation` | Analytics hits and human bookmarks with model name/hash and score |
| `custody` | Physical custody transfers (from, to, purpose, signature ref) |
| `audit` | Append-only hash-chained audit log; UPDATE/DELETE triggers raise |

### 6.12 Target directory layout (doc 3 §15)

```
spectra/
├── core/        source.py · hashing.py · audit.py · casestore.py · jobs.py · media.py · models.py
├── identify/    engine.py (probe dispatch, ambiguity) · dossier.py (FR-09)
├── acquire/     imager.py · blocker.py · hpa_dco.py · live.py (FR-16)
├── plugins/     base.py (THE CONTRACT) · registry.py · dahua.py · hikvision.py · uniview.py ·
│                tplink_vigi.py · matrix.py · honeywell.py · generic_fs.py
├── recover/     orphans.py (T2) · carver.py (T3) · gop.py · merge.py
├── timeline/    timemodel.py · offset.py (methods A–D) · correlate.py · gaps.py
├── ml/          motion.py · objects.py · faces.py · sandbox.py (FR-95)
├── report/      generator.py · findings.py · negative.py · templates/ (report, bsa_63_4_certificate)
├── services/    thin orchestrators — the API for UI and CLI
└── cli.py       Typer — also the desktop app's only interface to the engine
desktop/         Electron + React shell (§17 item 26): electron/ main process · src/ screens
tools/           dump_signature.py (FR-09) · make_corpus.py (synthetic ground-truth images)
tests/           corpus/ · test_plugins.py (conformance) · test_timestamps.py · test_audit.py
```

### 6.13 Security and deployment (doc 3 §14)

Single desktop install; no server, daemon, or listening socket. Raw device read needs
elevation; privileges drop right after opening the handle. Optional AES-256-GCM case
container encryption. Roles: `examiner` (full) · `reviewer` (read + annotate, no export) ·
`readonly`; every media access audited. Supply chain: pinned dependency hashes, SBOM,
reproducible builds where possible. **Threat model:** not a network attacker — (a) an
examiner's undetected mistake → read-only layer + coverage map; (b) post-hoc alteration →
hash chain; (c) defence expert hunting undisclosed gaps → mandatory negative findings.

### 6.14 Stack (doc 3 §13)

Python 3.11+ · `struct`/`mmap` · libewf/pyewf · `hashlib` · pinned bundled FFmpeg ·
SQLite WAL · ONNX Runtime + OpenCV · Electron + React (desktop) · Typer · Jinja2 + WeasyPrint (PDF/A) ·
PyInstaller + offline wheelhouse · pytest + synthetic corpus. Drop to Rust/Cython only if
the T3 carve profiles badly.

### 6.15 Design decisions worth defending (doc 3 §16)

Family keys the parser, brand is cosmetic · extents not buffers · remux for evidence,
transcode only as labelled derivative · `ReferenceTime.utc` may be `None` · negative
findings auto-generated and non-deletable · analytics in a network-isolated process ·
desktop app, no server · SQLite per case, no central DB · MD5 computed but SHA-256
asserted.

---

## 7. Format families

From doc 4. **Every structural claim has a confidence label — keep it when quoting.**

| Label | Meaning | Implementation rule |
|---|---|---|
| **[C]** Confirmed | Peer-reviewed literature and/or verified by the team on a real image | Safe to implement |
| **[R]** Reported | Consistent public sources, not yet team-verified | Implement behind a probe that fails loudly if wrong |
| **[H]** Hypothesis | Inferred from ODM relationships / partial observation | Verify before implementing |
| **[U]** Unknown | No reliable information | Doc 4 §11 reverse-engineering procedure |

### 7.1 Family matrix (doc 4 §2)

| | Dahua family | Hikvision family | TP-Link VIGI | Uniview | Matrix SATATYA | Honeywell native |
|---|---|---|---|---|---|---|
| **Brands** | Dahua [C], CP Plus [R], some Godrej/Honeywell [H] | Hikvision [C], some Godrej/ODM [H] | VIGI | UNV | Matrix (Vadodara, Indian) | non-Dahua Honeywell SKUs |
| **On-disk FS** | Proprietary raw block device [C] | Proprietary, HIKBTREE [C] | ext4 + files [R] | Proprietary [R] | [U] | some ext3-based [H] |
| **Identification** | Superblock magic [R] + `DHAV`/`dhav` fallback | `HIKVISION@HANGZHOU` master sector [C] — near-zero false positives | Standard FS superblock + naming [R] | Vendor magic [H] | [U] | [U] |
| **Index** | Block-index table, duplicated [R] | HIKBTREE page-list + pages, **two copies** [C] | FS directory [R] | Index table [H] | [U] | [U] |
| **Block size** | Large fixed [R] | Commonly 256 MB [R] | n/a | Fixed [H] | [U] | [U] |
| **Container** | `DHAV`…`dhav` per frame [C]; field offsets [R] | MPEG-PS-like `00 00 01 BA` + private headers [C] | MP4/fMP4 [R] | Vendor [H] | [U] | [U] |
| **Per-frame time/channel** | **Yes** [C] | Partly — private headers carry time [R] | File-level | [U] | [U] | [U] |
| **Timestamp storage** | Packed date-time in frame header [R] | Epoch-style in index entries [R] | MP4 + FS mtime [R] | [U] | [U] | [U] |
| **T3 carve feasibility** | **Very high** (self-validating length + end magic) | High | Medium (`ftyp`/`moof`) | Medium | Medium (raw start codes) | Medium |
| **Difficulty** | Low–Medium | Medium | **Low** | Medium–High | High | High |
| **Build priority** | **1** | **2** | 3 | 4 | 5 | 6 |

**Why Dahua first:** its `DHAV` parser serves the disk path, `.dav` export files, and T3
carving — one parser, three features. **Why Hikvision second:** best publicly documented,
lowest-risk proof that the plugin contract is really vendor-agnostic. **Matrix:** Indian
product in an Indian government problem statement, least likely covered by foreign tools —
the most defensible novelty claim; **order hardware first** even though it's built late.

### 7.2 Family-specific notes

- **Dahua:** probe `DHAV`/`dhav` in the data area as a fallback so a damaged or
  unknown-version superblock is still carvable; version layout descriptors; detect Smart
  H.264/H.265 from SPS/SEI and warn if FFmpeg can't decode cleanly (FR-35).
- **Hikvision:** read master sector, then **both** HIKBTREE copies; report which was used
  and any divergence (a finding); validate orphans against block content (256 MB blocks
  make stale entries dangerous); some carved items legitimately end up "channel unknown";
  H.264+/H.265+ are decode hazards.
- **TP-Link VIGI:** `generic_fs` + light naming/index reader; deleted recovery is ordinary
  FS forensics (ext4 journal, inodes, MP4 box carving); MP4 time vs FS mtime disagreement
  is itself a finding.
- **Uniview:** exports are `.mp4`/`.ts`, so class D ingest delivers value before the disk
  parser exists; ship carve-only first.
- **Matrix / Honeywell native:** carve-only unless reverse engineering completes.

### 7.3 Export files (provenance class D, very common)

Dahua `.dav` (same DHAV framing as disk — same parser) · Hikvision `.mp4`/`.264`,
sometimes a self-extracting player · Uniview `.mp4`/`.ts` · VIGI `.mp4` · Matrix/Honeywell
vendor formats [U].

### 7.4 Coverage strategy (doc 4 §10)

| Milestone | Family | Level |
|---|---|---|
| M3 | Dahua family (Dahua, CP Plus, Dahua-ODM Godrej/Honeywell) | Full parse + T1–T3 |
| M4 | Hikvision family | Full parse + T1–T3 |
| M9a | TP-Link VIGI via generic FS | Full parse |
| M9b | Uniview | Parse if RE completes, else carve-only |
| M9c | Matrix SATATYA | Parse if RE completes, else carve-only |
| M9d | Honeywell native | Carve-only |

Support level is stated **per family** in the tool and the report. **Never claim "supports
8 vendors"** when some are carve-only.

### 7.5 Reverse-engineering procedure for an unknown format (doc 4 §11)

0 get hardware (weeks of lead time — start now) → 1 format a zeroed disk and image it
(the empty reference: every non-zero byte is structure) → 2 controlled recordings with a
GPS clock in frame and distinct scenes per channel → 3 **differential imaging** (the bytes
that change when you add one recording *are* its index entry — highest-yield step) → 4
locate timestamps (a decoder that explains 12 recordings is a decoder; one is a
coincidence) → 5 map the data area (magics, start codes, periodicity) → 6 validate by
extraction against ground truth → 7 add fixture + `ground_truth.json` → 8 force overwrite
and diff to find the in-use flag (the basis of T2). All analysis is clean-room on
team-owned hardware; no firmware decompiled or redistributed (doc 4 §13).

### 7.6 Hardware verification tracker (doc 4 §12)

Rows 1–6 unlock the market (Hikvision master-sector offsets, HIKBTREE entry layout,
Dahua superblock, DHAV field offsets, Dahua timestamp packing, CP Plus ≡ Dahua); rows
7–8 map Godrej/Honeywell SKUs; rows 9–12 are Uniview, VIGI, Matrix, Honeywell native.
**The single highest-leverage action for the project is acquiring hardware.**

---

## 8. Legal and procedural framework

From doc 1 §5 and doc 5. These are design constraints, not background reading.

### 8.1 What each provision means for the product

| Provision | What it says (as the docs use it) | Product consequence |
|---|---|---|
| **BSA 2023 s. 61** | Electronic records not denied admissibility merely for being electronic | — |
| **BSA 2023 s. 63** (successor to IEA s. 65B) | Electronic records admissible subject to s. 63(2) conditions and a **s. 63(4) certificate** identifying the record, describing its production, giving device particulars, signed by the person in charge of the device and an expert; schedule format includes **hash values** | Auto-filled s. 63(4) certificate annexure with hashes (FR-83); the reason MD5 is computed alongside SHA-256 |
| **BSA 2023 s. 39(2)** | Expert opinion on electronic evidence | Examiner's declaration in report appendices |
| **BNSS 2023 s. 105** | Mandatory audio-video recording of search and seizure | Case accepts and hashes the seizure video and panchnama as attachments (FR-74) — custody chain starts at the scene |
| **BNSS 2023 s. 176(3)** | Forensic expert attendance at scene for offences punishable by ≥ 7 years | SOP-1 staffing |
| **IT Act 2000 s. 79A** | Notified Examiners of Electronic Evidence | Report carries examiner identity, designation, notification reference |
| ***Arjun Panditrao Khotkar v. Kailash Kushanrao Gorantyal* (SC, 2020)** | Certificate (then s. 65B) is mandatory; missing ones have sunk cases | Automating the certificate is real user value |
| **DPDP Act 2023** (s. 17 exemptions) | Personal data in footage; law-enforcement exemptions | Conservative posture anyway: local-only, no telemetry, encryption at rest, RBAC, media access audited |
| CCTNS / CFSL / NCRB guidance | Procedural guidance on digital evidence | Reflected in SOPs |

Validation report (doc 6) exists so the examiner can answer *"has your tool been tested?"*
— a question that has ended cross-examinations.

### 8.2 SOP essentials the software must support (doc 5)

- **SOP-0 governing principles:** don't alter the original · record everything, including
  what you didn't do · prefer the disk to the device · **establish the clock before power
  down**.
- **SOP-1 field seizure:** start s. 105 recording first · photograph in place, **rear
  cabling with ports** (channel mapping) · interview custodian, get admin password ·
  photograph UI time, NTP status, timezone · ⭐ **reference-clock capture: GPS/NTP phone
  held ≥ 30 s in front of a camera, true start/end times on Form F-1** · graceful shutdown
  preferred, method recorded · seal, F-2 custody entry, hand F-1/F-2/memo/video over with
  the device.
- **SOP-2 lab physical acquisition:** verify seal · create case, attach and hash scene
  documents · photograph internals and **multi-disk bay order** · dump firmware flash if
  possible · hardware write blocker, tool pre-flight, stop if unverified · HPA/DCO recorded,
  removed only with authorisation · image, verify hashes, record on F-3 · re-seal original;
  all further work on the image.
- **SOP-3 live logical** (class C) · **SOP-4 third-party exports** (class D; image the
  whole USB stick, recommend seizing the source) · **SOP-5 examination:** identify → set
  time model *before* analysis → parse, read coverage map and device log → recover T2, T3,
  T4 → timeline gaps first → motion then objects → verify every hit visually → export
  evidence copy + manifest · **SOP-6 reporting:** read negative findings, verify hashes and
  head digest, complete certificate, confirm versions/model hashes, sign, archive, release
  report + clips + manifest together.
- **Forms:** F-1 seizure record · F-2 chain of custody · F-3 acquisition record.

---

## 9. Quality bar — NFRs, acceptance criteria, validation

### 9.1 Non-functional requirements (doc 2 §6)

| ID | Requirement | Target |
|---|---|---|
| NFR-01 | Acquisition throughput | ≥ 80 % of source sustained read rate (typ. ≥ 120 MB/s SATA HDD) |
| NFR-02 | Index parse time | ≤ 5 min for a fully populated 12 TB disk |
| NFR-03 | T3 carve throughput | ≥ 300 MB/s on NVMe working storage, multi-worker |
| NFR-04 | Memory ceiling | ≤ 4 GB RSS regardless of image size |
| NFR-05 | Minimum host | 4-core x86-64, 8 GB RAM, no GPU (GPU optional, ≥ 4× analytics) |
| NFR-06 | Platforms | Linux primary, Windows 10/11 supported, macOS best-effort |
| NFR-07 | Offline | Fully functional with no network; offline installer |
| NFR-08 | Determinism | Byte-identical artefacts and identical findings JSON |
| NFR-09 | Resumability | Acquire, parse, carve, analytics all checkpoint and resume |
| NFR-10 | Auditability | 100 % of state-changing operations audited; O(n) offline verify |
| NFR-11 | Read-only guarantee | No write syscall against evidence, verified by strace/Procmon |
| NFR-12 | Test coverage | ≥ 80 % lines on parsing, timestamp, hashing, recovery; **100 % of timestamp decoders have known-good vectors** |
| NFR-13 | Localisation | English UI; report supports Hindi and regional agency blocks |
| NFR-14 | Accessibility | Keyboard navigable; no colour-only timeline encoding |
| NFR-15 | Installability | Single installer/binary; no privileged services beyond raw read |
| NFR-16 | Licensing | Permissive open source; all components licence-compatible, SBOM |

### 9.2 Acceptance criteria — the ship gate (doc 2 §7)

| AC | Criterion |
|---|---|
| AC-01 | Correct family for every corpus image; **zero false-confident misidentifications** ("unknown" passes, wrong family fails) |
| AC-02 | Image MD5/SHA-256 match independent `dd` + `sha256sum` on ≥ 3 disks |
| AC-03 | strace/Procmon: zero writes to evidence across a full workflow |
| AC-04 | ≥ 99 % of ground-truth recordings enumerated on Dahua and Hikvision corpus images |
| AC-05 | Exported evidence MP4's video ES bit-identical to the ES extracted from the image (see §17 item 5) |
| AC-06 | T2+T3 recover ≥ 30 % more recording-minutes than T1 on the deletion corpus |
| AC-07 | Every timestamp decoder passes vectors; known clock offset normalised within stated uncertainty |
| AC-08 | Audit-chain verification detects tampering in 100 % of injected trials |
| AC-09 | Report has all 12 sections, non-empty negative findings, completed s. 63(4) certificate |
| AC-10 | Analytics run with network down; egress attempt impossible/blocked |
| AC-11 | Two runs on the same image produce identical findings JSON |
| AC-12 | New plugin passes fixture test with no core file modified (diff scope) |
| AC-13 | 8-channel × 7-day benchmark, acquisition → report within one working day, ≥ 80 % less than manual |

**Release plan rule (doc 2 §8):** M7 (report) is the shippable line; if time compresses,
cut from M8/M9 — never from M0, M2, or M7.

### 9.3 Validation plan (doc 6)

**Philosophy:** requirements first, tests second (a test that traces to nothing is
deleted) · ground truth or it doesn't count ("the video played" is not a result) ·
publish the failures · negative testing is mandatory.

**Corpus:**

| Tier | What | Items |
|---|---|---|
| **S** synthetic (`tools/make_corpus.py`) | Small (100 MB–2 GB) images with fully known ground truth; live in repo, run in CI | S-01 Dahua 4 ch/24 h · S-02 30 % orphaned entries (T2 oracle) · S-03 20 % partial overwrite (T3 oracle) · S-04 Hikvision + both HIKBTREEs · S-05 primary HIKBTREE corrupt · S-06 2 % bad sectors (T4 oracle) · S-07 offset +00:17:42, UTC+05:30 (time oracle) · S-08 clock set back 2 h on day 3 · S-09 truncated at 60 % · S-10 Hikvision structures + Dahua magics (must report ambiguity) · S-11 empty disk · S-12 array member 2 of 3 |
| **R** real device (doc 4 §11 procedure) | Team-owned hardware, controlled recordings | R-01 Dahua fresh · R-02 R-01 filled to force overwrite · R-03 Hikvision · R-04 CP Plus (tests CP Plus ≡ Dahua) · R-05 VIGI · R-06 Uniview · R-07 Matrix · R-08 field-pulled unit (robustness only) |
| **E** export files | `.dav`/`.264`/`.mp4` from the units above | Validates class D; lets the `.dav` parser be tested before the disk parser exists |

Governance: every item has `ground_truth.json`; item hashes in repo; large real images
stored externally with CI **skip-with-notice — a skipped test is reported as skipped,
never passed.**

**Test case prefixes:** TC-ID identification · TC-AQ acquisition · TC-PS parsing/decoding ·
TC-RC recovery · TC-TS timestamps/timeline · TC-IN integrity/custody · TC-RP reporting ·
TC-ML analytics · TC-PF performance · **TC-RB robustness** (zero-length image, NTFS image
must not yield recordings, garbage index → carving, fuzzed headers, 4 GB length field,
cyclic index, 10 000 fuzz variants with zero crashes/hangs/confident-wrong — TC-RB-07 is
the headline gate).

**Environments:** CI (Tier S + unit + fuzz on every commit, minutes) · Linux lab bench
(Tier R, acquisition, write blocker, performance) · Windows lab bench (Procmon, E01
interop, packaging) · minimum-spec VM (NFR-05).

**Release gate (doc 6 §6):** all priority-P0 tests · AC-01..13 · zero false-confident IDs ·
TC-AQ-03 zero writes · TC-IN-01 100/100 · TC-RB-07 · TC-RP-06 determinism · TC-ML-01
isolation · all decoder vectors · known limitations documented **and surfaced in the
tool** · signed report. **Any P0 failure, and any confident-wrong failure at any
priority, blocks release.**

**Known limitations (doc 6 §8), each surfaced in the tool:** L1 Matrix/Honeywell native
carve-only · L2 unknown layout versions not parsed · L3 carved items without header time
have no absolute time · L4 codec variants may not decode · L5 live logical can't verify
source · L6 exports' completeness unverifiable · L7 analytics are leads · L8 encrypted
storage detected, not defeated.

---

## 10. Vocabulary, ID schemes, glossary

### 10.1 IDs that are easy to confuse

| Token | Meaning in this repo | Watch out |
|---|---|---|
| **P1–P6** | **Work sections / owners** (§12, doc 8 §3) | Doc 2 uses **P1–P5 for personas** and **P0/P1/P2 for requirement priority**; the WhatsApp diagrams used P-numbers differently again (§4.2). Write "priority P0" for priorities, "persona P2" for personas |
| Phase 0–10 | Build order (doc 8 §2) | Phase *n* ≡ milestone M*n* (doc 2 §8) |
| FR / NFR / AC | Functional req · non-functional req · acceptance criterion (doc 2) | Cite in code comments, tests, commits |
| G1–G7 | Product goals (doc 2 §2) | — |
| UJ1–UJ6 | User journeys (doc 2 §4) | — |
| D1–D5 | Architectural drivers (doc 3 §1) | — |
| R1–R8 | Risks (doc 1 §9) | Not Tier R corpus items R-01..R-08 |
| T1–T4 | Recovery tiers | — |
| Class A–D | Provenance classes | **Not** offset methods A–D |
| Method A–D | Clock-offset determination methods | **Not** provenance classes |
| [C]/[R]/[H]/[U] | Doc 4 confidence labels | Only [C] implementable without a loud-failing probe |
| S-xx / R-xx / E | Corpus tiers (doc 6 §3) | — |
| TC-xx-nn | Test cases (doc 6 §4) | — |
| L1–L8 | Known limitations (doc 6 §8) | — |
| SOP-0..7, F-1/2/3 | Procedures and forms (doc 5) | — |
| Q1–Q5 | Open questions (doc 2 §10) | — |

### 10.2 Glossary (doc 1 §10, extended)

| Term | Meaning |
|---|---|
| **DVR / NVR** | Digital Video Recorder (analogue cameras, encodes) / Network Video Recorder (IP cameras, muxes) |
| **OEM / ODM** | Brand selling the product / manufacturer who actually builds it — why brands share formats |
| **Format family** | Group of brands sharing one on-disk format; the unit a plugin implements |
| **Superblock / master sector** | Structure near LBA 0 describing disk geometry and where the index lives |
| **Index / HIKBTREE** | Table mapping data blocks to channel + time; HIKBTREE is Hikvision's |
| **Data block** | Fixed-size (64 MB–1 GB) allocation unit holding one channel's contiguous stream |
| **Orphan index entry** | Entry marked free whose block isn't yet overwritten — T2 target |
| **Carving** | Recovering data by content signatures rather than filesystem metadata |
| **DHAV** | Dahua-family per-frame container magic (`DHAV` start, `dhav` end) |
| **MPEG-PS / PES** | MPEG-2 Program Stream packs (`00 00 01 BA`) and packets (`00 00 01 E0`); Hikvision payload is close to this |
| **ES** | Elementary stream — raw coded video without a container |
| **Annex-B** | H.264/H.265 byte-stream format with `00 00 00 01` start codes |
| **NAL unit** | Network Abstraction Layer unit — the H.264/H.265 packet (SPS, PPS, IDR slice…) |
| **GOP** | Group of Pictures — an I-frame plus dependent P/B frames; smallest independently decodable unit |
| **Remux / transcode** | Re-container without re-encoding (payload preserved) / re-encode (payload changed) |
| **Evidence copy / derivative** | Remuxed, hash-proven copy for court / re-encoded copy, always labelled |
| **E01 / Ex01 / AFF4 / raw** | Forensic image formats; E01 is the common Indian lab exchange format |
| **HPA / DCO** | Host Protected Area / Device Configuration Overlay — ATA features hiding sectors |
| **Write blocker** | Hardware/software that permits reads and prevents writes to evidence media |
| **RTC** | Battery-backed real-time clock in the recorder; main source of time error |
| **OSD** | On-screen display — the time burned into the video pixels |
| **Ring / circular overwrite** | Overwrite oldest data when full |
| **Chain of custody (CoC)** | Unbroken documented record of who held/handled evidence and what they did |
| **Provenance class** | A–D label for how evidence was obtained (§6.8) |
| **Coverage map** | Byte-level accounting of the image into five buckets (§6.5) |
| **Negative findings** | What the tool could not read/recover — report §7 |
| **Panchnama** | Seizure memo signed by witnesses |
| **Signature dossier** | Hexdump + entropy + periodicity export for an unknown format (FR-09) |
| **TSA / RFC 3161** | Trusted timestamping authority / its protocol |
| **Tier S / R / E** | Synthetic / real-device / export-file test corpus |

---

## 11. Build phases and staging

### 11.1 Phases (doc 8 §2, done criteria doc 8 §5)

| Phase | Scope | Done when | Stage |
|---|---|---|---|
| 0 | `core/`: case DB, audit chain, hashing, read-only I/O | `spectra case new` works; tampered audit record detected 100/100 | **Pre-selection** |
| 1 | `identify/`: signature scanner + dossier | Every corpus image classified correctly, zero false-confident misidentifications | **Pre-selection** (dossier FR-09 → finals) |
| 2 | `acquire/`: raw + E01, write-block verify, HPA/DCO | Real disk → E01, hashes match independent `sha256sum`; strace shows zero writes | Finals |
| 3 | `plugins/dahua`: T1 + DHAV decode + MP4 remux | Disk image → playable evidence MP4 whose ES hash equals the reference | **Pre-selection** |
| 4 | `plugins/hikvision` | Same on the second family, **no core file changed** | **Pre-selection** |
| 5 | `recover/`: T2 + T3 | T2+T3 recover ≥ 30 % more recording-minutes than T1 on the overwritten corpus | **Pre-selection** (T4 → finals) |
| 6 | `timeline/`: time model + multi-camera timeline | Known offset recovered within stated uncertainty; gaps rendered | **Pre-selection** |
| 7 | `report/`: report + BSA s. 63(4) certificate — **★ shippable line ★** | Complete report, populated negative findings, filled certificate | Finals (skeleton pre-selection) |
| 8 | `ml/`: motion → objects → faces | Analytics run with networking disabled; every hit has model hash + disclaimer | Finals (motion may land pre-selection) |
| 9 | `plugins/`: vigi · uniview · matrix · generic_fs | ≥ 6 families, support level stated for each | Finals |
| 10 | Validation, SOP finalisation, user manual | All ACs pass; validation report signed; docs 5–7 final | Finals |

### 11.2 Staging rules

- **Pre-selection** builds the demo-visible pipeline (identify → parse → recover → time)
  on a real, minimal Phase 0. Deferring Phases 2 and 7 to finals is a **reorder, not a
  cut** — pre-selection parsers read ingested images (FR-17) through `EvidenceSource`,
  so nothing gets re-plumbed.
- **Phase 0 is never deferred.** "Retrofitting an audit trail produces an audit trail
  nobody believes" (doc 8 §2).
- **Dahua before Hikvision** (one DHAV parser = disk + `.dav` + carving). **Report before
  analytics** (a parser + court-ready report is a product; face detection without a report
  is a demo).
- **Cut order if time compresses:** Phase 9, then Phase 8. **Never Phase 0, 2, or 7.**
- **Parallel tracks from day one:** hardware procurement (P4) and the Tier S synthetic
  corpus (P6) — the corpus is the single most effective de-risking action against R1.

### 11.3 The six-minute judging demo (doc 8 §4)

| Time | Segment | The point to land | Stage |
|---|---|---|---|
| 0:00–0:30 | The problem | Seven tools, four unaudited, a hand-typed Excel timeline | Pre-selection |
| 0:30–1:15 | Identification | "Chassis says CP Plus, disk says Dahua" — matched bytes shown, confidence 0.97 | Pre-selection |
| 1:15–2:00 | Acquisition + integrity | Pre-flight and dual hashes (finals); **tamper a record, `spectra case verify` names it** (pre-selection) | Split |
| 2:00–3:00 | Recovery | T1 vs T1+T2+T3 side by side; play a clip only recovery found | Pre-selection |
| 3:00–3:45 | Time normalisation | OSD 14:22 vs truth 14:04:18, offset +00:17:42 ± 1 s; then the honest "not established" case | Pre-selection |
| 3:45–4:30 | Timeline + correlation | Two devices, uncertainty bands, a 40-minute all-channel gap as a finding | Pre-selection |
| 4:30–5:15 | Analytics | 6 h → 22 motion segments; "leads, not identifications" | Finals |
| 5:15–6:00 | Report | §7 negative findings (pre-selection skeleton) + s. 63(4) certificate (finals) | Split |

Pre-load the case; nothing live that takes > 20 s. Backup answers for judges are in doc 8 §4.

---

## 12. Work split — six sections P1–P6

Mirrors doc 8 §3.1 (same order, same scope). **Update both together.**

| Section | Role (doc 8) | Owns | Owner |
|---|---|---|---|
| **P1** | Core / integration lead | `core/`, `services/`, `cli.py`, CI, packaging | **Shankhanil** (`ShankhanilSaha`) |
| **P2** | Formats engineer A | `identify/`, `plugins/dahua.py`, `plugins/generic_fs.py` | **Shankhanil** (`ShankhanilSaha`) |
| **P3** | Formats engineer B | `plugins/hikvision.py`, `recover/` | *unassigned* |
| **P4** | Acquisition + time | `acquire/`, `timeline/`, hardware track | *unassigned* |
| **P5** | Reporting + UI | `report/`, `desktop/`, templates, legal-format review | *unassigned* |
| **P6** | ML + validation | `ml/`, `tools/make_corpus.py`, doc 6 execution | *unassigned* |

### P1 — Foundation & integration

- **Purpose:** build the layer every other section reads and writes through, then hold
  the system together.
- **Pre-selection (Phase 0):** `EvidenceSource` for raw + E01 via pyewf (FR-17, FR-75) ·
  `Hasher` · `AuditChain` + `spectra case verify` (FR-71, FR-72) · minimal `CaseStore` ·
  `MediaTool.remux` (FR-31) · CLI skeleton · co-owns `plugins/base.py` and
  `core/models.py` with P2 and P3.
- **Finals:** `JobRunner` (NFR-09) · `RawDeviceSource` · read-only proof under
  strace/Procmon (AC-03) · PyInstaller offline bundle + `spectra selftest` + SBOM (NFR-15,
  NFR-16) · services hardening · end-to-end integration.

### P2 — Identification, Dahua family, generic FS

- **Purpose:** decide what a disk is, and fully parse the highest-coverage family.
- **Pre-selection:** Phase 1 signature scanner, confidence, ambiguity surfacing
  (FR-01..FR-04) · Phase 3 Dahua T1 parse, `DHAV` `frames()`, `carve_signatures()`,
  `decode_time()` with known-good vectors (FR-21, FR-54, NFR-12).
- **Finals:** Phase 9 `generic_fs` + TP-Link VIGI full parse · dossier +
  `tools/dump_signature.py` (FR-09) · firmware facts + brand inference (FR-05, FR-06) ·
  Dahua system log (FR-07) · multi-disk member detection (FR-08).

### P3 — Hikvision family & recovery engine

- **Purpose:** prove the plugin contract on a second family and build the headline
  differentiator.
- **Pre-selection:** Phase 4 master sector, both HIKBTREE copies with divergence reported,
  `frames()`, `decode_time()` (FR-22) · Phase 5 generic T2 with validation, T3 carver +
  GOP reassembly, merge/dedup, coverage map (FR-29, FR-41..FR-45). The engine uses each
  plugin's `frames()` / `carve_signatures()`; `recover/` stays vendor-free.
- **Finals:** T4 (FR-43) · resumable carve (FR-46) · Phase 9 Uniview and Matrix (parse if
  RE completes, else carve-only).

### P4 — Acquisition, time & hardware track

- **Purpose:** get real devices and real images, and make every timestamp defensible.
- **Pre-selection:** day-one procurement in doc 8 §2 order (Dahua-family → Hikvision-family
  → Matrix → VIGI/Uniview/Godrej/Honeywell; second-hand units and pulled disks are fine) ·
  doc 4 §11 steps 1–2 (zeroed baseline, controlled recordings with GPS clock — doubles as
  Phase 6 time ground truth) · Phase 6 time model, methods A–D, uncertainty propagation,
  refusal (FR-50..FR-53), gap analysis (FR-58), cross-device correlation (FR-60).
- **Finals:** Phase 2 raw/E01 imaging, write-blocker verification, HPA/DCO, bad-sector
  map, provenance classes (FR-10..FR-15, FR-19) · live logical (FR-16) · Tier R R-01..R-04
  to P2 and P3.

### P5 — Reporting & UI

- **Purpose:** turn case data into something an examiner can operate and a court accepts.
- **Pre-selection:** desktop shell · multi-channel timeline view (FR-57) · report skeleton
  with §7 negative findings generated from the coverage map (FR-81) and the s. 63(4)
  certificate template (FR-83).
- **Finals:** Phase 7 full 12-section PDF/A + `findings.json` (FR-80..FR-86) · verify
  instructions (FR-85) · synchronised playback (FR-59) · G6 dry-run with a practising
  examiner.

### P6 — ML analytics & validation

- **Purpose:** provide the ground truth everyone tests against, then analytics and the
  signed validation report.
- **Pre-selection:** Tier S corpus with `ground_truth.json` — S-01..S-05 and S-07 first
  (they gate Phases 3–6), then S-09..S-11 · motion detection (FR-90) once those land.
- **Finals:** Phase 8 objects + face detect-and-cluster in the isolated worker (FR-91,
  FR-92, FR-95, FR-96); resolve model licensing (doc 2 Q4, §17 item 10) first · Phase 10
  execute doc 6, produce the validation report.

### Handoffs

```
P1    ── EvidenceSource · Hasher · AuditChain · models.py ─────────▶ everyone
P6    ── Tier S images + ground_truth.json ───────────────────────▶ P2, P3, P4
P4    ── Tier R images + in-frame clock ground truth ─────────────▶ P2, P3, P6
P2/P3 ── Recording[] · Frame · DeviceTime (raw + encoding) ───────▶ recover/, timeline/, MediaTool
P3    ── recovered Recording[] (tier + confidence) · coverage ────▶ P5
P4    ── ReferenceTime · gaps · provenance class ─────────────────▶ P5
```

---

## 13. Shankhanil — P1 + P2 task list

Shankhanil holds both sections on the critical path: **P1 blocks every other section, and
P2's Dahua parser is what P3's recovery work and P4's timeline first run against.** Work in
this order, and keep Phase 0 deliberately small so Phases 1 and 3 start early.

### 13.1 Pre-selection

**Phase 0 — foundation (P1). Unblock the team first.**

- [x] **Scaffold:** `spectra/` package per §6.12, `pyproject.toml` (Python 3.11+), pytest,
      lint, CI running Tier S + unit tests on every commit (doc 6 §5). *CI workflow written
      (`.github/workflows/ci.yml`, Linux + Windows); not yet run on GitHub.*
- [x] **`core/models.py`:** `Extent`, `Recording`, `Frame`, `DeviceTime` (raw + encoding +
      optional local), `ReferenceTime` (utc may be None, uncertainty always present),
      `ProbeResult`, `DiskLayout`. Draft now; freeze at end of Phase 1 with P3 (and P4 for
      the time types). *Drafted with additions listed in §17 item 12.*
- [x] **`core/source.py`:** `EvidenceSource` protocol with **no write method**;
      `RawImageSource` (incl. split `.001…`), `EwfImageSource` via pyewf, `FileSetSource`;
      `read`, `map` (mmap window), `readable_ranges`; zero-fill-with-gap-recorded on read
      errors; handles opened read-only. *E01 tested against images from a test-only EWF
      writer (`tests/ewfgen.py`); see §17 item 16 for the libewf zero-fill limitation.*
- [x] **`core/hashing.py`:** `Hasher` — MD5 + SHA-256 in one `update()` pass.
- [x] **`core/audit.py`:** `AuditChain` with canonical JSON, `prev_digest` linking,
      `started`/`ok`/`error` records, `verify()` naming the broken record;
      `tests/test_audit.py` tamper trials (insert, delete, reorder, field edit) — 100/100
      detected (AC-08, TC-IN-01).
- [x] **`core/casestore.py`:** case directory layout, SQLite WAL schema subset
      (`case_meta`, `evidence`, `identification`, `disk_layout`, `recording`, `artifact`,
      `coverage`, `audit` + UPDATE/DELETE triggers), content-addressed `artifacts/`.
      *Full doc 3 §11 schema plus columns listed in §17 item 12; manifest head digest catches
      tail truncation.*
- [x] **`cli.py` (Typer):** `spectra case new`, `case open`, `case verify`,
      `import image`, `import files` — calling the same services the UI will use. *Also
      `case info`, `identify [select]`, `parse`, `list recordings`, `export clip`,
      `verify chain`.*
- [x] **`core/media.py`:** `MediaTool.remux` (pinned FFmpeg, `-c copy`, command + stderr
      logged and hashed, version recorded). Read §17 item 5 on what "bit-identical" must
      mean before writing the comparison. *Per-frame PTS via a SPECTRA-written MPEG-TS
      intermediate; every export re-extracts the ES and asserts VCL NAL identity
      (`core/es.py`). FFmpeg is located via `SPECTRA_FFMPEG`/PATH until packaging pins one.*
- [x] **`plugins/base.py` + `registry.py`:** the §6.4 contract and `select_plugin` with
      ambiguity audit. Co-owned with P3. *Dispatch lives in `identify/engine.py`; ambiguity
      policy in §17 item 13.*
- **Done:** `spectra case new` works; tampered record detected 100/100; any image opens
  read-only through `EvidenceSource`. *Met.*

**Phase 1 — identification (P2).**

- [x] **`identify/engine.py`:** probe every registered plugin at FR-01 offsets (LBA 0,
      first/last 1 MB, configurable sparse sweep); report family, layout version,
      confidence, matched bytes + offsets, all candidates (FR-02); unknown layout →
      `parse_supported=False` → carving (FR-03); superblock facts (FR-04). *FR-04 facts
      land in `disk_layout` at parse time; standard-FS signatures reported as observations.*
- [x] Persist to `identification`; audit `identify.complete` / `identify.ambiguous`.
- [ ] **Tests:** S-10 must report ambiguity, not pick one · S-11 must report zero
      recordings · S-09 truncated must not crash · TC-RB-01/02 (zero-length, NTFS image).
      *TC-RB-01/02 pass; S-11 reports unknown; S-09 parses without crashing and notes the
      cut. S-10 is ambiguous only with a stand-in Hikvision plugin — with the shipping
      registry it is a strict xfail until the Hikvision layout conflict is settled (§17 item 25).*
- [ ] **Freeze contracts** at end of Phase 1 with P3/P4/P6 (§14). *Needs the team; draft
      additions in §17 item 12.*
- **Done:** every corpus image classified correctly, zero false-confident
  misidentifications (AC-01).

**Phase 3 — Dahua family (P2, using P1's export pipeline).**

- [ ] **`plugins/dahua.py` `probe()`:** superblock magic near LBA 0 [R] **plus
      `DHAV`/`dhav` in the data area as a fallback** so a damaged or unknown-version
      superblock is still carvable (doc 4 §3.4). *DHAV fallback and `.dav` export probe
      done; superblock magic blocked — §17 item 11.*
- [ ] **`superblock()` / `enumerate()`:** T1, plus the `include_orphans` hook P3's T2
      needs; versioned layout descriptors; unknown version → carve-only. *Done for the
      `dav_export_v1` layout (export file sets); raw disks raise `LayoutNotSupported` →
      carve-only until §17 item 11 is resolved.*
- [x] **`frames()`:** DHAV header → payload → trailing length → `dhav`; payload as
      `memoryview`; channel + `t_device` from the header; bounds-check every length field
      (TC-RB-05). *Plus `pts_ms` from the 16-bit tick, unwrapped using the 1 s date.*
- [x] **`carve_signatures()`:** `DHAV` with validator "length field lands on a matching
      `dhav`" — the self-validating pair that kills false positives (doc 4 §3.2).
- [x] **`decode_time()`:** packed date-time [R] → `DeviceTime(raw, encoding)`. Never
      return a bare `datetime`. Vectors in `tests/test_timestamps.py`; ≥ 12 ground-truth
      vectors before upgrading the label to [C] (doc 4 §12 row 5). *Spec-conformance
      vectors only; the ground-truth test skips with notice until hardware.*
- [x] **Export path:** `frames()` → ES writer → `Hasher` → `MediaTool.remux` → `Hasher` →
      `artifacts/`, per-frame `t_device` as PTS (FR-30..FR-33); audit `export.start` /
      `export.complete`.
- [ ] Validate the DHAV parser on `.dav` export files (Tier E) before the disk parser
      exists (doc 6 §3.3); T1 tests on S-01; expose hooks for P3's S-02/S-03 tests.
      *Validated on `.dav` files built from real x264/x265 output (spec conformance, not real
      Tier E); real `.dav` samples and S-01 still needed.*
- **Done:** disk image → playable evidence MP4 whose ES hash equals the reference. *Met on
  the export-file path only (H.264 and H.265); the disk path waits on §17 item 11.*

### 13.2 Finals

**P1**
- [ ] `JobRunner`: worker processes, checkpoint every N s / M bytes, resume (NFR-09).
- [ ] `RawDeviceSource` for `/dev/sdX` and `\\.\PhysicalDriveN`; drop privileges after
      opening; BLKROSET on Linux, logged.
- [ ] Read-only proof harness: full workflow under strace (Linux) / Procmon (Windows),
      assert zero writes to evidence paths (AC-03, TC-AQ-03).
- [ ] Determinism check: two runs → identical `findings.json` (AC-11, TC-RP-06) — with P5.
- [ ] Packaging: PyInstaller single binary + offline wheelhouse, `spectra selftest`
      (verifies bundled FFmpeg/libewf/model hashes), SBOM, pinned dependency hashes.
- [ ] Services layer: every UI action reachable from the CLI; integration across P2–P6.

**P2**
- [ ] `plugins/generic_fs.py` (ext2/3/4, FAT32, exFAT, XFS) + TP-Link VIGI naming/index
      reader — full parse (FR-24, FR-27); deleted recovery via FS journal, inodes,
      `ftyp`/`moof` carving.
- [ ] `identify/dossier.py` + `tools/dump_signature.py`: first/last 1 MB hexdump, entropy
      map, repeated-structure + periodicity (FR-09).
- [ ] Firmware facts + brand inference, family and brand always shown separately
      (FR-05, FR-06).
- [ ] Dahua `system_log()` (FR-07); multi-disk member detection (FR-08, S-12).
- [ ] Verify CP Plus ≡ Dahua on R-04 (doc 4 §12 row 6); upgrade labels only on proof.
- [ ] Fuzz every Dahua structure parser (TC-RB-04..07).

### 13.3 Dependencies

| From | What | Needed by |
|---|---|---|
| P6 | S-01, S-02, S-03, S-09, S-10, S-11 + `ground_truth.json` | Phase 1 tests, Phase 3 |
| P4 | Dahua-family or CP Plus unit / pulled disk; `.dav` exports | Phase 3 [R]→[C] upgrades |
| P3 | Agreement on `base.py` / `models.py` | End of Phase 1 |
| P4 | Agreement on `DeviceTime` / `ReferenceTime` | End of Phase 1 |

### 13.4 What others need from Shankhanil, and when

| To | What | When |
|---|---|---|
| Everyone | `EvidenceSource`, `Hasher`, `AuditChain`, `CaseStore`, CLI skeleton | End of Phase 0 — the earlier the better |
| P3 | Frozen `base.py` / `models.py`; Dahua `frames()` + `carve_signatures()` + `include_orphans` hook | End of Phase 1 / during Phase 3 |
| P4 | `DeviceTime` from Dahua `decode_time()` with vectors | Before Phase 6 integration |
| P5 | Identification results + export manifest format | Before report skeleton |

---

## 14. Team contracts — frozen at end of Phase 1

Freezing early lets P2 and P3 build parsers genuinely in parallel. A change after the
freeze needs the owner and every consumer to agree.

| Contract | Owner | Consumers |
|---|---|---|
| `VendorPlugin` protocol (doc 3 §4) | P1 + P2 + P3 | identify, recover, services |
| `Recording`, `Frame`, `Extent` (FR-28) | P1 | everyone |
| `DeviceTime` — raw value + encoding, **never a pre-converted datetime** | P2 | timeline, report |
| `ReferenceTime` (doc 3 §8.1) | P4 | report, UI |
| `AuditRecord` + canonical JSON (doc 3 §3.3) | P1 | everyone |
| Coverage buckets + precedence where tiers overlap | P3 | report |
| Provenance classes A–D (FR-19) | P4 | report |
| `ground_truth.json` schema (doc 6 §3.4) | P6 | P2, P3, P4 |
| `findings.json` schema (FR-86) | P5 | P6 (AC-11) |

---

## 15. Engineering and documentation conventions

### 15.1 Code (applies once code exists)

- **Evidence I/O only via `EvidenceSource`.** Reject any `open()`, `mmap`, or subprocess
  touching an evidence path directly.
- **No network** in the analysis path: no HTTP clients, update checks, model downloads,
  telemetry. Models ship in the bundle, pinned by SHA-256.
- **Streaming:** never materialise a recording or image; iterate extents, map windows,
  yield `memoryview`s.
- **Time:** plugins return `DeviceTime`; only `timeline/` produces `ReferenceTime`. Never
  `datetime.now()` for anything that lands in findings or reports.
- **Audit:** services write `*.start` before and `*.complete` / `*.error` after every
  state-changing operation.
- **Probes don't guess:** unknown layout ⇒ `parse_supported=False`; ambiguous ⇒ surface
  all candidates.
- **Defensive parsing:** bounds-check every length/offset read from disk, detect index
  cycles, cap allocations — hostile and corrupt images are normal inputs (TC-RB-*).
- **Tests:** generic conformance suite for every plugin; known-good vectors for every
  timestamp decoder (NFR-12, 100 %); ≥ 80 % line coverage on parsing, timestamps,
  hashing, recovery; test names reference TC/FR/AC IDs; skipped ≠ passed.
- **Corpus independence:** P6 writes the generator from doc 4 structure notes, not from
  parser code — a generator and parser sharing one misreading pass each other's tests.
- **Licensing (NFR-16):** permissive dependencies only, SBOM maintained. FFmpeg must be an
  LGPL build (no `--enable-gpl` / `--enable-nonfree`) invoked as a subprocess. Don't copy
  code from GPL/AGPL or unlicensed repositories. No vendor firmware, decompiled code, or
  vendor IP; format analysis is clean-room (doc 4 §13).
- **Portability:** `pathlib`; no shell-specific scripts in core; support both
  `/dev/sdX` and `\\.\PhysicalDriveN`.
- **Traceability:** reference FR/NFR/AC IDs in docstrings, tests, and commit messages
  where they apply.

### 15.2 Documentation (applies now — the repo is all docs)

- **British spelling**, matching existing docs: normalise, artefact, licence (noun),
  analyse, organisation, colour.
- Cross-reference as `doc N §x.y`, with relative links like
  `[doc 3 §4](03-architecture.md#4-the-plugin-contract)`; keep FR/NFR/AC IDs stable —
  never renumber.
- Keep doc 4 confidence labels on every structural claim.
- Tables for requirements, matrices, and mappings; ASCII diagrams in fenced code blocks
  with aligned box borders; Mermaid is used in doc 3 §12.
- Doc 8 status markers: ✅ written · ⏳ pending.
- When changing requirements, update the traceability tables (doc 1 end, doc 2 §11, doc 6
  §9) in the same change.

### 15.3 Planned CLI (doc 7 §5)

Implemented so far: `case new|open|info|verify|custody|attach|chain`,
`import image|files`, `identify [select|show]`, `parse`, `coverage`, `recover`,
`list recordings|artifacts`,
`export clip`, `time set|show`, `timeline`, `gaps`, `analyze motion`,
`report generate|findings|certificate`, `verify chain`. Everything else below is planned.
`import image` requires `--provenance A|B|C|D` (§17 item 14). `analyze motion` requires
FFmpeg and refuses without it (§17 item 18). Every command the desktop app drives takes
`--json`; `identify show` and `list artifacts` are read-only and write no audit record.

```
spectra case new|open|info|attach|custody|verify
spectra devices
spectra acquire [--resume] · spectra acquire live
spectra import image|files|firmware
spectra identify [--json] · spectra identify dossier
spectra parse · spectra coverage · spectra events · spectra list recordings
spectra recover --tiers T2,T3[,T4] [--resume]
spectra time set|show
spectra timeline · spectra gaps
spectra export clip|range|manifest [--derivative]
spectra analyze motion|objects|faces|anpr · spectra annotations list
spectra report generate|findings|certificate|sign
spectra verify chain|hashes|artifact        # what report §11 tells a defence expert to run
spectra selftest                            # verifies bundled component hashes
```

---

## 16. Risks (doc 1 §9)

| # | Risk | L / I | Mitigation |
|---|---|---|---|
| R1 | **No access to real DVR hardware** | High / Critical | Buy/borrow cheap Dahua- and Hikvision-family units early; second-hand and pulled disks; synthetic corpus keeps development unblocked |
| R2 | Firmware drift breaks a parser | High / Medium | Versioned layout descriptors; degrade to carving; fixtures per firmware |
| R3 | A format resists RE in time | Medium / Medium | Full parse for Dahua + Hikvision; carve-only elsewhere, stated per family |
| R4 | Legal challenge to tool output | Medium / High | Validation report, SOPs, audit chain, negative findings, certificate, open source |
| R5 | Analytics false positives mislead | Medium / High | Lead-only framing, mandatory confidence, human-verification statement |
| R6 | Performance on multi-TB images | Medium / Medium | Streaming/mmap, parallel carve workers, checkpoints, NFR targets |
| R7 | Scope creep | High / High | Phase order and non-goals are contractual within the team |
| R8 | Encrypted / obfuscated storage | Low–Medium / Medium | Detect and report honestly; don't burn time on it |

**Staffing risk (current):** P1 and P2 are both held by one person and both sit on the
critical path; Phase 0 must stay small, and P6's early corpus delivery matters most to them.

---

## 17. Open issues not yet in the docs

Raised in the 2026-09-15 review of the WhatsApp diagrams. **Not yet adopted into docs
1–8** — raise the relevant item before implementing the affected area; don't silently
diverge from the docs.

**Likely doc updates (the diagrams were right):**

1. **SHA-1 — decided and implemented (2026-09-18).** `Hasher` computes MD5, SHA-1 and
   SHA-256 in one pass. The s. 63(4) Schedule names exactly those three as checkboxes and
   a certificate cannot tick a box for a digest the tool never computed. FR-11 and doc 3
   `Hasher` still say MD5 + SHA-256 and need updating. Note `evidence` and `artifact` were
   **not** migrated to store SHA-1: the certificate re-hashes each artefact it tenders, and
   for the evidence image it reprints the stored MD5/SHA-256 and leaves the SHA-1 box
   blank rather than re-reading a multi-TB image to fill one checkbox.
2. **E01 can't store SHA-256** (it holds MD5/SHA-1). SHA-256 needs a sidecar manifest,
   and the sidecar's own hash must go into the audit chain.
3. **s. 63(4) certificate structure:** Part A and Part B, two signatories, and it must
   accompany the record at each instance of submission — FR-83's template should reflect
   this.
4. **BSA s. 86 presumption** for secure electronic records (DSC from a CCA-licensed CA +
   RFC 3161 timestamp) could sit beside FR-88 as priority P2. It presumes no alteration
   *since signing* only — never claim more.

**Technical corrections to carry into implementation:**

5. **Remux "bit-identical" (FR-31, AC-05):** FFmpeg's MP4 muxer converts H.264/H.265 from
   Annex-B start codes to length-prefixed NAL units and moves SPS/PPS into `avcC`/`hvcC`,
   so a file-level hash of the MP4 payload will never equal the ES hash. Define identity
   per NAL unit (or round-trip via `h264_mp4toannexb`, ignoring parameter-set placement).
   The disk image is the evidence; the MP4 is a verified extraction.
6. **Image verification (FR-11, doc 3 §5):** hashing the buffer on its way to the writer
   doesn't verify the written image. Read the image back; for E01, hash the decompressed
   media via pyewf, not the `.E01` segment files.
7. **32-bit time placeholder:** `0x7FFFFFFF` = 2038-01-19 **03:14:07 UTC** (displays as
   08:44:07 IST). Match raw values, never rendered strings.
8. **Windows lab hazard:** DVR disks look uninitialised; clicking "Initialize Disk" writes
   a partition table onto evidence. Hardware write blocker or SAN policy `OfflineAll` —
   belongs in SOP-2.
9. **Live acquisition (FR-16):** add Hikvision ISAPI alongside ONVIF Profile G / vendor
   CGI; enforce a read-only command allowlist.
10. **Model licences (doc 2 Q4):** Ultralytics YOLO is AGPL-3.0, YOLOv7/v9 are GPL,
    InsightFace pretrained models are non-commercial — incompatible with NFR-16.
    Candidates to verify: YOLOX / RT-DETR (Apache-2.0), OpenCV Zoo YuNet/SFace, PaddleOCR
    for ANPR.

**Raised while building Phase 0/1/3 (2026-09-15) — need team agreement or a doc update:**

11. **No Dahua on-disk layout to implement.** Doc 4 §3.1 gives the superblock and block
    index only as [R] concepts, with no offsets. So the plugin ships no disk layout
    descriptor, and raw Dahua-family disks are carve-only. That also blocks S-01: P6
    cannot generate a Dahua *disk* layout from doc 4 without inventing one, and a parser
    written to match an invented layout proves nothing (corpus independence, §15.1).
    Options: (a) wait for hardware (doc 4 §12 row 3), or (b) agree a clearly labelled
    **synthetic** layout spec that P6 generates and P2 parses, never shown as real.
12. **Contract additions for the freeze (§14):** `ProbePlan` passed to `probe(src, plan)`;
    `Frame.extent`, `Frame.sequence`, `Frame.pts_ms`; `Recording.channel`/`t_start`/`t_end`
    may be `None`, and `stream` may be `"unknown"`; `Recording.frame_count`/`notes`;
    `Signature.validate(src, offset) -> length | None`. Schema adds `evidence.source_*`,
    `identification.status/selection/…_json`, `recording.stream/frame_count/notes_json`,
    and `artifact.size_bytes/source_record_seq`. Doc 3 §4 and §11 need updating once agreed.
13. **Ambiguity policy is stricter than doc 3 §4.1.** When more than one family matches,
    nothing is auto-selected, and parse is blocked until an audited
    `identify select --reason`. This follows rule 10 and TC-ID-02. The doc 3 sketch picks
    the highest confidence.
14. **`import image` requires a stated provenance class.** SPECTRA didn't acquire the image
    and can't know A/B/C/D (FR-19). The doc 7 §5 example omits the flag.
15. **Dev FFmpeg isn't LGPL.** Tests run with a GPL build (`imageio-ffmpeg`, or apt in
    CI). `MediaTool` records `ffmpeg_gpl_or_nonfree_build` on every export. The pinned
    LGPL bundle (NFR-16) is still a packaging task.
16. **libewf zero-fills corrupt E01 chunks without saying so.** pyewf 20240506 raises
    nothing and exposes no checksum-error list, so a bad chunk reads as zeros and can't be
    recorded as a gap. Ingest compares the MD5 stored in the E01 with the media and writes
    any mismatch, or a missing stored hash, into `evidence.notes`. It can't locate the
    chunk. Options: verify chunk checksums ourselves, or get the libewf error list exposed.
    The PyPI wheel also can't *write* E01 (no zlib), which matters for Phase 2 acquisition.

**Raised while building Phases 4, 5 and 7 (2026-09-18):**

17. **Schema is at version 2.** `disk_layout` gained `format_t_device_raw` /
    `format_t_device_encoding` / `format_t_local` (the parse service had been discarding
    `DiskLayout.format_time` entirely); `custody` gained `seal_number`, `seal_intact` and
    `note`; a new `attachment` table holds FR-74 documents. `seal_intact` is three-valued
    on purpose — NULL means never sealed, 0 means found broken, and merging them would
    report a case-ending fact at the weight of a routine one. Doc 3 §11 needs updating.

18. **Motion analysis decodes, or refuses.** `services/analytics.py` previously differenced
    the compressed payload as if it were a greyscale buffer, which measures bitrate rather
    than motion and — because encoders spend more bits on moving scenes — produced
    plausible-looking output. It now decodes through FFmpeg at 2 fps / 320x240 and raises
    without it. Doc 3 §9 should state that Stage 1 samples, and that activity shorter than
    one sample interval can fall between samples.

19. **`findings.json` carries two digests.** The whole-file digest covers this
    examination's own history (audit head, artefact list, generation time) and legitimately
    differs between runs, because generating a report is itself an audited event.
    `conclusions_digest` covers only what was found on the disk and is what AC-11 is
    really about — the number two labs compare. Doc 2 AC-11 should say which one it means.

20. **WeasyPrint is an optional extra.** PDF/A needs cairo and pango at the system level,
    which CI and the minimum-spec VM should not carry. `spectra report generate` writes the
    HTML and states plainly that the PDF step was skipped. Packaging (NFR-15) still has to
    decide whether the bundle ships it.

21. **FR-34 audio extraction has no producer.** No plugin emits `Frame.kind == "audio"`, so
    an audio export path would be written against an invented format. Needs a real `.dav`
    or Hikvision sample first (P2, doc 4 §12).

**Raised while fixing the 2026-09-19 status review:**

22. **Dahua export parsing splits a channel where its device time breaks** (plugin 0.2.0):
    a step back, or a jump forward of more than 2 s (`MAX_CONTINUOUS_STEP`). One recording
    spanning S-08's clock change used to end before it started and crashed `spectra gaps`.
    The 2 s is a policy, not a format fact: the date field has 1 s resolution and the fps
    extension is a whole number, so a stream slower than 1 frame/s would fragment frame by
    frame, loudly. A segment that does not begin on an I-frame now says so in its notes.
23. **New FR-55 anomaly kind `ends_before_start`** (negative finding
    `NF-TIME-ENDS-BEFORE-START`), a new value in the P5 `findings.json` contract. Any plugin
    can still produce such a recording — Hikvision passes an inverted index entry through
    as-is — so timeline and gap analysis keep it off the axis and say so: `gap_report` gains
    `not_placed`, listing every recording the gap figures do not cover, with the reason.
24. **Motion rule pinned** (`ml/motion.py`): a pixel changed when `|Δ| >= sensitivity`,
    box around every changed pixel, and the OpenCV path is held to the pure-Python reference
    by a parity test. They had differed at the threshold and on small contours, so results
    depended on whether OpenCV was installed. New `ml` extra (OpenCV + NumPy), installed in
    CI. The motion model spec had claimed blur and morphological closing; it does neither.
25. **Tier S oracles.** S-03 listed a surviving range of −13 086 bytes: its 16 KiB overwrite
    runs past the end of its 5378-byte recording, so nothing after byte 2080 survives —
    including the second I-frame. The oracle now says so, but S-03 cannot test "resume at
    the next intact I-frame" (doc 1 §2.4); shortening the overwrite is P6's call. S-10's
    ambiguity oracle fails with the shipping plugins for the same reason S-04/S-05 are not
    recognised (Tier S puts the Hikvision magic at offset 0, the published layout at
    0x210); it is a strict xfail until P6 and a Tier R disk settle which layout is real.

26. **Desktop shell is Electron, not PySide6** (decided by the P1/P2 owner, 2026-09-19;
    docs 1, 2, 3 and 8 updated). `desktop/` holds no forensic logic: every action runs
    `python -m spectra.cli … --json` as a child process from a fixed argv built in the main
    process, so each GUI action is an audited command anyone can re-run. Hardening: sandboxed
    renderer, context isolation, UI and media served from two in-process schemes only
    (media only by digest from the open case's artefact store), every other request
    cancelled, and `host-resolver-rules` so no hostname resolves. No dev server: the renderer
    is built and loaded from disk, so nothing listens on a socket. `ui/` in §12 is now
    `desktop/` (P5). Packaging the Electron app with a bundled Python is still open (NFR-15).

**Diagram ideas NOT adopted — research spikes only, never committed scope:**

- XiongMai WFS support and a "shared DHFS = WFS descriptor core" — XiongMai isn't one of
  the eight named OEMs, and the Godrej → WFS mapping is unverified.
- Hikvision OFNI / Dahua DHII frame-index recovery as the novelty claim.
- "timestamp < volume init ⇒ footage destroyed" — a dead-RTC reset or clock change gives
  the same pattern; at most an anomaly to corroborate from the device log.
- "Hikvision has no delete ⇒ T2 always empty" — HDD initialise and expiry settings do
  remove footage, and a lagging backup HIKBTREE may hold orphan entries. Verify on hardware.
- Deferring the read-only I/O layer and audit log to finals; a JSONL audit log instead of
  the SQLite `audit` table.

**Doc 2 open questions:** Q1 which recorder units the team can obtain, and when · Q2 E01
vs raw as lab default · Q3 mandated agency report letterhead/format · Q4 redistributable
ONNX detector licence · Q5 government TSA availability for RFC 3161.

---

## 18. Notes for Claude

- **Identify the section (P1–P6) before implementing.** P1/P2 work is Shankhanil's; for
  P3–P6, confirm who it's for rather than assuming.
- **Resolve ambiguity from the docs.** When a request is ambiguous and the docs imply a
  sensible default, take it and state the choice rather than blocking on a question.
- **Keep this file in sync.** When the split, phases, or contracts change, update doc 8 §3
  and §11–§14 here in the same change. When a doc changes a rule summarised here, update
  this file too.
- **Claim only what has been run.** Phase 0 and parts of Phases 1 and 3 exist (§13.1). Don't
  claim a CLI command, test, or CI job works until it exists and has been run. Dev setup:
  `python -m venv .venv`, `pip install -r requirements.txt` (adds `-e .[dev,ml]` and a
  dev FFmpeg; E01 needs `.[ewf]` on Python 3.11–3.13), then `pytest`. FFmpeg tests skip
  with a notice unless `SPECTRA_FFMPEG` or PATH provides FFmpeg; the motion parity tests
  skip without OpenCV (`ml` extra). libewf-python has no wheel for Python 3.14 yet, so use
  3.11–3.13 for the E01 tests.
- **Format facts:** quote doc 4 with its label; never write "confirmed" for [R]/[H]/[U].
  Synthetic-only results must be labelled synthetic.
- **Legal claims:** stick to the citations in §8.1; flag any new legal claim for review
  rather than asserting it.
- **Hardware is the critical-path risk (R1).** Doc 4 §12 tracks what still needs a real
  image.
- **Git:** default branch `main`. Don't commit unless asked.
