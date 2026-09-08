# 07 — User Manual

**Named deliverable** of the problem statement.
**Product:** SENTINEL v1.0
**Audience:** Forensic examiners and investigating officers.
**Companion:** always follow [doc 5 — SOPs](05-sop.md). This manual tells you how to
operate the tool; the SOP tells you what a defensible examination requires.

---

## 1. Before you start

### 1.1 What SENTINEL does

Takes a DVR/NVR hard disk, a forensic image of one, or vendor export files, and
produces: identified device, forensic image, extracted recordings, recovered deleted
footage, a time-normalised multi-camera timeline, AI-assisted review leads, and a
signed, court-ready report with a verifiable chain of custody.

### 1.2 What SENTINEL will not do

- It will not write to your evidence. Ever. There is no option to.
- It will not invent a timestamp it cannot establish.
- It will not identify a person. Analytics produce **leads**, which you verify.
- It will not hide what it failed to read. The negative-findings section is generated
  and you cannot delete from it.

### 1.3 System requirements

| | Minimum | Recommended |
|---|---|---|
| CPU | 4 cores x86-64 | 8+ cores |
| RAM | 8 GB | 32 GB |
| Working storage | 1.2× the source disk | NVMe, 2× source |
| GPU | none | any CUDA GPU (≈4× faster analytics) |
| OS | Linux (Ubuntu 22.04+/RHEL 9+) or Windows 10/11 | Linux |
| Network | **none required — the tool is designed to run offline** | keep it offline |

You also need a **hardware write blocker** for physical acquisition. Without one your
evidence is provenance class B instead of A, and the report will say so.

---

## 2. Installation

### 2.1 Linux

```bash
# From the offline bundle (for air-gapped lab machines)
tar xzf sentinel-1.0.0-linux-x86_64.tar.gz
cd sentinel-1.0.0
sudo ./install.sh            # installs to /opt/sentinel, adds /usr/local/bin/sentinel

sentinel --version
sentinel selftest            # verifies bundled FFmpeg, libewf, models, and their hashes
```

`selftest` checks the hash of every bundled component against the manifest. **Run it
after every install and after any system change.** If it fails, the tool's own version
recording in your reports is untrustworthy — stop and reinstall.

Raw device access needs elevation:

```bash
sudo sentinel acquire --device /dev/sdb ...
```

The process drops privileges immediately after opening the device handle.

### 2.2 Windows

Run `sentinel-1.0.0-setup.exe` as Administrator. Launch **SENTINEL** from the Start
menu, or use `sentinel.exe` from an elevated PowerShell for CLI work.

### 2.3 Verifying your download

```bash
sha256sum sentinel-1.0.0-linux-x86_64.tar.gz
# compare against the published SHA256SUMS file
```

---

## 3. Concepts you need to understand

Five ideas that shape everything in the interface. Understanding them takes five minutes
and prevents most user errors.

### 3.1 Format family ≠ brand

The parser is chosen by what is **on the disk**, not by the badge on the front. A CP
Plus recorder normally contains a Dahua-family disk. A Godrej unit may be either family
depending on the SKU. SENTINEL shows you both: the format family it detected, and the
brand it inferred (and from what evidence).

**Never override the detected family based on the chassis label.** If they disagree,
the disk is right.

### 3.2 Provenance class

How the evidence was obtained, which determines what can be claimed about it.

| Class | How obtained | Strength |
|---|---|---|
| **A** | Physical image, hardware write-blocked | Strongest |
| **B** | Physical image, write protection by other means, justified | Strong |
| **C** | Live logical acquisition from a running device | Moderate — source not independently verifiable |
| **D** | Third-party export (owner-provided files) | Weakest — original storage not examined |

Printed in the report. You cannot change it after acquisition.

### 3.3 Recovery tier

Where a recording came from, which determines how much you can rely on its metadata.

| Tier | Source | Metadata quality |
|---|---|---|
| **T1** | Valid index | Complete. What the vendor tool would show. |
| **T2** | Orphaned (deleted) index entry, validated against block content | Complete — channel and time known exactly |
| **T3** | Carved by signature, no index | Varies. Time and channel present **if** the format stores them per frame |
| **T4** | Carved around bad sectors | Fragmentary; gaps recorded |

Every recording in the UI is badged with its tier and a confidence score.

### 3.4 The three-layer time model

