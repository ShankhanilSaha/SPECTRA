# 02 — Product Requirements Document

**Product:** SPECTRA — Unified Vendor-Agnostic DVR/NVR Forensic Analysis Platform
**Version:** 1.0 (baseline)
**Owner:** SIH 2026 team · NTRO problem statement
**Depends on:** [doc 1 — Problem Analysis](01-problem-analysis.md)

---

## 1. Product summary

SPECTRA is an offline desktop forensic platform that takes a seized DVR/NVR hard disk
(or an image of one, or a vendor export) and produces court-admissible surveillance
evidence: identified device, forensic image, parsed recordings, recovered deleted
footage, time-normalised multi-camera timeline, offline AI-assisted leads, and a
BSA-compliant report with an unbroken cryptographic chain of custody — in one tool,
with one workflow, for the eight DVR/NVR OEM families common in India.

**One-line positioning:** *DVR Examiner's job, done openly, with Indian legal output
and deleted-footage recovery that actually looks where the footage still is.*

---

## 2. Goals and success criteria

### 2.1 Product goals

| G# | Goal | Measured by |
|---|---|---|
| G1 | Eliminate multi-tool workflows | One tool covers acquisition→report; count of external tools required drops from ~7 to 0 (FFmpeg is bundled, not a separate workflow step) |
| G2 | Support ≥ 6 OEM families at full-parse level, ≥ 8 at carving level | Validation matrix, doc 6 |
| G3 | Recover footage that vendor tooling cannot | ≥ 30 % additional recording-minutes recovered over T1 baseline on the ground-truth corpus |
| G4 | Make timestamps defensible | 100 % of reported times carry derivation method + uncertainty; 0 fabricated timestamps |
| G5 | Cut analysis time | ≥ 80 % reduction in operator hours to first relevant clip on the standard 7-day/8-channel benchmark |
| G6 | Produce legally usable output | Report + auto-filled BSA s. 63(4) certificate accepted in dry-run review by a practising examiner/legal reviewer |
| G7 | Provable evidence integrity | Hash chain verifiable by an independent third party using only the report and published instructions |

### 2.2 Non-goals

