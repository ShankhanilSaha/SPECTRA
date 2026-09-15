# 01 — Problem Analysis & Technical Writeup

**Project:** SPECTRA — Unified Vendor-Agnostic DVR/NVR Forensic Analysis Platform
**Problem statement:** SIH 2026, NTRO
**Document status:** Baseline v1.0
**Audience:** Everyone on the project. This is the document that explains *why the
thing is hard*. Every design decision in docs 2–7 traces back to a section here.

---

## Table of contents

1. [Domain background](#1-domain-background)
2. [Why this is a real problem and not a tooling preference](#2-why-this-is-a-real-problem-and-not-a-tooling-preference)
3. [Anatomy of a DVR/NVR as a storage device](#3-anatomy-of-a-dvrnvr-as-a-storage-device)
4. [The nine technical challenges, in detail](#4-the-nine-technical-challenges-in-detail)
5. [Legal and evidentiary framework (India)](#5-legal-and-evidentiary-framework-india)
6. [State of the art and gap analysis](#6-state-of-the-art-and-gap-analysis)
7. [Proposed approach](#7-proposed-approach)
8. [Scope boundaries and explicit non-goals](#8-scope-boundaries-and-explicit-non-goals)
9. [Risk register](#9-risk-register)
10. [Glossary](#10-glossary)

---

## 1. Domain background

### 1.1 What a DVR/NVR actually is

A Digital Video Recorder (DVR) is an embedded appliance that takes analogue camera
input (CVBS, or the digital-over-coax HDCVI/TVI/AHD variants), encodes it in hardware
to H.264 or H.265, and writes it continuously to one or more internal SATA hard disks.
A Network Video Recorder (NVR) does the same but receives already-encoded streams from
IP cameras over RTSP/ONVIF and mostly just muxes and writes. Hybrid recorders do both.

Physically it is a low-power SoC — Hisilicon (HiSilicon Hi35xx family dominates the
Chinese OEM market), Novatek, Grain Media, or Ambarella — running a stripped Linux
(kernel 3.x or 4.x, BusyBox userland, SquashFS or JFFS2 root on NAND/SPI flash), with
2–64 GB of firmware flash and 1 TB–48 TB of SATA storage.

The critical property, from a forensic point of view:

> **The video disk is almost never formatted with a general-purpose filesystem.**

The vendor writes its own filesystem — or something that is not a filesystem at all
but a fixed-geometry block allocator with an index — directly onto the raw block
device. There is no ext4 superblock, no NTFS boot sector, no partition table that
`fdisk` recognises. Plugging the disk into a Windows or Linux workstation yields
"unallocated" or a prompt to format. This is deliberate: it lets the recorder write at
sustained sequential speed with no metadata journalling overhead, survive power loss
without fsck, and — as a side effect the vendors are entirely happy with — makes the
data hard for anyone else to read.

### 1.2 Where the evidence comes from

In Indian investigations, CCTV footage appears in effectively every category of case:

| Case type | Typical role of DVR evidence |
|---|---|
| Homicide, assault, dacoity | Establishing presence, sequence, and identity of persons |
| Road accidents / hit-and-run | Vehicle identification, ANPR, speed estimation from frame timing |
| Bank and ATM fraud | Card-skimmer installation, mule identification |
| Corporate theft / IP exfiltration | Server-room and cabin access records |
| Riots and public-order events | Crowd movement, first-aggressor determination |
| Critical infrastructure incidents | Perimeter breach timeline, insider access correlation |
| Custodial and departmental inquiries | Compliance with mandatory recording requirements |

The recorder is seized either as a whole appliance (most common), or the investigator
is handed a USB stick containing vendor-exported clips by the premises owner (common
and evidentially weaker), or a remote export is taken over the network from a live
device that cannot be powered down (a hospital, a toll plaza, an airport).

### 1.3 The OEM landscape relevant to India

Eight vendors are named in the problem statement. They are not eight independent
engineering efforts — that fact is the single biggest lever this project has:

```
                     ┌──────────────────────────────┐
                     │ Dahua Technology (Hangzhou)  │
                     │  DHFS-family FS + DHAV frames│
                     └──────────────┬───────────────┘
                                    │ ODM / rebrand
              ┌─────────────┬───────┴────────┬──────────────┐
              ▼             ▼                ▼              ▼
          CP Plus       Godrej (part)   Honeywell (part)  numerous
          (Aditya       Security Sol.   Performance/HQA   regional
           Infotech)                     series           brands

                     ┌──────────────────────────────┐
                     │ Hikvision (Hangzhou)         │
                     │  HIKBTREE FS + PS/ES payload │
                     └──────────────┬───────────────┘
                                    │ ODM / rebrand
                     ┌──────────────┴──────────────┐
                     ▼                             ▼
                 Godrej (part)              various OEM labels

     Independent stacks:
       Uniview (UNV, Zhejiang)  — own FS, own index
       TP-Link VIGI             — newest entrant, most standards-compliant
       Matrix Comsec (Vadodara) — SATATYA series, indigenous stack
```

**Consequence:** a well-built Dahua plugin plus a well-built Hikvision plugin plausibly
covers 60–75 % of units seized in India by volume. Uniview, TP-Link and Matrix are
three additional plugins. The "support 5–6 OEMs" requirement is therefore achievable
with **five parser implementations**, not eight, provided the architecture treats
"vendor brand" and "storage format family" as separate concepts. It is engineered that
way in [doc 3](03-architecture.md).

---

## 2. Why this is a real problem and not a tooling preference

A common objection: "just use the vendor's own playback software." Here is why that
fails, concretely.

**2.1 Vendor software is a player, not a forensic tool.** It shows you video. It does
not hash, does not log what it did, does not tell you what it *could not* read, does
not expose deleted regions, and frequently transcodes silently on export — producing a
file that is not bit-identical to what the disk held and therefore weak under
cross-examination.

**2.2 Vendor software requires the vendor's hardware.** Dahua's SmartPlayer will not
open a raw disk image; it opens `.dav` files that a working Dahua recorder produced.
If the recorder is smashed, water-damaged, or its board is dead — which is exactly the
situation in the cases that matter — there is no path from the surviving disk to the
video.

**2.3 Vendor export writes to the evidence.** Exporting from a live recorder to USB
causes the recorder to write logs, update indices, and in some models perform
housekeeping deletions. The device is not read-only during its own export.

**2.4 Multi-tool workflow destroys the chain of custody.** A realistic current-day
workflow for a two-recorder case:

```
FTK Imager           → image the disks (or, worse, no imaging at all)
DVR Examiner         → parse Hikvision disk, export clips        [commercial, ~$$$]
Vendor SmartPlayer   → open the Dahua .dav files                 [free, unlogged]
FFmpeg (manual)      → convert .dav / .264 to something playable
VLC / MPC            → view
Excel                → hand-typed timeline of who appeared when
HashCalc / certutil  → hash the exported files (after export, not before)
Word                 → write the report, paste screenshots
```

Seven tools, four of which produce no audit trail, one hand-typed timeline (the single
most error-prone artefact in the entire chain), and hashing applied *after* the
conversion rather than to the original evidence. Every hand-off between those tools is
a point at which defence counsel can ask "what happened to the file here, and who
verified it?" and receive no logged answer.

**2.5 Cost and availability.** The credible commercial option (DVR Examiner by
Salvation Data / Magnet's competitor set) is licensed per-seat at a price point that
puts it in central labs, not in district cyber cells — where the volume actually is.
Sovereignty matters too: for NTRO-adjacent and critical-infrastructure work, sending
disk images through a foreign-vendor closed binary is its own problem.

**2.6 The recovery gap is real evidence lost.** DVRs overwrite oldest-first in a ring.
When a case surfaces three weeks after the incident, the index entries for the relevant
window have usually been unlinked and re-used, but a substantial fraction of the actual
*data blocks* have not yet been physically overwritten, because block reuse is not
strictly LRU across all channels. No commonly available tool carves those. That is
footage that exists on the platter and is never recovered because nobody looks.

---

## 3. Anatomy of a DVR/NVR as a storage device

### 3.1 The two-disk model

Every recorder has two distinct storage domains, and forensics must treat them
separately:

| Domain | Medium | Contents | Forensic value |
|---|---|---|---|
| **Firmware / config** | SPI-NOR or NAND flash on the mainboard, 8 MB–2 GB | Bootloader (U-Boot), kernel, SquashFS root, config partition, user accounts, network settings, **the device RTC offset**, camera names, motion-detection zones, **the system event log** | High. Gives device identity, camera-name-to-channel mapping, clock configuration, and the log of power-cycles, logins, disk format events, and video-loss events. |
| **Video storage** | SATA HDD(s), 1 TB–48 TB, sometimes eSATA/NAS extension | The proprietary filesystem: superblock, index/allocation structures, data blocks holding encoded frames, and (on some) a separate picture/snapshot area | Primary. The footage itself. |

Most existing workflows ignore the firmware flash entirely. That is a mistake: the
camera-name-to-channel mapping and the RTC offset live there, and without them a
timeline says "Channel 5, 14:22" instead of "Rear Gate camera, 14:22 IST ±3 s".

### 3.2 The generic shape of a DVR filesystem

Despite the diversity, essentially all of these formats converge on the same design,
because they are all solving the same problem (sustained sequential write, power-loss
tolerance, cheap seeking to a timestamp):

```
LBA 0
┌──────────────────────────────────────────────────────────────────────┐
│ SUPERBLOCK / MASTER SECTOR                                           │
│  · vendor magic string                                               │
│  · disk capacity, format version, format timestamp                   │
│  · data-block size (typically 64 MB / 256 MB / 1 GB)                  │
│  · total data-block count                                            │
│  · offsets of the index structure(s) and the log area                 │
└──────────────────────────────────────────────────────────────────────┘
┌──────────────────────────────────────────────────────────────────────┐
│ SYSTEM LOG AREA                                                       │
│  · ring of fixed-size log records: power on/off, disk error,          │
│    format, login, video loss, motion event                            │
└──────────────────────────────────────────────────────────────────────┘
┌──────────────────────────────────────────────────────────────────────┐
│ INDEX / ALLOCATION TABLE  (often duplicated for redundancy)            │
│  entry[i] = { data_block_id, channel, start_time, end_time,           │
│               flags (in-use / free / locked / event),                 │
│               stream type, resolution, [frame-offset table ref] }      │
└──────────────────────────────────────────────────────────────────────┘
┌──────────────────────────────────────────────────────────────────────┐
│ DATA BLOCK 0   ── fixed size, one channel's contiguous stream ──      │
│  [frame][frame][frame] … in a proprietary framing container            │
├──────────────────────────────────────────────────────────────────────┤
│ DATA BLOCK 1                                                          │
├──────────────────────────────────────────────────────────────────────┤
│ …                                                                     │
│ DATA BLOCK N-1                    ← allocation wraps around to reuse   │
└──────────────────────────────────────────────────────────────────────┘
```

Once you internalise that picture, the whole parsing problem decomposes into four
questions per vendor, and *only* four:

1. **Where is the superblock and what is its magic?** → device identification
2. **How do I walk the index?** → enumerating recordings
3. **How do I turn (data_block, byte range) into frames?** → the container format
4. **What do the timestamp fields mean?** → epoch, endianness, timezone, packing

This four-question decomposition is what makes a vendor-agnostic plugin contract
possible. It is formalised as the `VendorPlugin` interface in [doc 3 §4](03-architecture.md#4-the-plugin-contract).

### 3.3 The container layer

Inside a data block, frames are not raw Annex-B H.264. Each vendor wraps them:

- **Dahua family (`.dav`, and the on-disk equivalent):** each frame is preceded by a
  fixed header beginning with the ASCII magic `DHAV` and terminated by `dhav`. The
  header carries frame type (I/P/B/audio), channel number, a monotonic frame counter,
  a packed date-time, and the payload length. Extension fields carry resolution and
  frame rate. This is *self-describing at the frame level*, which is the single most
  useful property in the entire project — it means a Dahua data block can be parsed
  with the index completely destroyed, and every recovered frame carries its own
  timestamp and channel. Deleted-footage recovery on Dahua is therefore tractable.

- **Hikvision family:** payload inside a data block is closer to standard MPEG-2
  Program Stream — pack headers `00 00 01 BA`, PES packets `00 00 01 E0` — with
  vendor-specific private-stream headers carrying the per-frame timestamp and channel.
  A separate per-block structure holds a frame-offset table for fast seeking. Carving
  is therefore also tractable (PS pack headers are a strong, frequent signature), but
  channel attribution for orphaned blocks depends on the private headers or on the
  block-header remnant.

- **Uniview / Matrix / Honeywell native:** less publicly documented; see
  [doc 4](04-oem-comparative-analysis.md) for what is known versus what the team must
  reverse-engineer, with the method for doing so.

- **TP-Link VIGI and other modern entrants:** trend towards a real filesystem (ext4)
  holding near-standard MP4/fMP4 segments. Easiest case; handled by the generic plugin
  plus a light index reader.

### 3.4 The ring-buffer overwrite behaviour — and why deleted recovery works

The recorder never "deletes" in the filesystem sense. When space runs out it picks the
oldest data block, marks its index entry free, and overwrites the block with new video.
Two consequences:

1. **A freed index entry is not immediately a destroyed block.** Between the moment the
   entry is freed and the moment the block is physically rewritten, the video is fully
   intact and completely invisible to the vendor's own software. Depending on the
   channel count, per-channel bitrate, and allocation policy, this window can be hours
   to days of footage — and on a disk that was pulled from service (which is the
   forensic case!) it is *permanent*, because nothing is writing any more.

2. **Partial overwrite leaves partial GOPs.** A 256 MB block half-overwritten still
   holds ~128 MB of the old recording at the tail. Frame-level self-description
   (Dahua) or strong start-codes (Hikvision PS) let you recover from the first intact
   I-frame after the overwrite boundary.

This is the mechanism the "improve deleted video recovery" requirement rests on, and it
is the highest-value differentiator in the whole product. It is specified in
[doc 2 FR-40..FR-46](02-prd.md) and designed in [doc 3 §7](03-architecture.md#7-recovery-engine).

---

## 4. The nine technical challenges, in detail

The problem statement lists nine challenges. Each is analysed here with its root cause
and the design response, which is then made concrete as requirements in doc 2.

### 4.1 Non-standard forensic acquisition methods

**Root cause.** There is no accepted answer to "how do you image a DVR?" because there
are four physically different situations and the community has no shared doctrine:

| Situation | What is available | Correct method |
|---|---|---|
| Recorder seized, disk healthy | Pull disk, attach write-blocker | Full physical image of the SATA disk + separate dump of firmware flash |
| Recorder seized, disk fine, no write-blocker on site | Live recorder only | **Do not use the recorder's own export as primary evidence.** Document and defer to lab. |
| Recorder cannot be powered down (hospital, toll, airport) | Network access only | Live logical acquisition over vendor protocol/ONVIF with continuous hashing, explicitly documented as a *logical, non-verifiable-at-source* acquisition |
| Disk physically damaged | Nothing | Route to hardware data-recovery; SPECTRA consumes whatever image comes back |
| Only a USB export exists (owner-provided) | Vendor clip files | Treat as third-party-derived evidence; hash on receipt; parse the container; flag provenance in the report |

**Design response.** Ship acquisition as a first-class module with **four documented
modes**, each with its own SOP ([doc 5](05-sop.md)) and its own provenance class
recorded in the case database, so the report always states *how* the evidence was
obtained and what that implies for weight. Never silently mix classes.

**Secondary root cause: HPA/DCO and the last-sector problem.** DVR disks are commodity
drives. A Host Protected Area or Device Configuration Overlay hides sectors from a
naïve image. Some vendors also place a secondary superblock copy at the *end* of the
disk. If you image only the visible LBA range and the vendor put its backup superblock
in the DCO region, you lose your recovery fallback. The acquisition module must detect
and, on operator authorisation, temporarily disable HPA/DCO — and log that it did.

### 4.2 Proprietary file systems

**Root cause.** Covered in §3. No general-purpose parser exists because the format is
undocumented and changes across firmware major versions.

**Design response.** Two-stage: (a) a **signature scanner** that reads a fixed set of
byte offsets and classifies the disk into a *format family* with a confidence score,
and (b) a **plugin registry** where each format family provides the four operations
from §3.2. A crucial detail: the scanner must classify the **format family**, not the
brand — the plastic on the front says CP Plus, the disk says Dahua. Brand is a
cosmetic attribute derived from the firmware dump or the operator's input; the parser
is chosen by the disk signature alone.

**Handling firmware version drift.** Vendors change structure layouts between major
firmware revisions. The plugin must therefore version its own layout descriptors and,
when it encounters a superblock version it does not know, degrade gracefully to
**carving mode** rather than producing wrong output. Silently mis-parsing is far worse
than admitting ignorance: it produces a confident, wrong timeline that a lab will sign.

### 4.3 Proprietary video formats

**Root cause.** The *codec* is standard (H.264/H.265/rarely MJPEG or H.264+ /
"Smart codec" variants). The *container* is proprietary. Vendors additionally use
non-standard extensions: Hikvision's H.264+ and Dahua's "Smart H.264" apply long-term
reference-frame tricks and background modelling that some decoders mishandle, and a
handful of models apply light obfuscation or per-model header scrambling on export
files.

**Design response.** Remux, do not transcode. The pipeline is:

```
proprietary frame container
   → strip vendor frame headers, keep the elementary stream bytes intact
   → wrap Annex-B / HEVC NALUs in a standard MP4 (or MKV) container
   → attach the original per-frame timestamps as presentation timestamps
   → hash the extracted elementary stream AND the remuxed MP4
```

Because the codec payload is copied byte-for-byte, the exported MP4 is
**bit-faithful to the evidence at the elementary-stream level** and the report can say
so and prove it with a hash of the extracted ES. Transcoding to "make it play
everywhere" is offered only as an explicitly-labelled *derivative* copy, never as the
evidence copy. This distinction is what makes the output defensible.

### 4.4 Difficulty in recovering deleted or damaged recordings

**Root cause.** §3.4. Compounded by: no tool looks at freed index entries; carving
tools (Foremost, Scalpel, PhotoRec) do not know DVR frame signatures and will happily
emit unplayable garbage; and damaged-disk cases produce images with unreadable sector
runs that break naïve sequential parsers.

**Design response — four recovery tiers, run in order, results merged and deduplicated:**

| Tier | Method | Recovers |
|---|---|---|
| **T1 — Live index** | Walk the valid index | Everything the vendor tool would show. Baseline. |
| **T2 — Orphan index** | Walk index entries marked free/deleted but whose data block still validates against the expected magic and timestamp continuity | Recently deleted recordings with full metadata (channel + time known exactly) |
| **T3 — Signature carve** | Scan the entire image for frame magics (`DHAV`, PS pack `00 00 01 BA`, HEVC/AVC start codes) independent of any index; reconstruct GOPs from the first valid I-frame; read channel and time from frame headers where present | Footage whose index is gone entirely, footage in partially overwritten blocks, footage on damaged disks |
| **T4 — Bad-sector tolerant re-scan** | Re-run T3 with zero-filled gaps for unreadable sectors, emitting each contiguous survivable GOP run as its own clip | Footage on failing disks |

Every recovered clip carries a **recovery provenance tag** (T1–T4) and a
**confidence score**, and the report distinguishes them. A T3-carved clip whose
timestamp comes from a frame header is strong; a T3-carved clip with no recoverable
timestamp is presented as "recovered, time unknown, sequence position N in physical
order" and never given a fabricated time.

### 4.5 Inconsistent timestamps

This is the most under-appreciated and most legally dangerous challenge. There are
**six independent sources of timestamp error**, and they compound:

1. **RTC drift.** A DVR's real-time clock is a cheap crystal with a dead backup
   battery in half the units in the field. Drift of several minutes per month is
   routine; units running for years with no NTP are commonly 20–90 minutes wrong. The
   burned-in on-screen display shows the *wrong* time, confidently.
2. **Timezone and DST ambiguity.** The on-disk timestamp may be local, UTC, or local
   with a stored offset. India has no DST, which helps, but imported firmware defaults
   frequently ship as UTC+8 (China) or UTC+0 and are never corrected by the installer.
3. **Epoch and packing variety.** Some vendors store Unix epoch seconds (32-bit LE);
   some store a bit-packed calendar (`yyyyyyyyyyyy mmmm ddddd hhhhh mmmmmm ssssss`
   variants); some store a vendor epoch. Getting the packing wrong yields a plausible
   but wrong date — the worst possible failure.
4. **Cross-device skew.** A case with three recorders from three premises has three
   independently wrong clocks. Correlating "the car passed camera A then camera B"
   across them is meaningless without reconciliation.
5. **Frame-level versus block-level time.** Index entries give a recording's start and
   end; frame headers give per-frame time. They disagree after a power event. The
   frame-level value is authoritative for seeking; the index value is authoritative for
   enumeration.
6. **OSD burn-in versus metadata.** The time drawn into the pixels and the time in the
   metadata are produced by the same wrong clock but can diverge after a manual clock
   change, because some models re-stamp metadata and not the OSD, or vice versa.

**Design response — a formal three-layer time model, applied to every single frame:**

```
t_device   : the raw value as stored on disk, in the vendor's own encoding
             (preserved verbatim, always shown in the report)
      │
      │  + tz_offset          (from firmware config, or operator-supplied)
      ▼
t_local    : wall-clock time as the device believed it
      │
      │  + clock_offset       (measured, see below)  ± uncertainty
      ▼
t_reference: normalised UTC, the ONLY time used for cross-camera correlation
```

`clock_offset` is not guessed. It is **measured** by one of four ranked methods, and
the method used is recorded and printed in the report:

| Rank | Method | Typical uncertainty |
|---|---|---|
| A | Device was NTP-synced (proved from the firmware config + system log) | ±1 s |
| B | **Reference-object capture**: at seizure, the investigator holds a GPS/NTP-synced clock or phone display in front of a live camera and records the moment; the offset is read directly off the recording | ±1 s |
| C | An event with an independently timestamped external record appears in frame (a card swipe with an access-control log, a phone call, a POS transaction) | ±2–60 s |
| D | Device RTC read directly from the live recorder's UI/API at a noted reference instant, before power-down | ±1 s at that instant, extrapolated |
| — | **None available** | **Offset unknown — the report says so and refuses to assert an absolute time** |

Method B is a **procedural** contribution, not a software one, and it belongs in the
seizure SOP ([doc 5 §2](05-sop.md)). It costs the investigator fifteen seconds at the
scene and eliminates the single most common ground for challenging CCTV evidence. Any
serious platform in this space must push it into the field workflow.

The uncertainty is carried through the whole pipeline. The timeline UI draws error bars.
The report never prints an absolute time without its uncertainty and its derivation.

### 4.6 Limited event correlation across cameras

**Root cause.** Correlation is currently done by a human scrubbing N players side by
side and typing into Excel. It does not scale past about four channels and one hour,
and it is unreproducible.

**Design response.** Once §4.5 gives every frame a `t_reference` with uncertainty, a
unified timeline becomes a straightforward join. Provide:

- **Multi-channel timeline view**: N lanes on one time axis, showing recording coverage,
  gaps, motion/event markers from the device's own event records, and analytics hits.
- **Synchronised playback**: play up to 16 channels locked to `t_reference`, not to
  file position — with the drift correction actually applied, which no vendor player does.
- **Gap analysis**: explicitly render *absence* of recording. A missing 40-minute window
  across all channels is itself evidence (of tampering, power loss, or disk failure),
  and it is invisible in per-file workflows.
- **Cross-device correlation**: recorders from different premises on the same axis once
  each has a measured offset.
- **Entity threading (ML-assisted)**: cluster analytics detections (a person, a vehicle,
  a plate) into candidate tracks across cameras, presented as *investigative leads,
  ranked, never as identifications* (see §4.9).

### 4.7 Chain-of-custody challenges

**Root cause.** The chain is maintained on paper while the data moves through seven
unlogged tools. There is no cryptographic link between the paper form and the bytes.

**Design response.** An **append-only, hash-chained audit log** in the case database.
Every operation — image created, plugin selected, recording exported, clip transcoded,
report generated, case opened by user X — is a record containing the previous record's
hash. Any excision or edit of history breaks the chain and is detectable. Structure:

```
record_n = {
  seq, utc_timestamp, operator_id, action, target_object_id,
  parameters, result, object_hash_before, object_hash_after,
  prev_record_sha256
}
digest_n = SHA256( canonical_json(record_n) )
```

The final report embeds the head digest. Optionally the head digest is timestamped by
an RFC 3161 TSA or published to an internal notary, giving an independent
"this report existed at this time" anchor without any blockchain theatre.

Hashing policy: **MD5 and SHA-256 both**, computed concurrently in a single pass over
the data, at four points — source device (where readable), acquired image, each
extracted artefact, each generated report. MD5 is included solely because Indian court
and departmental practice still cites it and cross-referencing with legacy records
requires it; SHA-256 is the one that is asserted as integrity-bearing. The report says
exactly that, so nobody has to defend MD5's collision resistance on the stand.

### 4.8 Dependence on multiple tools and lack of standardised reporting

**Root cause.** Everything above. Reports are hand-written in Word, vary per officer,
and omit the negative findings (what could *not* be recovered) that a defence expert
will immediately ask for.

**Design response.** One tool, one case file, one report generator with a fixed
template covering:

1. Case and authority metadata, examiner identity and qualification
2. Evidence received: device make/model/serial, disk make/model/serial/capacity
3. Acquisition: method, tool version, write-blocker used, HPA/DCO status, hashes
4. Device identification: how the format family was determined, with the raw signature bytes shown
5. Filesystem findings: layout, block size, total/used blocks, format date
6. Recordings recovered: table by channel and time, with recovery tier per item
7. **Negative findings**: unreadable sectors, unparsed regions, channels with no data,
   time windows with no coverage, structures the parser did not understand
8. Timestamp analysis: the three-layer model, the offset determination method, the uncertainty
9. Timeline and correlation findings
10. Analytics findings, clearly labelled as machine-generated leads
11. Integrity: full hash table, audit-log head digest, verification instructions a third party can follow
12. Appendices: SOP references, tool validation reference, glossary, examiner's declaration

Section 7 is the one that separates a forensic report from a demo. It is mandatory and
auto-populated — the tool knows what it failed to read and must say so.

### 4.9 Limited use of intelligent video analytics

**Root cause.** Analytics in this space are either absent or, worse, a cloud API call
that ships evidence to a third-party server.

**Design response.** Analytics run **fully offline, on-device, in a sandbox with no
network egress.** This is non-negotiable for evidence handling and for NTRO-context
work. Models are pinned by hash and their version is recorded in the report so a result
is reproducible years later.

Capabilities, in build-priority order:

1. **Motion / activity detection** — cheap, robust, and the highest-value feature by
   far. Reduces 720 hours of footage to the 40 minutes containing movement. This alone
   delivers most of the "reduce analysis time" requirement. Frame-differencing plus
   region masking; no ML needed.
2. **Object detection and classification** — person / vehicle / two-wheeler / bag, via
   a pinned YOLO-class model exported to ONNX, run on CPU with GPU optional.
3. **Person and vehicle re-identification** — appearance-embedding clustering to thread
   the same entity across cameras.
4. **Face detection and clustering** — detect faces, cluster by embedding, let the
   investigator label a cluster. **Detection and clustering only.**
5. **ANPR** — Indian plate formats, high value for vehicle cases.

**The hard ethical and evidentiary line, stated in the product and in the report:**

> Analytics output is an **investigative lead-generation aid**. It is never an
> identification, never an assertion of identity, and never admissible as the basis of
> a conclusion in itself. Every analytics hit in the report carries its model name,
> model hash, confidence score, and the sentence "machine-generated; requires human
> verification against the source frame." The tool ships **no face-recognition-against-a-
> watchlist capability**, no identity database, and no cloud connectivity.

This is both the right call and the defensible one. A tool that claimed to identify
people from CCTV would be attacked on accuracy grounds in the first hearing and would
raise privacy issues that have no place in a hackathon deliverable. Reducing 30 days of
footage to 12 candidate clips is the actual value, and it is uncontroversial.

---

## 5. Legal and evidentiary framework (India)

Design constraints, not background reading. Every one of these maps to a feature.

### 5.1 Bharatiya Sakshya Adhiniyam, 2023 (BSA) — electronic records

The BSA replaced the Indian Evidence Act, 1872 with effect from 1 July 2024. For this
project the operative provisions are:

- **s. 61** — electronic records are not to be denied admissibility merely for being
  electronic.
- **s. 63** — the successor to the old IEA s. 65B. Electronic records are admissible as
  documents subject to the conditions in s. 63(2) and the **certificate under s. 63(4)**,
  which must identify the electronic record, describe the manner of its production,
  give particulars of the device involved, and be signed by the person in charge of the
  device and by an expert. The BSA's schedule prescribes a certificate format that
  includes **hash values of the electronic record** (the format contemplates hashes such
  as SHA-256, SHA-1 and MD5).

**→ Product requirement:** SPECTRA must generate a **pre-filled s. 63(4) certificate**
as a report annexure, populated with the record identification, the acquisition
particulars, the device particulars, and the hash values it computed. This is a
concrete, high-scoring, low-effort deliverable that directly serves the user, and it is
the reason the platform computes MD5 alongside SHA-256.

- **s. 39(2)** — expert opinion on electronic evidence; supports the examiner's
  declaration section of the report.

### 5.2 Bharatiya Nagarik Suraksha Sanhita, 2023 (BNSS)

- **s. 105** — mandates **audio-video electronic recording of search and seizure**
  operations. The seizure of the DVR itself must be recorded, and that recording is
  part of the case file.
- **s. 176(3)** — forensic-expert attendance at the scene for offences punishable by
  seven years or more.

**→ Product requirement:** the case record must accept and hash the seizure video and
the seizure memo (panchnama) as case attachments, so the custody chain starts at the
scene and not at the lab bench.

### 5.3 Information Technology Act, 2000

- **s. 79A** — designation of Examiners of Electronic Evidence. Reports intended for
  court are issued by notified examiners; the tool supports the examiner, it does not
  replace the notification.

**→ Product requirement:** the report carries examiner identity, designation, and
notification reference fields.

### 5.4 Practice guidance

- **CCTNS / CFSL / NCRB** procedural guidance on seizure and handling of digital evidence.
- **Supreme Court**, *Arjun Panditrao Khotkar v. Kailash Kushanrao Gorantyal* (2020) —
  established the mandatory nature of the (then) s. 65B certificate. The BSA has
  codified the requirement; the practical lesson is unchanged: **the certificate is not
  optional and a missing one has sunk cases.** Automating it is genuine user value.
- The tool's own validation report ([doc 6](06-validation-plan.md)) exists so that the
  examiner can answer "has your tool been tested?" — a question that has ended
  cross-examinations.

### 5.5 Data protection

The **Digital Personal Data Protection Act, 2023** applies to the personal data in the
footage. Law-enforcement processing is subject to exemptions under s. 17, but the design
posture stays conservative: local-only processing, no telemetry, no cloud, encrypted
case containers at rest, role-based access, and the audit log recording every access to
case media. This is also simply good engineering.

---

## 6. State of the art and gap analysis

| Tool | What it does | Where it stops |
|---|---|---|
| **DVR Examiner** (Salvation Data / DME) | The reference commercial product. Broad vendor filesystem support, clip export, some deleted recovery | Closed-source, per-seat licence out of reach for district units, foreign vendor, limited analytics, limited Indian-specific vendors (Matrix, CP Plus variants), no BSA s.63 certificate, no cross-device time reconciliation |
| **Vendor tools** (SmartPLSS, VSPlayer, Dahua SmartPlayer, Matrix/Uniview players) | Play their own exports | Not forensic. No hashing, no logging, no raw-disk support, no recovery, no report. |
| **Amped FIVE / Authenticate** | Excellent at video *enhancement* and authentication | Consumes files; does not acquire or parse DVR filesystems. Complementary, not competing. |
| **FTK / EnCase / Autopsy / X-Ways** | General digital forensics; excellent imaging and case management | No DVR filesystem parsers. An Autopsy ingest module for DVRs does not exist. |
| **Foremost / Scalpel / PhotoRec** | Generic file carving | No DVR frame signatures; output is unplayable fragments with no timestamps or channel attribution |
| **Academic work** (DFRWS/FSI papers on HIKBTREE, Dahua DHFS, DVR carving) | Documents specific formats well | Research artefacts, not maintained tools; one vendor per paper; no unified workflow |

### The gap, stated precisely

There is no **open, auditable, India-focused, unified** tool that spans
**acquisition → parsing → recovery → time normalisation → correlation → analytics →
legally-formatted reporting** for the DVR/NVR brands actually seized in India, with the
chain-of-custody rigour and the BSA-compliant output that Indian courts now require.

SPECTRA targets exactly that gap. It does not need to beat DVR Examiner on the number
of supported firmware revisions — it cannot, on a hackathon timeline. It beats it on
being open and auditable, on carving tiers T2–T4, on the measured-clock-offset time
model, on cross-device correlation, on offline analytics, and on producing a report
that an Indian court will accept without rework.

---

## 7. Proposed approach

### 7.1 Design principles

1. **Read-only by construction.** No code path in the product opens evidence for
   writing. Enforced at the I/O layer, not by convention.
2. **Never fabricate.** Unknown timestamp → say unknown. Unparsed region → report it.
   Low-confidence identification → present as a lead with its score. A blank in a
   forensic report is a finding; a guess is a liability.
3. **Format family, not brand.** Parser selection is driven by disk bytes.
4. **Remux, never transcode, for the evidence copy.**
5. **Everything is hashed and everything is logged**, in one append-only chain.
6. **Offline by default.** No network egress from the analysis host. Analytics models
   pinned by hash and shipped locally.
7. **Degrade loudly.** An unrecognised firmware version drops to carving mode and says
   so. It never guesses a layout.
8. **The plugin is the product.** New vendor support must be one new file implementing
   one interface, with no changes to the core.

### 7.2 Module map

```
                       ┌───────────────────────────────┐
                       │        Case Manager           │
                       │  case DB · audit chain · CoC  │
                       └───────────────┬───────────────┘
                                       │ every module writes here
  ┌───────────┬───────────┬────────────┼────────────┬───────────┬──────────┐
  ▼           ▼           ▼            ▼            ▼           ▼          ▼
┌──────┐  ┌────────┐  ┌────────┐  ┌─────────┐  ┌─────────┐ ┌───────┐ ┌────────┐
│Device│  │Acquis- │  │FS/     │  │Recovery │  │Timeline │ │  ML   │ │Report  │
│Ident.│→ │ition   │→ │Format  │→ │T1–T4    │→ │& Corre- │→│Analy- │→│Gener-  │
│      │  │        │  │Parsing │  │         │  │lation   │ │tics   │ │ator    │
└──────┘  └────────┘  └────────┘  └─────────┘  └─────────┘ └───────┘ └────────┘
  §4.2      §4.1        §4.2/4.3     §4.4         §4.5/4.6    §4.9      §4.7/4.8
```

These are exactly the seven modules named in the problem statement, plus the Case
Manager that binds them. Detailed design in [doc 3](03-architecture.md).

### 7.3 Technology choices and why

| Layer | Choice | Rationale |
|---|---|---|
| Core language | **Python 3.11+** | Fast to write parsers in; `struct`/`mmap` handle binary work fine; the bottleneck is disk I/O and FFmpeg, not the interpreter. Drop to Cython/Rust only for the carve scan if profiling demands it. |
| Binary parsing | `struct`, `mmap`, `construct` (optional) | Stdlib does 95 % of it. |
| Imaging | `dd`/`dc3dd` semantics implemented in-process; **libewf** (`ewfacquire`/`pyewf`) for E01 | E01 is what Indian labs already exchange. Raw + E01 covers everything real. AFF4 optional. |
| Hashing | `hashlib`, single-pass dual MD5+SHA-256 | Stdlib. |
| Video remux/transcode | **FFmpeg** (subprocess, pinned version, version recorded in report) | The only sane answer. Remux with `-c copy`. |
| Case store | **SQLite** (WAL) + content-addressed file store | One file per case, portable, no server, well-understood by courts and labs. |
| Analytics | **ONNX Runtime** + pinned YOLO-class detector; OpenCV for motion | CPU-first, GPU optional, no cloud, reproducible via model hash. |
| GUI | **PySide6/Qt** desktop (offline-first) | Evidence machines are air-gapped. A desktop app is the correct shape; a browser app implies a server. |
| Reports | Jinja2 → HTML → **WeasyPrint** → PDF/A | Deterministic, no Word dependency, archival format. |
| Packaging | PyInstaller single binary + offline wheel bundle | Air-gapped lab installation must not require pip. |

### 7.4 Build sequence (rationale in [doc 8](08-demo-and-deliverables.md))

```
Phase 0  Case manager + audit chain + hashing            ← everything depends on it
Phase 1  Device identification (signature scanner)       ← cheap, demoable, unblocks all
Phase 2  Acquisition (raw + E01, write-block verify)
Phase 3  Dahua/CP Plus plugin  (T1 parse + MP4 remux)    ← widest coverage first
Phase 4  Hikvision plugin      (T1 parse + MP4 remux)
Phase 5  Recovery T2 + T3                                ← the differentiator
Phase 6  Timestamp normalisation + multi-camera timeline
Phase 7  Report generator + BSA s.63(4) certificate
Phase 8  Motion + object analytics
Phase 9  Uniview / TP-Link VIGI / Matrix plugins
Phase 10 Validation runs, SOP finalisation, user manual
```

Note that **Phase 7 comes before analytics**. A tool that parses two vendors and
produces a court-ready report is a product. A tool with face detection and no report is
a demo.

---

## 8. Scope boundaries and explicit non-goals

Stating these prevents scope creep and pre-empts the evaluator's "what about…?"

**In scope:** post-mortem analysis of disks/images from the eight named OEM families;
four acquisition modes; four recovery tiers; timestamp normalisation; cross-camera and
cross-device correlation; offline analytics; BSA-compliant reporting; SOPs and
validation.

**Explicitly out of scope, and why:**

| Non-goal | Reason |
|---|---|
| Face **recognition** against a watchlist / identity database | Accuracy, privacy, and admissibility. Detection + clustering only. §4.9 |
| Video **enhancement** (super-resolution, deblurring) | A different discipline; Amped FIVE-class tooling; and generative enhancement is evidentially dangerous |
| Deepfake / tamper detection of the video content | Valuable but a separate research problem. Structural tamper detection (gaps, index inconsistency, re-encode traces at the container level) **is** in scope; pixel-level synthetic-media detection is not |
| Physical data recovery from failed platters | Cleanroom hardware problem. SPECTRA consumes the resulting image |
| Firmware exploitation / password bypass on live devices | Legally fraught, model-specific, and not needed when you have the disk |
| Real-time / live monitoring VMS features | Different product entirely |
| Cloud storage, cloud analytics, telemetry | Deliberate architectural exclusion, §7.1 |
| Decrypting vendor-encrypted disks where a key is genuinely required | Where a vendor uses real crypto with a device-bound key, the honest answer is that the disk needs the device or the key. The tool detects and reports this rather than pretending |

---

## 9. Risk register

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| R1 | **No access to real DVR hardware for format work** | High | Critical | Buy/borrow the cheapest available Dahua-family and Hikvision-family units early; source second-hand recorders and pulled disks; use published research for structure hypotheses; build the synthetic-image generator ([doc 6 §3](06-validation-plan.md)) so parser development is not blocked on hardware |
| R2 | Firmware version drift breaks a parser | High | Medium | Version-tagged layout descriptors; graceful degrade to carving; fixture corpus per firmware version |
| R3 | A vendor format resists reverse engineering in the time available | Medium | Medium | Tiered commitment: full parse for Dahua + Hikvision families; carving-only support (still genuinely useful) for the rest; be explicit in the report about which level applied |
| R4 | Legal challenge to tool-produced evidence | Medium | High | Validation report (doc 6), SOPs (doc 5), audit chain, negative findings, BSA s.63(4) certificate, open source for independent audit |
| R5 | Analytics false positives mislead an investigation | Medium | High | Lead-only framing enforced in UI and report text; confidence scores mandatory; human-verification statement on every hit |
| R6 | Performance unacceptable on multi-TB images | Medium | Medium | Streaming/`mmap` I/O, no full-image buffering, parallel carve workers, resumable jobs with checkpoints; target throughput stated as an NFR in doc 2 |
| R7 | Scope creep sinks the timeline | High | High | The phase order in §7.4 and the non-goals in §8 are contractual within the team |
| R8 | Encrypted or genuinely obfuscated storage on some models | Low–Medium | Medium | Detect and report honestly (§8); do not burn the timeline on it |

---

## 10. Glossary

| Term | Meaning |
|---|---|
| **AFF4 / E01 / raw (dd)** | Forensic image container formats. E01 (EnCase) supports compression and embedded hashes and is the common exchange format in Indian labs |
| **Annex-B** | The byte-stream format for H.264/H.265 NAL units, using `00 00 00 01` start codes |
| **Carving** | Recovering data by scanning for content signatures rather than reading filesystem metadata |
| **Chain of custody (CoC)** | The documented, unbroken record of who held/handled evidence and what they did to it |
| **DCO / HPA** | Device Configuration Overlay / Host Protected Area — ATA mechanisms that hide sectors from normal addressing |
| **DHAV** | The frame magic of the Dahua-family container format |
| **Data block** | The fixed-size allocation unit (64 MB–1 GB) a DVR filesystem writes video into |
| **ES (elementary stream)** | The raw coded video bitstream, without any container |
| **GOP** | Group of Pictures — an I-frame and the P/B frames that depend on it. The smallest independently decodable unit |
| **HIKBTREE** | The index structure of the Hikvision-family disk format |
| **Orphan index entry** | An index entry marked free/deleted whose data block has not yet been overwritten. Tier-2 recovery target |
| **Remux** | Re-containerising a bitstream without re-encoding it. Preserves the payload bit-for-bit |
| **RTC** | Real-time clock — the battery-backed clock inside the recorder; the source of most timestamp error |
| **Ring / circular overwrite** | The DVR storage policy of overwriting the oldest data when full |
| **`t_device` / `t_local` / `t_reference`** | The three-layer time model of §4.5 |
| **Write blocker** | Hardware or software that permits reads but physically/logically prevents writes to evidence media |

---

## Traceability

| §  | Challenge | Requirements | Design |
|---|---|---|---|
| 4.1 | Acquisition | FR-10..FR-19 | doc 3 §5 |
| 4.2 | Filesystems | FR-01..FR-09, FR-20..FR-29 | doc 3 §4, §6 |
| 4.3 | Video formats | FR-30..FR-36 | doc 3 §6.3 |
| 4.4 | Recovery | FR-40..FR-46 | doc 3 §7 |
| 4.5 | Timestamps | FR-50..FR-56 | doc 3 §8 |
| 4.6 | Correlation | FR-57..FR-62 | doc 3 §8.3 |
| 4.7 | Chain of custody | FR-70..FR-76 | doc 3 §3 |
| 4.8 | Reporting | FR-80..FR-88 | doc 3 §10 |
| 4.9 | Analytics | FR-90..FR-97 | doc 3 §9 |