| Layer | Meaning |
|---|---|
| `t_device` | Exactly what is stored on the disk, in the vendor's own encoding. Never altered. |
| `t_local` | Wall-clock time as the recorder believed it — i.e. the wrong time, if its clock was wrong |
| `t_reference` | **Normalised UTC.** The only time used for correlation. Always carries an uncertainty and a derivation method. |

**`t_reference` can legitimately be "not established".** If you have no way to measure
the clock offset, SENTINEL shows device-local time with a warning banner and refuses to
assert absolute times. This is correct behaviour — do not work around it by entering a
guessed offset.

### 3.5 Evidence copy vs derivative copy

- **Evidence copy** — remuxed MP4, video payload bit-identical to the disk. Hash-proven.
  This is what goes to court.
- **Derivative copy** — re-encoded for compatibility or size. Clearly labelled
  everywhere. Never present a derivative as the evidence.

---

## 4. Your first case — the GUI walkthrough

### Step 1 — New case

**File → New Case**. Fill in:

| Field | Notes |
|---|---|
| Case ID | Your agency's case number |
| Title | Short description |
| Agency / unit | Printed on the report |
| FIR / authority reference | Required for the report |
| Examiner name, designation | Printed and signed |
| s. 79A notification reference | If you are a notified Examiner of Electronic Evidence |
| Case directory | Where the case container lives. **Use fast local storage.** |

### Step 2 — Attach the scene documents

**Case → Attachments → Add**. Attach and hash the seizure memo/panchnama, Form F-1,
and the BNSS s. 105 seizure video.

Do this **first**. It makes the custody chain start at the scene rather than at your
bench, and F-1 carries the reference-clock capture data you will need in Step 6.

### Step 3 — Add evidence

**Evidence → Add**, then choose:

| Option | Use when |
|---|---|
| **Acquire from attached disk** | You have the physical disk (normal case) |
| **Import existing image** | Someone else imaged it (raw/E01/AFF4/split) |
| **Import export files** | Owner handed over `.dav`/`.mp4` files |
| **Live acquisition** | The recorder cannot be powered down |
| **Import firmware dump** | You dumped the SPI/NAND flash |

#### Acquiring from a disk

1. Attach the disk through a write blocker, then to the workstation.
2. SENTINEL lists detected devices. Select yours — **check the serial number against
   Form F-3** before proceeding.
3. Pre-flight runs automatically and shows:
   - Write protection: **verified / NOT VERIFIED**
   - HPA / DCO presence and hidden sector counts
   - SMART status and drive identity
4. **If write protection is not verified, stop.** Fix the blocker. Only override with
   authorisation — it downgrades you to provenance class B permanently.
5. If HPA/DCO is present, decide whether to remove it. Removing it is usually right
   (some vendors keep a backup superblock there), but it must be authorised and it is
   logged.
6. Choose the format (E01 for lab exchange, raw if downstream tools need it) and the
   destination, then **Start**.
7. Progress shows throughput, ETA, and a live bad-sector count. Acquisition is
   resumable — you can close the app and come back.
8. On completion, verify: source and image MD5/SHA-256 must match. Record them on F-3.

### Step 4 — Identify

Runs automatically after acquisition; re-run from **Evidence → Identify**.

The Identification panel shows:

- **Format family** and **layout version**
- **Confidence** (0–1)
- **Matched signature bytes** with their offsets — the raw evidence for the decision
- **All other candidates** with their confidences
- **Inferred brand** and what that inference is based on
- **Support level**: full parse / carve-only / unsupported

Reading the result:

| Result | What to do |
|---|---|
| One family, confidence > 0.9 | Proceed |
| Multiple candidates with similar confidence | Review the matched bytes for each; select deliberately. Your choice is logged. |
| Family known, layout version unknown | Parsing is disabled; **carving still works**. Proceed in carving mode. |
| Unknown | Generate a signature dossier (**Identify → Export Dossier**) and proceed with generic carving. An unknown format is not a dead end. |

### Step 5 — Parse

**Evidence → Parse**. When it finishes you get:

- **Recording inventory** — by channel and date, with tier and confidence badges
- **Disk layout** — block size, block count, blocks used, format date
- **Device event log** — power events, formats, disk errors, logins, **clock changes**
- **Coverage map** — a bar showing what fraction of the image is parsed / carved /
  structural / unreadable / **unaccounted**