Restated from [doc 1 §8](01-problem-analysis.md#8-scope-boundaries-and-explicit-non-goals),
binding on this release: no face recognition against identity databases, no video
enhancement, no pixel-level deepfake detection, no physical platter recovery, no live
VMS features, no cloud anything, no firmware exploitation.

---

## 3. Personas

### P1 — Field Investigating Officer ("Sub-Inspector, district cyber cell")
Seizes the recorder at the scene. Not a forensic specialist. Needs: an unambiguous
checklist, the reference-clock capture step, correct paperwork, and to not destroy the
evidence before it reaches the lab. Interacts mostly with **doc 5 SOP** and a one-page
seizure form, occasionally with the tool's field-triage mode on a laptop.
**Success:** device reaches the lab with a valid seizure record and a measured clock offset.

### P2 — Forensic Examiner ("CFSL / State FSL / notified s. 79A examiner")
The primary user. Runs the lab workflow, signs the report, faces cross-examination.
Needs: read-only guarantees, complete logging, control over every automated decision,
explicit negative findings, and a report they can defend line by line.
**Success:** completes a two-recorder, 16-channel, 30-day case in one working day
instead of a week, and can answer "how do you know?" for every number in the report.

### P3 — Investigation Lead / Case Officer
Not technical. Wants to know what happened, when, and who was there. Consumes the
timeline view and the report's findings section. Needs entity threading and clip
bookmarking, and needs to be prevented from treating an ML hit as an identification.
**Success:** gets a defensible narrative timeline and shareable evidence clips.

### P4 — Tool Developer / Vendor-support Contributor
Adds a new OEM plugin when a new recorder shows up. Needs: a documented plugin
contract, a signature-dumping utility, a fixture corpus, and a test harness that
proves the new plugin without touching core code.
**Success:** ships a working plugin for a new format family in under a week, changing
exactly one new file plus one registry entry.

### P5 — Court / Defence Expert (adversarial reader)
Never uses the tool; reads the report and attacks it. Needs to find the methodology,
the validation evidence, the negative findings, and a way to independently verify the
hashes.
**Success (for us):** finds nothing undisclosed.

---

## 4. Key user journeys

### UJ1 — Seizure to first clip (the headline journey)
1. Officer (P1) follows the seizure SOP, performs the reference-clock capture, records
   the seizure video, fills the form, seals the device.
2. Examiner (P2) creates a case in SPECTRA, enters authority and evidence metadata,
   attaches and hashes the seizure memo and video.
3. Examiner attaches the disk through a hardware write blocker. SPECTRA verifies
   write-blocking, detects HPA/DCO, and reports drive identity.
4. **Device Identification** scans signatures, reports `Dahua-family, format v3,
   confidence 0.97`, and shows the raw bytes it matched on.
5. **Acquisition** creates an E01 image with dual MD5/SHA-256 verification, resumable,
   with progress and ETA.
6. **Parsing** walks the index and enumerates 14 channels × 27 days of recordings.
7. Examiner sets the time model: enters the reference-clock capture, tool computes
   `clock_offset = +00:17:42 ± 1 s`, applies it to all frames.
8. Examiner filters the timeline to the incident window on the relevant channels.
9. **Motion analytics** reduces 6 hours across 4 channels to 22 candidate segments.
10. Examiner reviews, bookmarks 5 clips, exports them as remuxed MP4 + hash manifest.
11. **Report** generated, including the s. 63(4) certificate and negative findings.
12. Audit chain head digest printed on the report; case archived.

### UJ2 — Deleted-footage recovery
Incident is 24 days old; the vendor tool shows nothing before day 9. Examiner runs
recovery T2+T3 over the image. Tool reports 41 orphan index entries (T2, full metadata)
and 3,180 carved GOP runs (T3), of which 2,014 carry frame-level timestamps. 6 h 12 min
of additional footage on the relevant channel lands inside the incident window,
tagged with recovery tier and confidence.

### UJ3 — Live device that cannot be powered down
Airport recorder. Examiner uses live logical acquisition over the vendor/ONVIF
interface for the required window, with continuous hashing and a full protocol log. The
tool classifies the evidence as *provenance class C — live logical* and the report
states plainly what that means for weight and what could not be verified.

### UJ4 — Owner-provided USB export
Shopkeeper hands over 12 `.dav` files. Tool hashes on receipt, parses the container,
extracts embedded timestamps, flags that the on-disk source was not examined, and
records provenance class D. Report explicitly states the limitation.

### UJ5 — Cross-device correlation
Three recorders, three premises, three wrong clocks. Each gets a measured offset by a
different method (A, B, C). Tool places all 38 channels on one `t_reference` axis with
per-device uncertainty bands and threads a vehicle across five cameras from three
devices as a ranked lead.

### UJ6 — New OEM plugin (P4)
Unknown recorder. Signature scanner reports "no family matched" and dumps the first
64 KB with an entropy map and repeated-structure analysis. Developer identifies the
superblock, writes `plugins/newvendor.py` implementing the six contract methods, adds
a fixture, tests pass, plugin ships. Core untouched.

---

## 5. Functional requirements

Priority: **P0** = must ship, **P1** = should ship, **P2** = nice to have.
Each FR traces to a doc 1 section.

### 5.1 Device Identification (FR-01 … FR-09) — doc 1 §4.2

| ID | Requirement | Pri |
|---|---|---|
| FR-01 | Scan a disk/image at a defined set of offsets (LBA 0, first 1 MB, last 1 MB, and a configurable sparse sweep) for known format-family magic signatures | P0 |
| FR-02 | Classify into a **format family** (Dahua-family, Hikvision-family, Uniview, VIGI, Matrix, generic-ext, generic-FAT/exFAT, unknown) with a numeric confidence 0–1 and display the exact matched bytes with their offsets | P0 |
| FR-03 | Detect and report the on-disk **format version** where the superblock encodes one; on an unknown version, warn and offer carving mode rather than parsing | P0 |
| FR-04 | Report superblock-derived facts: disk capacity as recorded, data-block size, total/used block counts, format date, index locations | P0 |
| FR-05 | Ingest a firmware-flash dump (SPI/NAND) and extract: model string, serial, firmware version, camera-name-to-channel map, timezone/NTP config, user accounts | P1 |
| FR-06 | Map format family + firmware evidence to a **probable brand/model** (e.g. Dahua-family + CP Plus firmware string → CP Plus), always showing family and brand separately | P1 |
| FR-07 | Parse the device system log area into structured events (power on/off, format, disk error, login, video loss) | P1 |
| FR-08 | Handle multi-disk arrays: detect that a disk is member *n* of an *m*-disk set and require/relate the siblings before parsing | P1 |
| FR-09 | On "unknown" classification, emit a **signature dossier**: first/last 1 MB hexdump, entropy map, repeated-structure detection, candidate periodicity — to bootstrap plugin development | P2 |

### 5.2 Acquisition (FR-10 … FR-19) — doc 1 §4.1

| ID | Requirement | Pri |
|---|---|---|
| FR-10 | Create a **physical image** of an attached disk in raw (`dd`) and E01 formats | P0 |
| FR-11 | Compute MD5 **and** SHA-256 in a single pass during acquisition, for both source read and image written; verify they match and record both | P0 |
| FR-12 | Verify and record write-blocker presence before acquisition; refuse to proceed on a writable evidence device unless the operator explicitly overrides with a logged justification | P0 |
| FR-13 | Detect HPA and DCO; report hidden-sector counts; allow operator-authorised temporary removal, logged | P0 |
| FR-14 | Handle read errors gracefully: retry policy, zero-fill unreadable sectors, log every bad LBA range, and surface a bad-sector map in the report | P0 |
| FR-15 | Resumable acquisition with checkpointing; a 12 TB image that fails at 90 % resumes rather than restarting | P1 |
| FR-16 | **Live logical acquisition** from a powered recorder over its network interface (vendor HTTP/RTSP/CGI or ONVIF Profile G) for an operator-specified channel/time window, with continuous hashing and a full protocol transcript | P1 |
| FR-17 | Ingest an existing image (raw/E01/AFF4/split-raw) as evidence, hashing on ingest | P0 |
| FR-18 | Ingest loose vendor export files (`.dav`, `.264`, `.h264`, `.mp4`, vendor `.bin`) as evidence, hashing on ingest | P0 |
| FR-19 | Record an explicit **provenance class** per evidence item: A = physical image (write-blocked), B = physical image (no write-blocker, justified), C = live logical, D = third-party export. Class is printed in the report | P0 |

### 5.3 Filesystem & Format Parsing (FR-20 … FR-36) — doc 1 §4.2, §4.3

| ID | Requirement | Pri |
|---|---|---|
| FR-20 | Plugin architecture: a new format family is added as one module implementing the documented contract, with **zero core changes** | P0 |
| FR-21 | Full-parse support for **Dahua family** (covers Dahua, CP Plus, and Dahua-ODM Godrej/Honeywell variants) | P0 |
| FR-22 | Full-parse support for **Hikvision family** (covers Hikvision and Hikvision-ODM rebrands) | P0 |
| FR-23 | Full-parse support for **Uniview** | P1 |
| FR-24 | Full-parse support for **TP-Link VIGI** | P1 |
| FR-25 | Full-parse support for **Matrix (SATATYA)** | P1 |
| FR-26 | Full-parse support for **Honeywell native** (non-Dahua-derived models) | P2 |
| FR-27 | Generic plugin for recorders using standard filesystems (ext2/3/4, FAT32, exFAT, XFS) with near-standard media files | P1 |
| FR-28 | Enumerate recordings as a normalised record set: `{channel, stream, t_start, t_end, size, codec, resolution, fps, physical extents, recovery_tier, confidence}` — identical schema regardless of vendor | P0 |
| FR-29 | Report **unparsed regions** of the image as an explicit coverage map (parsed / carved / unreadable / unaccounted-for bytes) | P0 |
| FR-30 | Decode vendor frame containers to elementary streams without modifying payload bytes | P0 |
| FR-31 | **Remux** to standard MP4 with `-c copy`; the exported evidence clip's video payload is bit-identical to the source ES | P0 |
| FR-32 | Hash the extracted ES *and* the remuxed MP4 separately; both appear in the manifest | P0 |
| FR-33 | Preserve and apply per-frame timestamps as presentation timestamps in the output container | P0 |
| FR-34 | Extract audio streams where present, with the same fidelity guarantee | P1 |
| FR-35 | Handle vendor codec variants (H.264+/Smart H.264/H.265+) — decode correctly or, where a variant is not decodable, detect it and report rather than emitting corrupt output | P1 |
| FR-36 | Optionally produce a clearly labelled **derivative** transcode (H.264 baseline MP4) for distribution, never as the evidence copy | P1 |

### 5.4 Recovery (FR-40 … FR-46) — doc 1 §4.4

| ID | Requirement | Pri |
|---|---|---|
| FR-40 | **T1** — enumerate all recordings referenced by the valid index | P0 |
| FR-41 | **T2** — enumerate orphaned index entries (free/deleted flag) and validate their data blocks; recover with full metadata | P0 |
| FR-42 | **T3** — index-independent signature carve over the whole image for vendor frame magics and codec start codes; reconstruct from the first valid I-frame; extract channel and time from frame headers where the format carries them | P0 |
| FR-43 | **T4** — bad-sector-tolerant carve producing one clip per contiguous survivable run | P1 |
| FR-44 | Merge and deduplicate T1–T4 results; where the same footage appears at multiple tiers, keep the highest-tier instance and record that duplicates existed | P0 |
| FR-45 | Tag every recovered item with its tier and a confidence score; never present a carved item with an inferred timestamp as though it were indexed | P0 |
| FR-46 | Recovery must be resumable and must checkpoint; a carve over 12 TB may take hours | P1 |

### 5.5 Timeline & Correlation (FR-50 … FR-62) — doc 1 §4.5, §4.6

| ID | Requirement | Pri |
|---|---|---|
| FR-50 | Implement the three-layer time model (`t_device` → `t_local` → `t_reference`); store and display all three | P0 |
| FR-51 | Support all four clock-offset determination methods (A: NTP-proved, B: reference-object capture, C: external correlated event, D: live RTC read) and record which was used | P0 |
| FR-52 | Carry a numeric **uncertainty** with every normalised time; propagate it through correlation | P0 |
| FR-53 | Where no offset can be determined, mark times as *device-local, offset unknown* and refuse to assert absolute time anywhere in the output | P0 |
| FR-54 | Correctly decode each vendor's timestamp encoding (epoch, packing, endianness, timezone semantics), with the decoding rule unit-tested per vendor | P0 |
| FR-55 | Detect and flag time anomalies: backwards jumps, duplicate windows, gaps, and manual clock changes visible in the system log | P1 |
| FR-56 | Compare metadata timestamps against burned-in OSD time via OCR on sampled frames and flag divergence | P2 |
| FR-57 | Multi-channel timeline view: N lanes on one `t_reference` axis showing coverage, gaps, device events, and analytics hits | P0 |
| FR-58 | Explicit **gap analysis** — render and report windows with no recording, per channel and globally | P0 |
| FR-59 | Synchronised multi-channel playback (≥ 4 channels P0, ≥ 16 P1) locked to `t_reference` with offsets applied | P1 |
| FR-60 | Cross-**device** correlation: multiple recorders on one axis with per-device uncertainty | P1 |
| FR-61 | Entity threading: cluster analytics detections into candidate cross-camera tracks, ranked, labelled as leads | P2 |
| FR-62 | Bookmarking and annotation of timeline regions and clips, carried into the report | P1 |

### 5.6 Case Management & Chain of Custody (FR-70 … FR-76) — doc 1 §4.7

| ID | Requirement | Pri |
|---|---|---|
| FR-70 | Case container: one portable SQLite case DB plus a content-addressed artefact store | P0 |
| FR-71 | **Append-only hash-chained audit log** covering every operation, with operator identity, UTC timestamp, parameters, and before/after object hashes | P0 |
| FR-72 | Audit-chain verification command that detects any insertion, deletion, or modification of history | P0 |
| FR-73 | Chain-of-custody records: transfers of physical custody with holder, purpose, timestamp, signature reference | P0 |
| FR-74 | Attach and hash external case documents (seizure memo/panchnama, BNSS s.105 seizure video, authorisation letters) | P0 |
| FR-75 | All evidence I/O is read-only by construction, enforced at the I/O layer | P0 |
| FR-76 | Encrypt the case container at rest; role-based access (examiner / reviewer / read-only); log every media access | P1 |

### 5.7 Reporting (FR-80 … FR-88) — doc 1 §4.8, §5

| ID | Requirement | Pri |
|---|---|---|
| FR-80 | Generate the standard 12-section report (doc 1 §4.8) as PDF/A and HTML | P0 |
| FR-81 | **Mandatory negative-findings section**, auto-populated from the coverage map, bad-sector map, unparsed structures, empty channels, and time gaps | P0 |
| FR-82 | Complete hash table for every artefact (MD5 + SHA-256), plus the audit-chain head digest | P0 |
| FR-83 | Auto-filled **BSA s. 63(4) certificate** annexure with record identification, production particulars, device particulars, and hash values | P0 |
| FR-84 | Record tool version, plugin versions, FFmpeg version, and analytics model names+hashes in the report so results are reproducible | P0 |
| FR-85 | Third-party verification instructions: exact commands a defence expert can run to re-verify the hashes and the audit chain | P0 |
| FR-86 | Machine-readable export of findings (JSON) alongside the human report | P1 |
| FR-87 | Configurable report templates (agency letterhead, examiner block, language) | P1 |
| FR-88 | Optional RFC 3161 timestamping of the report's head digest | P2 |

### 5.8 Analytics (FR-90 … FR-97) — doc 1 §4.9

| ID | Requirement | Pri |
|---|---|---|
| FR-90 | **Motion/activity detection** with region masking and sensitivity control; output as timeline segments | P0 |
| FR-91 | Object detection and classification (person, vehicle, two-wheeler, bag) via a locally pinned ONNX model | P1 |
| FR-92 | Face **detection** and embedding-based clustering; investigator labels clusters. **No watchlist matching, no identity database** | P1 |
| FR-93 | ANPR for Indian plate formats | P2 |
| FR-94 | Person/vehicle re-identification for cross-camera threading | P2 |
| FR-95 | **All analytics run offline** with no network egress; enforced, not merely default | P0 |
| FR-96 | Every analytics result carries model name, model SHA-256, confidence, and a fixed human-verification disclaimer, in the UI and in the report | P0 |
| FR-97 | Analytics never mutate evidence; results are stored as separate annotations referencing frame numbers and `t_reference` | P0 |

---

## 6. Non-functional requirements

| ID | Requirement | Target |
|---|---|---|
| NFR-01 | **Acquisition throughput** | ≥ 80 % of the source drive's sustained sequential read rate (typ. ≥ 120 MB/s on SATA HDD) |
| NFR-02 | **Index parse time** | ≤ 5 min for a 12 TB disk with a fully populated index |
| NFR-03 | **Full carve (T3) throughput** | ≥ 300 MB/s on commodity NVMe-backed working storage, multi-worker |
| NFR-04 | **Memory ceiling** | ≤ 4 GB RSS regardless of image size (streaming/`mmap`; no full-image buffering) |
| NFR-05 | **Minimum host** | 4-core x86-64, 8 GB RAM, no GPU. GPU optional, ≥ 4× analytics speedup when present |
| NFR-06 | **Platform support** | Linux (primary) and Windows 10/11 (labs run Windows). macOS best-effort |
| NFR-07 | **Offline operation** | Fully functional with no network interface up; installable from an offline bundle |
| NFR-08 | **Determinism** | Same image + same version + same parameters ⇒ byte-identical extracted artefacts and identical findings JSON |
| NFR-09 | **Resumability** | Acquisition, parsing, carving, and analytics all checkpoint and resume |
| NFR-10 | **Auditability** | 100 % of state-changing operations produce an audit record; verification is O(n) and offline |
| NFR-11 | **Read-only guarantee** | No write syscall issued against any evidence path, verifiable by strace/Procmon in validation |
| NFR-12 | **Test coverage** | ≥ 80 % line coverage on parsing, timestamp, hashing, and recovery modules; 100 % of timestamp decoders have unit tests with known-good vectors |
| NFR-13 | **Localisation** | English UI at minimum; report template supports Hindi and regional-language agency blocks |
| NFR-14 | **Accessibility** | Keyboard-navigable UI, no colour-only encoding on the timeline (patterns + labels) |
| NFR-15 | **Installability** | Single installer/binary; no admin-privileged services beyond raw device read |
| NFR-16 | **Licensing** | Permissive open source; all bundled components licence-compatible and inventoried (SBOM) |

---

## 7. Acceptance criteria

Ship gate. Each is binary and testable per [doc 6](06-validation-plan.md).

| AC | Criterion |
|---|---|
| AC-01 | Correctly identifies the format family of every image in the validation corpus, with **zero false-confident misidentifications** (an "unknown" is a pass; a wrong family is a fail) |
| AC-02 | Acquisition produces an image whose MD5 and SHA-256 match an independently computed reference (`dd` + `sha256sum`), on ≥ 3 disks |
| AC-03 | Read-only verified: strace/Procmon shows zero writes to evidence paths across a full workflow |
| AC-04 | Full-parse enumerates ≥ 99 % of ground-truth recordings on Dahua-family and Hikvision-family corpus images |
| AC-05 | Exported evidence MP4's video ES is bit-identical to the ES extracted from the image (hash equality) |
| AC-06 | On the deletion-scenario corpus, T2+T3 recover ≥ 30 % more recording-minutes than T1 alone |
| AC-07 | Every timestamp decoder passes its known-good vector tests; a synthetic image with a known clock offset is normalised to within the stated uncertainty |
| AC-08 | Audit-chain verification detects a deliberately tampered record in 100 % of injected-tamper trials |
| AC-09 | Report generates with all 12 sections populated, including a non-empty negative-findings section, and a completed s. 63(4) certificate |
| AC-10 | Analytics run with the host network interface down; a network-egress attempt during analytics is impossible/blocked |
| AC-11 | Two independent runs on the same image produce identical findings JSON (NFR-08) |
| AC-12 | A new plugin can be added and pass its fixture test with no modification to any core file (verified by git diff scope) |
| AC-13 | Benchmark case (8 channels × 7 days) completes acquisition→report within one working day of operator time, ≥ 80 % less than the manual baseline |

---

## 8. Release plan

| Milestone | Contents | Exit criterion |
|---|---|---|
| **M0 — Foundation** | Case DB, audit chain, hashing, read-only I/O layer, CLI skeleton | AC-08 passes; audit chain verifiable |
| **M1 — Identify** | FR-01..FR-04, FR-09; signature scanner + dossier | AC-01 passes on available corpus |
| **M2 — Acquire** | FR-10..FR-15, FR-17..FR-19 | AC-02, AC-03 pass |
| **M3 — Dahua family** | FR-20, FR-21, FR-28..FR-33, FR-40 | End-to-end: disk → playable evidence MP4 with hashes |
| **M4 — Hikvision family** | FR-22 | Same, second family; proves the plugin contract |
| **M5 — Recovery** | FR-41..FR-45 | AC-06 passes |
| **M6 — Time & Timeline** | FR-50..FR-58 | AC-07 passes; multi-channel timeline usable |
| **M7 — Report** | FR-80..FR-85 | AC-09 passes. **Product is now shippable.** |
| **M8 — Analytics** | FR-90..FR-92, FR-95..FR-97 | AC-10 passes |
| **M9 — Breadth** | FR-23..FR-25, FR-27 | ≥ 6 families at full parse |
| **M10 — Validation & Docs** | doc 6 execution, doc 5 finalisation, doc 7 | All ACs pass; validation report signed |

M7 is the shippable line. Everything after it is upside. If the timeline compresses,
cut from M8/M9 — never from M0, M2, or M7.

---

## 9. Dependencies and assumptions

**Dependencies**
- Access to at least one Dahua-family and one Hikvision-family recorder + disk for
  format work. **This is the critical-path dependency** (risk R1, doc 1 §9).
- FFmpeg (pinned build), libewf, ONNX Runtime, Electron (desktop shell), SQLite.
- A hardware write blocker for validation of FR-12/AC-03.
- A legal/examiner reviewer for G6 sign-off on the report format.

**Assumptions**
- Disks are unencrypted at rest on the target models (holds for the overwhelming
  majority; encrypted models are detected and reported rather than defeated).
- Examiner hosts are x86-64 with ≥ 8 GB RAM.
- Cases are handled offline; no requirement for multi-user concurrent server access in v1.

---

## 10. Open questions

| # | Question | Owner | Needed by |
|---|---|---|---|
| Q1 | Which specific recorder units can the team physically obtain, and in what order do they arrive? | Team lead | M3 |
| Q2 | Do target labs standardise on E01 or raw for exchange? (Affects default) | Examiner contact | M2 |
| Q3 | Is an agency-specific report letterhead/format mandated that we should template? | Examiner contact | M7 |
| Q4 | Which ONNX detector licence permits redistribution in an open-source bundle? | ML owner | M8 |
| Q5 | Is RFC 3161 timestamping against a government TSA available, or is head-digest-only acceptable? | Team lead | M7 (P2 anyway) |

---

## 11. Traceability matrix (challenge → requirement → acceptance)

| Doc 1 challenge | FRs | ACs |
|---|---|---|
| §4.1 Acquisition | FR-10..FR-19 | AC-02, AC-03 |
| §4.2 Filesystems | FR-01..FR-09, FR-20..FR-29 | AC-01, AC-04, AC-12 |
| §4.3 Video formats | FR-30..FR-36 | AC-05 |
| §4.4 Recovery | FR-40..FR-46 | AC-06 |
| §4.5 Timestamps | FR-50..FR-56 | AC-07 |
| §4.6 Correlation | FR-57..FR-62 | AC-13 |
| §4.7 Chain of custody | FR-70..FR-76 | AC-03, AC-08 |
| §4.8 Reporting | FR-80..FR-88 | AC-09, AC-11 |
| §4.9 Analytics | FR-90..FR-97 | AC-10 |
