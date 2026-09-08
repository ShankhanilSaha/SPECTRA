# 05 — Standard Operating Procedures

**Named deliverable** of the problem statement.
**Scope:** seizure, acquisition, examination, and reporting of DVR/NVR surveillance
evidence using SENTINEL.
**Audience:** Investigating Officers (SOP-1), Forensic Examiners (SOP-2 … SOP-6).
**Legal basis:** BSA 2023 ss. 61, 63; BNSS 2023 ss. 105, 176(3); IT Act 2000 s. 79A.
See [doc 1 §5](01-problem-analysis.md#5-legal-and-evidentiary-framework-india).

---

## SOP-0 — Governing principles

These four rules override any step below. If a step conflicts with a principle, stop
and escalate.

1. **Do not alter the original.** If you cannot proceed without writing to evidence,
   stop and escalate. There is no deadline that justifies altering evidence.
2. **Record everything, including what you did not do.** An action you cannot account
   for is an action the defence will account for on your behalf.
3. **Prefer the disk to the device.** A physical image of the hard disk is stronger
   evidence than anything the recorder's own menu will hand you.
4. **Establish the clock before you power the device down.** Once it is off, the
   opportunity is gone forever and no software can recover it. See SOP-1 Step 6 — this
   is the step most often skipped and the one most often regretted.

---

## SOP-1 — Field seizure of a DVR/NVR

**Who:** Investigating Officer, with a forensic examiner present where BNSS s. 176(3)
applies (offences punishable by seven years or more).
**Time required:** 25–40 minutes.
**Materials:** seizure kit — anti-static bags, evidence seals, tamper-evident tape,
labels, camera/phone for the s. 105 recording, a **GPS/NTP-synced clock or phone with
a large legible time display**, torch, screwdriver set, cable ties, Form F-1.

### Step 1 — Authorise and record

1.1 Confirm the search/seizure authority and note its reference on Form F-1.
1.2 **Start the BNSS s. 105 audio-video recording before touching anything.** It must
run continuously through the entire seizure.
1.3 Arrange for independent witnesses per the seizure memo/panchnama requirements.

### Step 2 — Photograph in place

2.1 Wide shot showing the recorder in its installed position and surroundings.
2.2 Close shot of the front panel: brand, model, LED states, any display.
2.3 Close shot of the rear: **every cable connected, with its port**, before anything is
touched. This is how you later prove which camera was on which channel.
2.4 Close shot of the model/serial label.
2.5 Photograph the monitor if one is attached and displaying live video.

### Step 3 — Record the physical scene facts on F-1

- Make, model, serial, asset tag
- Number of cameras connected and which physical ports
- Camera physical locations and what each covers (walk the premises; this is the only
  time this information is available)
- Power state, network connection, UPS presence
- Physical condition, evidence of tampering, dust patterns suggesting recent access

### Step 4 — Interview the custodian

- Who installed it, when, who maintains it
- Who has the admin password (**ask for it and record it** — it may enable live
  acquisition later, and it is far easier to get now than by warrant next month)
- Has the time ever been set or corrected? By whom, when?
- Has anything been exported, deleted, or viewed since the incident?
- Recording mode: continuous, motion-triggered, or scheduled
- Retention period as the custodian understands it

### Step 5 — Capture the on-screen state (if the device is live)

5.1 Photograph or video the recorder's **system time display** as shown in its UI.
5.2 Navigate (view-only) to the device information screen and photograph model,
firmware version, serial, and disk status.
5.3 Photograph the network/time settings screen — **note whether NTP is configured and
whether it shows as synchronised**, and note the configured timezone.
5.4 **Do not** change settings, delete, format, or export at this stage.

### Step 6 — ⭐ REFERENCE-CLOCK CAPTURE ⭐ (the critical step)

> This step costs fifteen seconds and is the difference between a timestamp you can
> defend and a timestamp the defence destroys. **Do not skip it.**

6.1 Take a device showing accurate time — a GPS-synced or NTP-synced phone, with the
seconds visible and the display brightness high.
6.2 Walk to a camera whose field of view you can reach. Hold the display steady, filling
as much of the frame as possible, for **at least 30 seconds**.
6.3 Note on F-1: the camera/channel used, the **exact true UTC/IST start and end times**
of the capture, and the device used as the time reference.
6.4 Repeat on a second camera if practical (gives redundancy if one recording is
unrecoverable).
6.5 If the recorder's clock is grossly wrong, this is now the *only* way to establish
absolute time. If it is roughly right, this confirms and quantifies the residual drift.

Record on F-1 in this exact form:

```
REFERENCE-CLOCK CAPTURE
  Channel used              : ____   Camera location: ______________________
  Reference device          : _________________ (make/model)
  Reference sync source     : [ ] GPS  [ ] NTP  [ ] Other: __________
  True time at start of capture (IST, to the second) : ____:____:____
  True time at end of capture   (IST, to the second) : ____:____:____
  Recorder's own displayed time at that moment       : ____:____:____
  Apparent offset (recorder − true), if computable   : ______________
  Performed by ______________  Witnessed by ______________
```

### Step 7 — Power down

7.1 Prefer a **graceful shutdown** through the device menu where available — it flushes
buffers and closes the current recording cleanly.
7.2 If the menu is inaccessible or password-locked, pull power at the wall. Note which
method was used and why. (An abrupt pull risks losing the final in-progress recording
block; a graceful shutdown writes a shutdown log entry. Both are acceptable; the record
of which one you did is not optional.)
7.3 **If the device cannot be powered down** — hospital, airport, toll plaza, live
critical infrastructure — do **not** power it down. Go to SOP-3 (live logical
acquisition) and record the reason on F-1.

### Step 8 — Seize

8.1 Disconnect cables, labelling each with its port number as you go.
8.2 Seal the recorder in an anti-static bag with a tamper-evident seal; record the seal
number on F-1.
8.3 Seize associated items: the power adapter, any USB stick already containing exports,
manuals, and any written passwords.
8.4 **Preferably seize the whole recorder rather than opening it at the scene.** Open it
only if the recorder itself cannot be transported, and if so, follow SOP-2 Step 2 for
disk removal, at the scene, on camera.
8.5 Complete the seizure memo/panchnama with witness signatures.
8.6 Stop the s. 105 recording only after sealing is complete and announced on camera.

### Step 9 — Transport and hand over

9.1 Transport so that shock and static are controlled; hard disks are mechanical.
9.2 Complete the first chain-of-custody entry on Form F-2 (§Forms) at hand-over.
9.3 Hand F-1, F-2, the seizure memo, and the s. 105 recording to the examiner **together
with the device**. The reference-clock capture data on F-1 is useless if it does not
reach the person doing the analysis.

### SOP-1 completion checklist

- [ ] s. 105 recording made, continuous, covers seizure to sealing
- [ ] Photographs: in-place, front, **rear with cables**, label, monitor
- [ ] F-1 complete, including camera-location map
- [ ] Custodian interviewed; admin password obtained and recorded
- [ ] Device UI time, NTP status, and timezone photographed
- [ ] **⭐ Reference-clock capture performed and times recorded**
- [ ] Power-down method recorded
- [ ] Cables labelled by port; device sealed; seal number recorded
- [ ] Seizure memo signed by witnesses
- [ ] F-2 custody entry made at hand-over

---

## SOP-2 — Laboratory physical acquisition

**Who:** Forensic Examiner.
**Provenance class produced:** A (write-blocked) or B (justified alternative).
**Materials:** hardware write blocker, SATA/power cabling, examination workstation with
SENTINEL, sufficient target storage (≥ 1.2× source capacity), Form F-3.

### Step 1 — Receive and open the case

1.1 Verify the seal is intact; photograph it; record the seal number against F-1.
1.2 Photograph the device as received.
1.3 In SENTINEL: create the case; enter FIR/authority references, examiner identity and
s. 79A notification reference.
1.4 **Attach and hash** the seizure memo, F-1, and the s. 105 seizure video as case
documents (FR-74). The custody chain now starts at the scene, not at your bench.
1.5 Record the custody transfer (F-2 → SENTINEL custody record).

### Step 2 — Remove the disk

2.1 Photograph the chassis before opening; note screw seals or warranty stickers.
2.2 Open under camera; photograph the internal layout **before disconnecting anything**.
2.3 If there are multiple disks: photograph the **bay position and cabling of each**, and
label each disk with its bay number. Disk order matters on multi-disk arrays (FR-08);
losing it can make the array unparseable.
2.4 Photograph each disk's label: make, model, serial, capacity, firmware.
2.5 Record all of it on F-3.
2.6 **Also dump the firmware flash where the team has the capability** (SPI clip or
chip-off). It carries the camera-name-to-channel map, timezone/NTP config, user
accounts, and the device event log (FR-05) — all of which materially improve the report.
If not possible, record that it was not done and why.

### Step 3 — Attach through a write blocker

3.1 Connect the disk to a **hardware write blocker**, then to the workstation.
3.2 Record the write blocker's make, model, and firmware version on F-3.
3.3 In SENTINEL, run pre-flight: it verifies write protection, reads drive identity and
SMART, and detects HPA/DCO (FR-12, FR-13).
3.4 **If write protection cannot be established, stop.** Escalate. Proceed only with a
documented, authorised override, which downgrades the evidence to provenance class B and
says so in the report.
3.5 If HPA or DCO is present, record the hidden-sector count. Remove it only with
authorisation, and only with the removal logged — some vendors keep a backup superblock
in that region, so the hidden area can be the difference between a parse and a carve.

### Step 4 — Image

4.1 Select the format: **E01** for lab exchange, **raw** where downstream tools require
it. Both are acceptable; state which in the report.
4.2 Start acquisition. SENTINEL hashes source and image in one pass and verifies them
against each other (FR-11).
4.3 If read errors occur, let the retry policy run; the bad-sector map is a finding and
goes into the report (FR-14). Do not abort a failing disk's acquisition — a partial
image with a documented bad-sector map is evidence; an abandoned acquisition is nothing.
4.4 On completion, record MD5 and SHA-256 on F-3 and confirm SENTINEL's verification
passed.
4.5 Repeat for every disk in the array.

### Step 5 — Return the original to storage

5.1 Re-bag, re-seal with a new seal, record the new seal number.
5.2 Record the custody transfer to secure storage.
5.3 **All subsequent work is on the image.** The original disk is not touched again.

### SOP-2 completion checklist

- [ ] Seal verified intact and photographed on receipt
- [ ] Case created; F-1, seizure memo, and s. 105 video attached and hashed
- [ ] Internal layout and disk bay positions photographed before disconnection
- [ ] Disk identity recorded; firmware flash dumped or non-dump justified
- [ ] Write blocker used and identified; write protection verified by the tool
- [ ] HPA/DCO checked and recorded
- [ ] Image acquired; source and image hashes match; both recorded
- [ ] Bad sectors, if any, mapped and recorded
- [ ] Original re-sealed with new seal number; custody updated

---

## SOP-3 — Live logical acquisition (device cannot be powered down)

**Provenance class produced:** C. Weaker than class A. Use only when a physical
acquisition is genuinely impossible, and record why on F-3.

1. Record on F-3 the reason physical acquisition is not possible, and who decided.
2. Photograph the device state and its UI system time; note the true time at that
   instant (this is offset method D, doc 1 §4.5).
3. **Perform the reference-clock capture (SOP-1 Step 6) if any camera is reachable** —
   it is still the best offset you can get, and the device is conveniently still running.
4. Connect over the network using the credentials obtained at seizure. Use a direct
   cable or an isolated switch; do not attach the recorder to a general network.
5. In SENTINEL, start live logical acquisition (FR-16) for the required channels and
   time window. The tool hashes continuously and writes a full protocol transcript.
6. **Issue no configuration change, no PTZ command, no deletion, no format.** The tool
   only reads; make sure the operator does too.
7. Where the interface allows, also retrieve the device event log and configuration.
8. Record start and end times, the channels and windows retrieved, the interface used
   (vendor CGI / RTSP / ONVIF Profile G), byte counts, and hashes.
9. If the device becomes available for physical acquisition later, **do it** — the
   class-A image supersedes the class-C extract, and both go in the report.

---

## SOP-4 — Ingest of third-party exports (owner-provided media)

**Provenance class produced:** D. The weakest class. Very common in practice.

1. Photograph the media as received; record who provided it, when, and under what
   authority.
2. Attach through a write blocker. Image the **entire USB/media device**, not just the
   video files — deleted files and the filesystem journal on that stick are themselves
   evidence of what was exported and when.
3. Hash every file on receipt, before any processing.
4. Record in the case: who exported the footage, from which device, when, using what
   method, and whether the source recorder is still available.
5. Parse the export files with the relevant plugin (FR-18).
6. **The report must state plainly** that the original storage was not examined, that
   the completeness of the export cannot be verified, and that footage may exist on the
   source device that was not exported.
7. If the source recorder is still accessible, **recommend in the report that it be
   seized and imaged**. Class D should be a stage, not a destination.

---

## SOP-5 — Examination

### Step 1 — Identify

1.1 Run device identification on the image (FR-01–FR-04).
1.2 Record the reported format family, layout version, confidence, and the matched
signature bytes.
1.3 If multiple candidates are reported, review each and select deliberately; the
override is logged.
1.4 If "unknown", the disk is not necessarily unreadable — proceed to carving mode and
generate the signature dossier (FR-09).
1.5 **Do not assume the format from the brand on the chassis.** A CP Plus front panel
over a Dahua-family disk is the normal case, not an anomaly (doc 4 §1).

### Step 2 — Establish the time model — do this before any analysis

2.1 Enter the timezone from the device configuration (firmware dump or the photographed
settings screen).
2.2 Determine the clock offset using the **highest-ranked available method**:
- **A** — NTP proved synchronised from config and device log → ±1 s
- **B** — reference-clock capture from F-1: locate the capture in the recovered footage,
  read the reference clock in frame, enter the true time → ±1 s
- **C** — an externally timestamped event visible in frame (access-control swipe, POS
  transaction, phone call) → ±2–60 s
- **D** — device RTC read at a noted instant before power-down → ±1 s at that instant

2.3 If **none** is available, set the offset as undetermined. SENTINEL will then present
device-local time only and will not assert absolute time anywhere (FR-53). **Do not
enter an estimated offset to make the output look complete.** An honest "offset
undetermined" survives cross-examination; an unsupported timestamp does not.
2.4 Record the method used and its uncertainty. This is printed verbatim in the report.

### Step 3 — Parse and enumerate

3.1 Run T1 enumeration; review the recording inventory by channel and date.
3.2 Review the **coverage map** (FR-29). High-entropy `unaccounted` regions mean data
the tool could not explain — investigate before concluding.
3.3 Review the device event log for power events, formats, disk errors, and **manual
clock changes** (a clock change invalidates a single global offset across the whole
timeline and must be handled as two segments).

### Step 4 — Recover

4.1 Run T2 (orphan index entries). Review; each is validated against block content.
4.2 Run T3 (signature carve) across the whole image. This takes hours on a large disk;
it is checkpointed and resumable, so start it and continue other work.
4.3 Run T4 if the disk had read errors.
4.4 Review merged results. Note the tier and confidence of each item.
4.5 **Items with confidence below the "time recoverable" threshold are reported as
time-unknown.** Do not assign them a time by inference from neighbouring items.

### Step 5 — Analyse

5.1 Apply the time model; verify a known event lands where it should.
5.2 Build the timeline; review **gaps** (FR-58) — an unexplained gap across all channels
is a finding in its own right and often the most important one in the case.
5.3 Run motion analytics first to reduce the review volume, then object detection on the
motion segments only.
5.4 Review candidate segments manually. **Every analytics hit that enters the report
must have been visually verified by the examiner against the source frame**, and the
report says so.
5.5 Bookmark and annotate relevant segments.
5.6 For multi-device cases, reconcile each device's offset and review the combined
timeline with uncertainty bands. Do not assert an ordering that is not safe at the
stated uncertainty.

### Step 6 — Export

6.1 Export evidence clips as remuxed MP4 (`-c copy`). The payload is bit-identical to
the source; the hash manifest proves it (FR-31, FR-32).
6.2 Where a distributable copy is needed, generate a **derivative** transcode and label
it as such everywhere. Never let a derivative be mistaken for the evidence copy.
6.3 Record every export in the case; each is an audit record.

---

## SOP-6 — Reporting and release

1. Generate the report (FR-80). Review every section.
2. **Read the negative-findings section carefully** (FR-81). It is auto-populated from
   the coverage map, bad-sector map, unparsed structures, empty channels and time gaps.
   You may add to it; you cannot remove from it. If it says something surprising,
   investigate before signing — that section is what a defence expert reads first.
3. Verify the hash table and the audit-chain head digest.
4. Complete and review the **BSA s. 63(4) certificate** annexure (FR-83). Confirm the
   record identification, production particulars, device particulars, and hash values.
5. Confirm the report records tool version, plugin versions, FFmpeg version, and
   analytics model names and hashes (FR-84) — this is what makes the result reproducible
   years later.
6. Sign; obtain countersignature where required.
7. Archive the case container; record its hash; log the custody transfer.
8. Release the report, the evidence clips, and the hash manifest together. **A clip
   released without its manifest is a clip whose integrity nobody can check.**

---

## SOP-7 — Quick reference: what to do when

| Situation | Go to |
|---|---|
| Recorder seized, disk healthy | SOP-1 → SOP-2 → SOP-5 → SOP-6 |
| Recorder cannot be powered down | SOP-1 (steps 1–6) → SOP-3 → SOP-5 → SOP-6 |
| Only an owner-provided USB export | SOP-4 → SOP-5 → SOP-6 |
| Disk physically damaged / not spinning | SOP-2 steps 1–2, then route to hardware data recovery; resume at SOP-2 step 4 with the recovered image |
| Format probes as "unknown" | SOP-5 step 1.4; carving mode; generate dossier; consider doc 4 §11 |
| Clock offset cannot be determined | SOP-5 step 2.3. Report device-local time only. Do not estimate. |
| Multi-disk array | SOP-2 step 2.3 — preserve bay order; image every disk |
| Suspected tampering with the recorder | Preserve, photograph extensively, prioritise the device event log and the gap analysis |

---

## Forms

### Form F-1 — DVR/NVR Seizure Record

```
CASE / FIR NO: ________________  AUTHORITY REF: ________________
DATE: __________  TIME OF SEIZURE: __________  LOCATION: ______________________
SEIZING OFFICER: ______________________  RANK/UNIT: ______________________
WITNESSES: 1) ______________________  2) ______________________

DEVICE
  Type: [ ] DVR  [ ] NVR  [ ] Hybrid      Make: ______________  Model: ______________
  Serial: ______________  Asset tag: ______________
  Channels connected: ____ of ____       Network connected: [ ] Y [ ] N
  UPS present: [ ] Y [ ] N               Physical condition: ______________________
  Evidence of tampering: ______________________________________________

CAMERA MAP  (channel → physical location → coverage)
  Ch __ : ______________________________ : ______________________________
  Ch __ : ______________________________ : ______________________________
  Ch __ : ______________________________ : ______________________________
  (continue on annexure)

DEVICE STATE AT SEIZURE
  Powered on: [ ] Y [ ] N     Recording: [ ] Y [ ] N     Display attached: [ ] Y [ ] N
  Device UI system time shown : ____-__-__ __:__:__
  True time at that moment    : ____-__-__ __:__:__   Source: [ ] GPS [ ] NTP phone
  Timezone configured on device: ______________  NTP: [ ] on [ ] off [ ] unknown
  Firmware version shown: ______________

⭐ REFERENCE-CLOCK CAPTURE  (MANDATORY WHERE ANY CAMERA IS REACHABLE)
  Performed: [ ] Y  [ ] N — if N, reason: ______________________________________
  Channel used: ____   Camera location: ______________________________
  Reference device: ______________  Sync: [ ] GPS [ ] NTP [ ] other: __________
  True start (IST, to the second): __:__:__   True end: __:__:__
  Second capture on channel ____ : [ ] Y [ ] N

CUSTODIAN INTERVIEW
  Custodian name / role: ______________________  Contact: ______________
  Installed by / when: ______________________________________
  Admin username / password obtained: ______________________________________
  Clock ever set or corrected? By whom, when: ______________________________
  Anything exported / deleted / viewed since incident: ______________________
  Recording mode: [ ] continuous [ ] motion [ ] schedule   Retention claimed: ____ days

POWER DOWN
  Method: [ ] graceful shutdown via menu  [ ] power removed at source
  Reason if not graceful: ______________________________________
  Time of power down: __:__:__

SEIZURE
  Cables labelled by port: [ ] Y [ ] N      Seal number: ______________
  Items also seized: [ ] PSU [ ] USB media [ ] manuals [ ] written passwords
                     [ ] other: ______________________________
  BNSS s.105 recording made: [ ] Y [ ] N    Recording ref: ______________
  Photographs taken: [ ] in-place [ ] front [ ] rear-with-cables [ ] label [ ] monitor

SIGNATURES
  Seizing officer: ______________  Witness 1: ______________  Witness 2: ______________
```

### Form F-2 — Chain of Custody

```
CASE NO: ________________   EVIDENCE ITEM ID: ________________
DESCRIPTION: ______________________________________  SEAL NO: ______________

 #  DATE/TIME (IST)   RELEASED BY        RECEIVED BY        PURPOSE           SEAL INTACT  SIGN
 1  ________________  _________________  _________________  ________________  [ ]Y [ ]N   ____
 2  ________________  _________________  _________________  ________________  [ ]Y [ ]N   ____
 3  ________________  _________________  _________________  ________________  [ ]Y [ ]N   ____
 4  ________________  _________________  _________________  ________________  [ ]Y [ ]N   ____

Every physical transfer gets a row. Every row is also entered into the SENTINEL case
custody record. A gap in this form is a gap in the chain.
```

### Form F-3 — Acquisition Record

```
CASE NO: ________________   EXAMINER: ______________________
S.79A NOTIFICATION REF: ______________________   DATE: ______________

SOURCE
  Item ID: ____________  Disk bay position (if array): ____ of ____
  Disk make/model: ______________________  Serial: ______________
  Capacity (label): ____________  Capacity (reported by tool): ____________
  Firmware: ____________  SMART status: ______________
  Firmware flash dumped: [ ] Y [ ] N — if N, reason: ______________________

WRITE PROTECTION
  Hardware write blocker: ______________________ (make/model/fw)
  Write protection verified by tool: [ ] Y [ ] N
  If N — authorised override by: ______________  Justification: ______________
  → PROVENANCE CLASS: [ ] A  [ ] B  [ ] C  [ ] D

HIDDEN AREAS
  HPA present: [ ] Y [ ] N   Hidden sectors: ____________
  DCO present: [ ] Y [ ] N   Hidden sectors: ____________
  Removed for acquisition: [ ] Y [ ] N   Authorised by: ______________

ACQUISITION
  Tool + version: SENTINEL ____________   Format: [ ] raw  [ ] E01  [ ] AFF4
  Start: ____-__-__ __:__:__   End: ____-__-__ __:__:__
  Bytes acquired: ______________   Bad sectors: ______  Bad-sector map ref: __________

  SOURCE  MD5: ________________________________
          SHA-256: ________________________________________________________
  IMAGE   MD5: ________________________________
          SHA-256: ________________________________________________________
  Verification passed: [ ] Y [ ] N

IDENTIFICATION
  Format family: ______________  Layout version: ________  Confidence: ______
  Other candidates: ______________________________________
  Brand inferred: ______________  Evidence for brand: ______________________

TIME MODEL
  Device timezone: ____________
  Offset method: [ ] A NTP  [ ] B reference capture  [ ] C external event
                 [ ] D live RTC  [ ] NONE — offset undetermined
  Measured offset: ______________  Uncertainty: ± ______ s
  Derivation note: ______________________________________________________

  Examiner signature: ______________________
```

---

## Common errors and how this SOP prevents them

| Error seen in practice | Consequence | Prevented by |
|---|---|---|
| Exporting from the live recorder as the primary evidence | Recorder writes during export; no hash of the source; incomplete export | SOP-1 step 5.4, SOP-2 (image the disk) |
| Skipping the reference-clock capture | Absolute time unverifiable forever after power-down | SOP-1 step 6, marked mandatory |
| Not photographing the rear cabling | Channel-to-camera mapping lost | SOP-1 step 2.3 |
| Losing multi-disk bay order | Array may be unparseable | SOP-2 step 2.3 |
| Assuming the format from the brand | Wrong parser, or "unsupported" concluded wrongly | SOP-5 step 1.5, doc 4 §1 |
| Entering an estimated clock offset to fill the field | Fabricated absolute timestamps in a court report | SOP-5 step 2.3 |
| Not running T2/T3 because T1 "found the footage" | Recoverable deleted footage never recovered | SOP-5 step 4 |
| Deleting inconvenient items from the caveats | Undisclosed limitations found by the defence | FR-81 — negative findings cannot be removed |
| Releasing clips without the hash manifest | Integrity uncheckable downstream | SOP-6 step 8 |
| Aborting acquisition of a failing disk | Recoverable evidence abandoned | SOP-2 step 4.3 |