> **Read the coverage map.** A large `unaccounted` region with high entropy means data
> the tool could not explain — possibly recordings you have not recovered. Investigate
> before you conclude, and run recovery (Step 7) regardless.

### Step 6 — Set the time model ⭐

**Do this before any analysis.** Everything downstream depends on it.

**Evidence → Time Model**.

1. **Timezone** — take it from the firmware dump or the settings screen photographed at
   seizure. Chinese-origin units are frequently left on UTC+8 by installers; if the
   configured zone looks wrong, that is itself a finding.

2. **Clock offset** — choose the highest-ranked method you actually have evidence for:

   - **A — NTP synchronised.** Requires proof from the config and the device log.
     ±1 s.
   - **B — Reference-clock capture** (the usual best case). From Form F-1: enter the
     true time of the capture, then scrub to the capture in the footage and mark the
     frame where the reference clock is legible. SENTINEL computes the offset. ±1 s.
   - **C — External timestamped event.** An access-control swipe, POS transaction, or
     call visible in frame with an independent record. ±2–60 s.
   - **D — Live RTC read.** The device's displayed time noted against true time before
     power-down. ±1 s at that instant, extrapolated.
   - **None available.** Select this. The tool will show device-local time only and
     will not assert absolute time anywhere.

3. If the device event log shows a **manual clock change**, SENTINEL prompts you to
   define offsets per segment. Do it — a single offset across a clock change is wrong
   for half the timeline.

4. Enter the derivation note. **It is printed verbatim in the report**, so write it as
   you would want it read aloud in court.

> **Do not enter a guessed offset to make the interface look complete.** "Offset
> undetermined" is a defensible finding. An unsupported timestamp is not.

### Step 7 — Recover deleted footage

**Evidence → Recover**. Select tiers:

- **T2 — orphan index entries.** Fast (minutes). Always run it.
- **T3 — full signature carve.** Slow (hours on a large disk) but it is where the
  footage the vendor tool cannot see comes from. It is checkpointed and resumable —
  start it and carry on with other work.
- **T4 — bad-sector tolerant.** Run it if acquisition reported read errors.

Results merge into the recording inventory, badged by tier. Items with no recoverable
timestamp appear as **"time unknown"** and are ordered by physical position. They are
still evidence; they simply cannot be placed on the clock.

### Step 8 — The timeline

**View → Timeline**. Channels as lanes on one `t_reference` axis.

| Element | Meaning |
|---|---|
| Solid bar | Recording present |
| Hatched region | **Gap — no recording.** A finding in itself. |
| Bar shading | Recovery tier (T1 solid → T4 lightest) |
| Marker ▲ | Device event (power on/off, format, video loss) |
| Marker ● | Analytics hit |
| Marker ★ | Your bookmark |
| Faint horizontal band | Time uncertainty for that device |

Controls: scroll to zoom, drag to pan, click a bar to preview, shift-drag to select a
range, `B` to bookmark a selection.

**Look at the gaps first.** A gap across all channels at once usually means a power
event or a deliberate interruption, and it is often the single most important finding in
the case.

### Step 9 — Analytics

**Analysis → Run Analytics**. Select a time range and channels.

1. **Motion** — always run this first. It typically reduces review volume by 90 %+ and
   it is what makes the "days of footage" problem tractable.
2. **Objects** — person / vehicle / two-wheeler / bag. Runs only over motion segments,
   which is why it is fast.
3. **Faces (detect + cluster)** — finds faces and groups similar ones. You label the
   clusters. **There is no watchlist and no identity database; the tool cannot tell you
   who someone is.**
4. **ANPR** — Indian plate formats.

Every hit shows its confidence, the model name and hash, and the disclaimer.

> **You must visually verify every hit that enters your report**, against the source
> frame. The report states that you did. This is not a formality — it is the difference
> between a lead and an assertion.

### Step 10 — Review and export

Review candidate segments in the player. Bookmark and annotate what matters.

**Export → Evidence Clips**:
- **Evidence copy (recommended)** — remuxed MP4, payload bit-identical, hash-proven
- **Derivative copy** — re-encoded, labelled as derivative

Both come with a **hash manifest**. Always release the manifest with the clips.

### Step 11 — Report

**Report → Generate**. Review every section, and §7 especially.

