# 08 — Demo Plan & Deliverables Tracker

**Audience:** the team, and evaluators checking deliverable coverage.
**Purpose:** map every named deliverable to an artefact, fix the build order, and
script the judging demo.

---

## 1. Deliverables coverage

The problem statement names eight deliverables. Mapping, with honest status:

| # | Named deliverable | Artefact | Status |
|---|---|---|---|
| 1 | Comparative analysis of major DVR/NVR OEMs | [doc 4](04-oem-comparative-analysis.md) | ✅ Written. Confidence-labelled; §12 tracks what still needs hardware verification. |
| 2 | DVR/NVR forensic image | Tier S synthetic corpus + Tier R real images ([doc 6 §3](06-validation-plan.md)) | ⏳ Synthetic generator is buildable now; real images gated on hardware |
| 3 | System architecture documentation | [doc 3](03-architecture.md) | ✅ Written |
| 4 | Functional prototype | `sentinel/` — see §2 build order | ⏳ Build |
| 5 | Standard Operating Procedures | [doc 5](05-sop.md), with Forms F-1/F-2/F-3 | ✅ Written |
| 6 | Validation reports | [doc 6](06-validation-plan.md) — plan + report template | ✅ Plan written; report produced by executing it |
| 7 | User manuals | [doc 7](07-user-manual.md) | ✅ Written |
| 8 | Final project report | Assembled from docs 1–7 + the executed validation report | ⏳ At submission |