**§7 Negative Findings** is generated from the coverage map, bad-sector map, unparsed
structures, empty channels, and timeline gaps. You can add to it. You cannot remove from
it. If it says something you did not expect, investigate before signing.

Check the **Annexure: BSA s. 63(4) certificate** — record identification, production
particulars, device particulars, hash values.

Then **Report → Sign & Finalise**. The report, `findings.json`, and the clip manifest
are written to the case directory, and the audit-chain head digest is embedded.

---

## 5. CLI reference

Everything in the GUI is available headless. Use the CLI for batch work, scripting, and
reproducible validation runs.

```bash
sentinel <command> [options]
```

### Case

```bash
sentinel case new --id CASE-2026-0142 --title "Market Rd CCTV" \
    --agency "District Cyber Cell" --fir "FIR 145/2026" \
    --examiner "Name" --designation "SI" --s79a "REF/2024/xx" \
    --dir /cases/CASE-2026-0142

sentinel case open   /cases/CASE-2026-0142
sentinel case info
sentinel case attach --file seizure_memo.pdf --kind document
sentinel case custody --from "SI Kumar" --to "Lab Store" --purpose "storage"
sentinel case verify        # verifies the audit chain and all artefact hashes
```

### Acquisition

```bash
sentinel devices                       # list attached disks

sentinel acquire --device /dev/sdb --format e01 \
    --out /images/case142_disk1.E01 \
    --blocker "Tableau T35u fw 1.2" \
    [--allow-no-blocker --justification "..."]   # downgrades to class B, audited

sentinel acquire --resume /images/case142_disk1.E01

sentinel import image  --file /images/existing.E01
sentinel import files  --dir  /media/usb/exports    # class D
sentinel import firmware --file /dumps/spi.bin

sentinel acquire live --host 192.168.1.64 --user admin \
    --channels 1,3,5 --from "2026-03-01T00:00:00+05:30" \
    --to "2026-03-01T06:00:00+05:30"              # class C
```

### Identification and parsing

```bash
sentinel identify --evidence EV-001 [--json]
sentinel identify dossier --evidence EV-001 --out dossier.html   # unknown formats

sentinel parse --evidence EV-001
sentinel coverage --evidence EV-001            # the coverage map
sentinel events --evidence EV-001              # device system log
sentinel list recordings --evidence EV-001 --channel 3 \
    --from 2026-03-01 --to 2026-03-02 [--tier T1,T2]
```

### Recovery

```bash
sentinel recover --evidence EV-001 --tiers T2,T3
sentinel recover --evidence EV-001 --tiers T3 --resume
sentinel recover --evidence EV-001 --tiers T4     # after read errors
```

### Time

```bash
sentinel time set --evidence EV-001 --tz +05:30 \
    --method B --true-time "2026-03-05T14:32:10+05:30" \
    --marked-frame REC-0912:4471 \
    --note "Reference clock capture per Form F-1, channel 2"

sentinel time set --evidence EV-001 --tz +05:30 --method none
sentinel time show --evidence EV-001
```

### Timeline and export

```bash
sentinel timeline --from "2026-03-05T14:00" --to "2026-03-05T16:00" \
    --channels 1,2,3,5 [--out timeline.svg]
sentinel gaps --evidence EV-001                 # gap analysis

sentinel export clip --recording REC-0912 --out /out/ [--derivative]
sentinel export range --channel 3 --from "..." --to "..." --out /out/
sentinel export manifest --out /out/manifest.json
```

### Analytics

```bash
sentinel analyze motion  --channels 1,2,3 --from "..." --to "..."
sentinel analyze objects --classes person,vehicle --min-score 0.5
sentinel analyze faces   --cluster
sentinel analyze anpr
sentinel annotations list --channel 3 [--source object]
```

### Reporting

```bash
sentinel report generate --out /out/report.pdf [--template agency_x]
sentinel report findings --out /out/findings.json
sentinel report certificate --out /out/bsa_63_4.pdf
sentinel report sign --examiner "Name"
```

### Verification (for a third party)

```bash
sentinel verify chain    --case /cases/CASE-2026-0142
sentinel verify hashes   --manifest /out/manifest.json
sentinel verify artifact --file clip.mp4 --manifest /out/manifest.json
```

These three commands are what the report's §11 tells a defence expert to run. They work
on a machine that has never seen the case before.

---

## 6. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| "Write protection not verified" | No write blocker, or one the tool cannot confirm | Use a hardware write blocker. Only override with authorisation — it permanently downgrades provenance to class B. |
| "Unknown format family" | Unsupported vendor, damaged superblock, or an encrypted disk | Export the dossier and proceed with carving. An unknown format is not a dead end — carving usually still recovers footage. |
| "Layout version not supported" | Newer firmware than the plugin knows | Carving mode. Report it upstream with the dossier so a layout descriptor can be added. |
| Parse finds 0 recordings but the disk is full | Wrong family selected, or the index area is corrupt | Re-check identification; run T3 carve, which does not need the index. |
| Large `unaccounted` in the coverage map | Data the parser could not explain | Run T3. If it persists, note it in the report — it is a genuine finding, not a cosmetic issue. |
| Exported clip will not play | Vendor codec variant (H.264+/Smart codec) | Check the codec-variant warning on the recording. Export a derivative copy for viewing; **keep the evidence copy as the evidence**. |
| Timestamps look wrong by hours | Timezone misconfigured on the device (commonly UTC+8) | Set the correct timezone in the time model. The misconfiguration itself is worth reporting. |
| Timestamps drift over the recording period | RTC drift | Method B if you have a reference capture; otherwise report the drift and the resulting uncertainty. |
| "Offset undetermined" banner | No offset evidence available | Correct behaviour. Report device-local time. **Do not enter a guess.** |
| Timeline lanes do not align across devices | Different clock offsets, correctly applied | This is the tool working. Check each device's uncertainty band. |
| Carve running for many hours | Normal on multi-TB disks | It is checkpointed. Let it run, or resume later. Faster working storage is the real fix. |
| Out of memory | Should not happen (4 GB ceiling) | Report it as a bug with the image size and operation — it indicates a buffering defect. |
| `selftest` fails | Bundled component altered or corrupt | Reinstall. **Do not use the tool for casework until it passes** — your reports record component versions and hashes. |
| Analytics slow | CPU-only | Expected. Motion-gate first (the default). A GPU gives roughly 4×. |
| "Multi-disk array detected" | Disk is one member of a set | Image every disk, preserving bay order (SOP-2 step 2.3), and import them all. |

---

## 7. FAQ

**Can SENTINEL modify my evidence?**
No. There is no write path to evidence in the code, the OS handle is read-only, and the
validation suite proves zero write syscalls against evidence paths across a full
workflow (doc 6, TC-AQ-03).

**Why does it sometimes refuse to give me a timestamp?**
Because it cannot establish one. A CCTV report with an unexplained absolute timestamp is
the easiest thing in the world to attack. "Device-local time; absolute offset not
established" is honest and survives.

**Can it tell me who a person is?**
No, deliberately. It detects and clusters faces so you can find every appearance of the
same person across days of footage. Identification is your job, with evidence.

**The recorder is a CP Plus but it says Dahua-family. Is it wrong?**
No — that is the expected result. CP Plus ships Dahua-ODM hardware. The tool reports
what is on the disk, which is what determines how to read it.

**Can I run it on an air-gapped machine?**
Yes, and you should. It requires no network at any point, and the analytics process is
explicitly isolated from networking.

**How do I prove to a court that the exported clip is unaltered?**
The report's §11 gives the hash table and the exact commands. A third party runs
`sentinel verify` (or plain `sha256sum`) against the manifest. The video payload in the
evidence copy is bit-identical to what was on the disk, and the extracted-ES hash proves
it independently of the container.

**What if the tool crashes mid-case?**
Long operations checkpoint and resume. The audit chain records the interrupted operation
as "started" with no completion, and still verifies. Nothing is silently lost.

**A colleague ran the same image and got a different report. Why?**
They should not have. `findings.json` is deterministic for the same image, version and
parameters (doc 6, TC-RP-06). Differences mean different parameters (check the time
model and the recovery tiers used) or different versions — both are recorded in the
report header. If neither differs, report it as a bug.

---

## 8. Getting help

- **Bug reports:** include the tool version, `selftest` output, the operation, the
  image size and format family, and the relevant audit-log excerpt.
  **Never attach case data.**
- **New vendor support:** send the signature dossier
  (`sentinel identify dossier`) — it contains structural bytes, not video.
- **Format documentation:** [doc 4](04-oem-comparative-analysis.md), including the
  reverse-engineering procedure in §11.