Plus the stated capability list, all traced to requirements in
[doc 2 §5](02-prd.md#5-functional-requirements):

| Required capability | Requirement | Where built |
|---|---|---|
| Automatically identify DVR models | FR-01…FR-06 | `identify/` |
| Parse proprietary file systems | FR-20…FR-29 | `plugins/` |
| Create forensic images | FR-10…FR-15 | `acquire/` |
| Extract videos and metadata | FR-28…FR-34 | `plugins/` + `core/media.py` |
| Decode proprietary formats | FR-30…FR-36 | `plugins/`, `core/media.py` |
| Recover deleted footage | FR-40…FR-46 | `recover/` |
| Normalise timestamps | FR-50…FR-56 | `timeline/timemodel.py` |
| MD5 and SHA-256 hashes | FR-11, FR-32, FR-82 | `core/hashing.py` |
| Correlate events across cameras | FR-57…FR-62 | `timeline/correlate.py` |
| Chain-of-custody records | FR-70…FR-76 | `core/audit.py` |
| Generate reports | FR-80…FR-88 | `report/` |
| AI face/object/motion detection | FR-90…FR-97 | `ml/` |
| ≥ 5–6 OEMs supported | FR-21…FR-27 | doc 4 §10 |

---

## 2. Build order and why

```
Phase 0  core/            case DB · audit chain · hashing · read-only I/O    ← everything needs it
Phase 1  identify/        signature scanner + dossier                        ← cheap, demoable, unblocks all
Phase 2  acquire/         raw + E01, write-block verify, HPA/DCO
Phase 3  plugins/dahua    T1 parse + DHAV frame decode + MP4 remux           ← widest coverage first
Phase 4  plugins/hikvision                                                    ← proves the plugin contract
Phase 5  recover/         T2 + T3                                            ← the differentiator
Phase 6  timeline/        time model + multi-camera timeline
Phase 7  report/          report + BSA s.63(4) certificate                   ← ★ SHIPPABLE LINE ★
Phase 8  ml/              motion → objects → faces
Phase 9  plugins/         vigi · uniview · matrix · generic_fs
Phase 10 validation, SOP finalisation, user manual
```

Three decisions worth stating explicitly:

**Phase 0 first, always.** The audit chain and read-only I/O layer are not
infrastructure to be retrofitted — every later module writes to them. Retrofitting an
audit trail produces an audit trail nobody believes.

**Dahua before Hikvision.** Hikvision is better documented, but Dahua's per-frame
self-describing `DHAV` container means the same frame parser serves the disk path, the
export-file path (`.dav`), and Tier-3 carving. One parser, three features. Best
effort-to-coverage ratio in the project.

**Report before analytics.** A tool that parses two vendors and produces a court-ready
report is a product. A tool with face detection and no report is a demo. If the timeline
compresses, cut Phase 8 and 9 — never Phase 0, 2, or 7.

### Parallel track: hardware acquisition

**Start on day one, before any code.** Doc 4 §12 shows that most remaining format
unknowns are gated on physical devices, and lead times are weeks. Priority order:

1. Any **Dahua-family** unit (Dahua or CP Plus — cheapest second-hand units are fine)
2. Any **Hikvision-family** unit
3. **Matrix SATATYA** — longest lead time, highest novelty value, order first even
   though it is built last
4. TP-Link VIGI, Uniview, Godrej, Honeywell

Second-hand units and pulled disks from e-waste and repair shops are legitimate,
cheap, and often more realistic (they contain real overwrite history). Note in the
validation report where images came from.

### Parallel track: synthetic corpus

`tools/make_corpus.py` (doc 6 §3.1) is buildable **immediately, with no hardware**, and
it unblocks parser development, CI, and every recovery-tier test. Build it during
Phase 0–1. This is the single most effective de-risking action against R1 (no hardware).

---

## 3. Team split (5–6 people)

| Role | Owns | Phases |
|---|---|---|
| **Core / integration lead** | `core/`, services, CLI, packaging, CI | 0, 10 |
| **Formats engineer A** | `identify/`, `plugins/dahua`, `plugins/generic_fs` | 1, 3, 9 |
| **Formats engineer B** | `plugins/hikvision`, `recover/` | 4, 5, 9 |
| **Acquisition + time** | `acquire/`, `timeline/` | 2, 6 |
| **Reporting + UI** | `report/`, `ui/`, templates, legal-format review | 7 |
| **ML + validation** | `ml/`, `tools/make_corpus.py`, doc 6 execution | 1 (corpus), 8, 10 |

Formats A and B share `plugins/base.py` — **freeze the contract early** (end of Phase 1)
so the two parser efforts can run genuinely in parallel. A contract change mid-project
costs both engineers a rewrite.

---

## 4. The judging demo — 6 minutes

Rehearse it. Pre-load the case so nothing runs live that takes more than 20 seconds; run
the slow operations (acquisition, full carve) beforehand and show the *results* plus a
short live segment to prove it is real.

**0:00–0:30 — The problem, concretely.**
Show the current workflow slide: seven tools, four with no audit trail, one hand-typed
Excel timeline. One sentence: *"Every hand-off in that chain is a question the defence
asks and nobody can answer."*

**0:30–1:15 — Identification.**
Attach an unknown disk image. Run identify. It reports **Dahua-family, layout v3,
confidence 0.97**, shows the matched signature bytes at their offsets, and infers the
brand as CP Plus from the firmware.
Say the line: *"The chassis says CP Plus. The disk says Dahua. We parse what's on the
disk — which is why one plugin covers four brands."*

**1:15–2:00 — Acquisition and integrity.**
Show the pre-flight: write protection verified, HPA detected, SMART read. Show dual
MD5/SHA-256 with source-vs-image verification. Show one audit-chain record.
Then: **tamper with a record and run `sentinel case verify`.** It fails, and names the
exact record. Ten seconds, and it is the most persuasive thing in the demo.

**2:00–3:00 — Recovery (the differentiator).**
Side by side: T1 alone versus T1+T2+T3 on the same disk. *"The vendor's own software
sees this much. We see this much."* Play a clip that exists **only** in the T2/T3 set —
footage the recorder considers deleted.
State the mechanism in one sentence: *"The recorder frees the index entry long before it
physically overwrites the block. Nobody looks in that window. We do."*

**3:00–3:45 — Time normalisation.**
Show the recorder's burned-in OSD time. Show the reference-clock capture from the scene.
Show the computed offset: **+00:17:42 ± 1 s**.
*"The camera says 14:22. The truth is 14:04:18. Every other tool would have put the
wrong time in the report — and that is the most common way CCTV evidence gets thrown
out."*
Then show the honest case: an evidence item with no offset evidence, displaying
"absolute time not established" and refusing to guess.

**3:45–4:30 — Timeline and correlation.**
Multi-channel timeline, two devices, uncertainty bands. Point at a gap: *"No recording
here on any channel for 40 minutes. That absence is itself a finding, and it is
invisible in a per-file workflow."* Scrub synchronised playback across four channels.

**4:30–5:15 — Analytics and the honest framing.**
Motion gating: 6 hours → 22 segments. Object detection on those segments only. A person
cluster across three cameras.
Then say the important part out loud: *"These are leads, not identifications. Every hit
carries its model hash and a human-verification requirement. There is no watchlist and
no identity database in this tool, deliberately."* Judges notice restraint.

**5:15–6:00 — The report.**
Open the PDF. Scroll to **§7 Negative Findings**: *"This section is generated, not
written, and the examiner cannot delete from it. It says what we failed to read — 2 % bad
sectors, 340 MB unaccounted, channel 7 empty. A forensic report that only lists successes
is not a forensic report."*
Then the **BSA s. 63(4) certificate annexure**, pre-filled with the hashes.
Close on: *"Seizure to certificate, one tool, one audit chain, and it tells you what it
could not do."*

### Backup answers to likely questions

| Question | Answer |
|---|---|
| "How is this different from DVR Examiner?" | Open and auditable, India-focused (CP Plus, Matrix, BSA certificate), carving tiers T2–T4, the measured-clock-offset time model, cross-device correlation, and offline analytics. We do not claim to beat it on firmware-revision breadth — we say so in the report. |
| "How many vendors really work?" | Full parse on Dahua and Hikvision families, which covers most Indian units once rebrands are counted. Carve-only on the rest, and the tool states its support level per family rather than claiming eight. |
| "How do you know the parse is correct?" | Ground-truth corpus with known recordings, and hash equality between the extracted elementary stream and the reference. Doc 6. |
| "What if it gets a timestamp wrong?" | Every decoder has known-good vectors before it ships, and where the offset cannot be established the tool refuses to assert absolute time. That refusal is a feature. |
| "Is the AI reliable enough for court?" | It is not offered for court. It is a review-reduction aid; every hit requires human verification and says so in the report. |
| "What about privacy?" | Fully offline, no cloud, no telemetry, no identity database, no watchlist matching, encrypted case containers, every media access audited. |
| "What's the biggest risk?" | Hardware access for format verification. Doc 4 §12 tracks exactly which claims are unverified, and the synthetic corpus keeps development unblocked meanwhile. |

---

## 5. What "done" looks like for each phase

| Phase | Done when |
|---|---|
| 0 | `sentinel case new` works; a tampered audit record is detected 100/100 times |
| 1 | Every corpus image is classified correctly, with zero false-confident misidentifications |
| 2 | A real disk images to E01 with hashes matching independent `sha256sum`; `strace` shows zero writes to evidence |
| 3 | Disk image → playable evidence MP4 whose ES hash equals the reference |
| 4 | Same, on the second family, **with no change to any core file** |
| 5 | T2+T3 recover ≥ 30 % more recording-minutes than T1 on the overwritten corpus |
| 6 | A known clock offset is recovered within its stated uncertainty; gaps rendered |
| 7 | A complete report with a populated negative-findings section and a filled s. 63(4) certificate |
| 8 | Analytics run with networking disabled; every hit carries model hash + disclaimer |
| 9 | ≥ 6 families supported, each with its support level stated |
| 10 | All ACs pass; validation report signed; docs 5–7 final |

---

## 6. Honest risk callout for the evaluators

Two things this project cannot fake, and says so up front:

1. **Format details need hardware.** Doc 4 labels every structural claim [C]/[R]/[H]/[U]
   and §12 lists exactly what must be verified. Nothing unverified goes into a
   court-facing report. The synthetic corpus keeps engineering moving meanwhile.
2. **"Supports 8 vendors" would be an overstatement.** Support level is stated per
   family — full parse, carve-only, or unsupported — in the tool, in the report, and in
   doc 4 §10. Overclaiming here is precisely the failure mode the negative-findings
   discipline exists to prevent, and it would be self-defeating in a forensic product.
