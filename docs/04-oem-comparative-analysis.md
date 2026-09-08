# 04 — DVR/NVR OEM Comparative Analysis

**Named deliverable** of the problem statement.
**Depends on:** [doc 1 §3](01-problem-analysis.md#3-anatomy-of-a-dvrnvr-as-a-storage-device)

---

## 0. How to read this document — confidence labelling

Vendor storage formats are undocumented. Every structural claim below carries a
confidence label. **Do not implement against a claim without first confirming it on a
real image**, and do not let any of it into a court report as established fact until
the corresponding fixture in [doc 6](06-validation-plan.md) passes.

| Label | Meaning |
|---|---|
| **[C]** | **Confirmed** — publicly documented in peer-reviewed forensic literature and/or verified by this team against a real image. Safe to implement. |
| **[R]** | **Reported** — consistently described in public research, vendor SDK docs, or practitioner sources, but not yet verified by this team. Implement behind a probe that fails loudly if wrong. |
| **[H]** | **Hypothesis** — inferred from architectural family, ODM relationships, or partial observation. Must be verified before any implementation. |
| **[U]** | **Unknown** — no reliable information. Requires the reverse-engineering procedure in §11. |

At time of writing, everything not marked **[C]** must be treated as a task, not a
fact. §12 tracks what needs to move to **[C]** and by when.

---

## 1. Executive summary — the leverage

The eight named OEMs are **not eight independent formats**. They collapse into roughly
**five storage-format families**:

```
FAMILY 1 — Dahua-derived           FAMILY 2 — Hikvision-derived
  Dahua Technology            [C]    Hikvision                    [C]
  CP Plus (Aditya Infotech)   [R]    Godrej (some SKUs)           [H]
  Godrej (some SKUs)          [H]    various ODM labels           [H]
  Honeywell (some SKUs)       [H]
                                   FAMILY 3 — Uniview (UNV)       [R]
FAMILY 4 — TP-Link VIGI      [R]   FAMILY 5 — Matrix SATATYA      [U]
  (standards-leaning)                (indigenous stack)
                                   FAMILY 6 — Honeywell native    [U]
                                     (non-Dahua SKUs)
```

**Consequence for the project:** building Family 1 and Family 2 well plausibly covers a
large majority of units seized in India by volume. Adding Families 3–5 satisfies the
"5–6 OEM" requirement. This is why [doc 3](03-architecture.md) keys the parser on the
**disk signature**, never on the brand on the front panel.

**Consequence for investigators, and it belongs in the SOP:** never assume the format
from the badge. A CP Plus unit and a Dahua unit may produce byte-identical disk
layouts; a Godrej unit may be either family depending on the SKU and year. Probe first.

---

## 2. The master comparison matrix

| Attribute | Dahua family | Hikvision family | Uniview | TP-Link VIGI | Matrix SATATYA | Honeywell native |
|---|---|---|---|---|---|---|
| **Origin** | Hangzhou, CN | Hangzhou, CN | Zhejiang, CN | Shenzhen, CN | Vadodara, IN | US/multi |
| **Rebrands under it** | CP Plus, parts of Godrej & Honeywell | parts of Godrej, various | few | none | none | — |
| **On-disk FS** | Proprietary, raw block device **[C]** | Proprietary (HIKBTREE) **[C]** | Proprietary **[R]** | Trends to ext4 + files **[R]** | Proprietary **[U]** | Varies; some ext3-based **[H]** |
| **Superblock magic** | Vendor magic near LBA 0 **[R]** | `HIKVISION@HANGZHOU` in the master sector **[C]** | Vendor magic **[H]** | Standard FS superblock **[R]** | **[U]** | **[U]** |
| **Index structure** | Block-index table, duplicated **[R]** | HIKBTREE (page-list + pages), two copies **[C]** | Index table **[H]** | Filesystem directory **[R]** | **[U]** | **[U]** |
| **Data-block size** | Large fixed blocks **[R]** | Commonly 256 MB **[R]** | Fixed blocks **[H]** | n/a (files) **[R]** | **[U]** | **[U]** |
| **Frame container** | `DHAV` … `dhav` per-frame framing **[C]** | MPEG-PS-like (`00 00 01 BA` packs) + private headers **[C]** | Vendor framing **[H]** | MP4/fMP4 **[R]** | **[U]** | **[U]** |
| **Per-frame self-describing?** | **Yes** — channel + time in every frame header **[C]** | Partly — private headers carry time **[R]** | **[U]** | No (file-level) | **[U]** | **[U]** |
| **Codecs** | H.264, H.265, Smart H.264/265 **[C]** | H.264, H.265, H.264+/H.265+ **[C]** | H.264/H.265 **[R]** | H.264/H.265 **[R]** | H.264/H.265 **[R]** | H.264 **[R]** |
| **Export file ext.** | `.dav` **[C]** | `.mp4`, `.264`, player-bundled exe **[C]** | `.mp4`/`.ts` **[R]** | `.mp4` **[R]** | vendor **[U]** | vendor **[U]** |
| **Timestamp storage** | Packed date-time in frame header **[R]** | Epoch-style in index entries **[R]** | **[U]** | Standard MP4 + FS mtime **[R]** | **[U]** | **[U]** |
| **System log on disk** | Yes **[R]** | Yes, dedicated area **[C]** | **[H]** | Files **[R]** | **[U]** | **[U]** |
| **T1 (index) feasibility** | High | High | Medium | High | Low until RE'd | Low until RE'd |
| **T2 (orphan) feasibility** | High | High | Medium | Low (FS journal instead) | **[U]** | **[U]** |
| **T3 (carve) feasibility** | **Very high** — `DHAV` is a strong, frequent, self-describing signature | **High** — PS pack headers are frequent; time from private headers | Medium | Medium (`ftyp`/`moof` carving) | Medium (raw H.264 start-code carving always works) | Medium |
| **Overall difficulty** | **Low–Medium** | **Medium** | Medium–High | **Low** | High | High |
| **Build priority** | **1** | **2** | 4 | 3 | 5 | 6 |

---

## 3. Family 1 — Dahua Technology (and CP Plus, Dahua-ODM Godrej/Honeywell)

**Why build this first.** Largest installed base in India once rebrands are counted, and
— critically — the frame container is **self-describing at the frame level**, which
makes deleted-footage carving (the project's headline differentiator) work far better
here than anywhere else.

### 3.1 Storage

- Proprietary layout written directly to the raw block device; no recognisable
  partition table or general-purpose filesystem. **[C]**
- Superblock/vendor structure in the first sectors carrying capacity, block geometry
  and index pointers. **[R]** — *exact offsets and field layout must be confirmed on a
  real image before implementation.*
- Fixed-size data blocks allocated per channel, with a block-index table (duplicated for
  redundancy) mapping block → {channel, start time, end time, in-use flag}. **[R]**
- Circular overwrite of the oldest block when full. **[C]** (behavioural, observable)

### 3.2 Container — the `DHAV` frame format

The single most useful format in this project. **[C]** for the framing concept and
magics; **[R]** for exact field offsets.

```
┌──────────────────────────────────────────────────────────────────────┐
│ 'D' 'H' 'A' 'V'      ── frame start magic                    [C]     │
│ frame type           ── I / P / B / audio / subtitle         [R]     │
│ channel number                                               [R]     │
│ frame sub-number / sequence counter                          [R]     │
│ total frame length (including header and trailer)            [R]     │
│ packed date-time (year/month/day/hour/min/sec)               [R]     │
│ millisecond / timestamp extension                            [R]     │
│ extended header block: resolution, fps, codec id             [R]     │
│ ──────────────── payload: H.264 / H.265 elementary stream ────────── │
│ trailing length field                                        [R]     │
│ 'd' 'h' 'a' 'v'      ── frame end magic                      [C]     │
└──────────────────────────────────────────────────────────────────────┘
```

**Forensic implications, in order of importance:**

1. **Carving works without any index.** Scan for `DHAV`, validate that the length field
   lands on a matching `dhav`, and you have a frame — with its channel and its
   timestamp. This is Tier-3 recovery at near-Tier-1 quality. No other family in this
   list is this friendly.
2. **The length + end-magic pair is a self-validating signature**, which kills the
   false-positive problem that makes generic carvers useless on video.
3. **Partially overwritten blocks are still productive** — resume from the next `DHAV`
   after the overwrite boundary, trim to the first I-frame, emit.
4. **Timestamps are per-frame**, so a recovered fragment carries its own time even with
   the index destroyed.

### 3.3 CP Plus, Godrej, Honeywell under this family

CP Plus (Aditya Infotech) is the dominant Indian brand and is widely understood to ship
Dahua-ODM hardware and firmware. **[R]** Its disks are expected to be byte-compatible
with the Dahua family; the differences that do exist are cosmetic (branding strings in
firmware, model naming, UI). **[H]**

Godrej Security Solutions and Honeywell both source across multiple ODMs and across
years; individual SKUs may be Dahua-derived, Hikvision-derived, or something else. **[H]**
**The only correct procedure is to probe the disk.** §11 covers what to do when a
Godrej or Honeywell unit probes as unknown.

### 3.4 Implementation notes

- Probe on the `DHAV`/`dhav` pair in the data area **as a fallback** even if the
  superblock is unrecognised — a Dahua-family disk with a damaged or unknown-version
  superblock is still fully carvable. This makes the plugin robust to firmware drift
  (doc 1 §4.2), which matters more here than anywhere else.
- Field offsets are firmware-version-dependent. Version the layout descriptor; on an
  unknown version drop to carving rather than mis-parsing.
- Vendor codec variants ("Smart H.264/H.265") apply reference-frame and background-model
  tricks; detect from SPS/SEI and warn where the bundled FFmpeg cannot decode cleanly
  (FR-35).

---

## 4. Family 2 — Hikvision (and Hikvision-ODM rebrands)

The **best publicly documented** DVR format — there is peer-reviewed forensic literature
on it — which makes it the lowest-risk second target.

### 4.1 Storage

- Master sector at the start of the disk containing the ASCII signature
  **`HIKVISION@HANGZHOU`**. **[C]** This is the single most reliable identification
  signature in the whole project.
- The master sector carries disk capacity, the offset and size of the system log area,
  the offset of the video data area, the data-block size, the total data-block count,
  and the offsets/sizes of **two HIKBTREE structures** (primary and backup). **[C]** for
  the set of fields; **[R]** for exact offsets, which vary across format versions.
- **HIKBTREE** — signature `HIKBTREE` **[C]** — is a page-list plus pages structure whose
  entries map data blocks to {channel, start time, end time, flags}. **[R]**
- Data blocks are commonly 256 MB. **[R]**
- Circular overwrite. **[C]**

### 4.2 Container

Payload inside a data block is close to **MPEG-2 Program Stream**: pack headers
`00 00 01 BA`, PES `00 00 01 E0`, with vendor private-stream headers carrying per-frame
timestamp and channel. **[C]** for the PS-like structure; **[R]** for the private-header
layout. Some models/firmwares write closer to a raw elementary stream with a separate
frame-offset table per block. **[R]**

**Forensic implications:**

1. `HIKVISION@HANGZHOU` gives near-certain identification at essentially zero cost.
2. Two HIKBTREE copies means **index redundancy**: if the primary is corrupt, the backup
   often survives. The plugin must try both and report which it used — that is a
   reportable finding.
3. PS pack headers are frequent and strong, so T3 carving works well; channel
   attribution for orphaned blocks depends on the private headers or on data-block
   header remnants, so some carved Hikvision items will legitimately be
   "channel unknown".
4. **H.264+/H.265+** are real decode hazards. Detect and report rather than emitting
   corrupt frames.

### 4.3 Implementation notes

- Implement the master-sector reader and both HIKBTREE readers first; enumeration
  (T1/T2) is then straightforward.
- Validate every orphan entry against the block's actual content before trusting it
  (doc 3 §7.1) — Hikvision's large block size means a stale entry can point at a fully
  rewritten 256 MB block.
- Record which HIKBTREE copy was used, and any divergence between them, as a finding.

---

## 5. Family 3 — Uniview (UNV)

Independent Chinese stack, meaningful and growing presence in India. Proprietary
on-disk format with its own index. **[R]** Public forensic documentation is thin
compared with Dahua and Hikvision; expect real reverse-engineering work (§11). **[U]**
for structure details.

Exports commonly land as `.mp4` or MPEG-TS **[R]**, which makes the "owner handed over
a USB stick" case (provenance class D) easy even before the on-disk parser exists —
worth noting because it means partial support delivers value early.

**Build priority 4.** Ship carving-only support first: raw H.264/H.265 start-code
carving plus, once identified, the vendor frame signature.

---

## 6. Family 4 — TP-Link VIGI

Newest entrant of the eight and, from a forensic standpoint, the friendliest. The
product line trends towards standard filesystems (ext4) holding near-standard MP4/fMP4
segment files. **[R]**

**Implications:**
- The **generic filesystem plugin** (`generic_fs.py`) plus a light index/naming reader
  may cover it almost entirely — low effort, real coverage, good ROI.
- Deleted recovery shifts from DVR-style orphan-index work to **conventional filesystem
  forensics**: ext4 journal analysis, inode recovery, and `ftyp`/`moof` carving. Existing,
  well-understood techniques apply.
- Timestamps come from both MP4 metadata and filesystem mtimes; they can disagree, and
  the disagreement itself is a finding.

**Build priority 3** — cheap coverage, and it exercises the generic-FS path that also
serves other standards-based recorders.

---

## 7. Family 5 — Matrix (SATATYA)

Indigenous Indian manufacturer (Matrix Comsec, Vadodara). Proprietary stack. **[U]** on
essentially all structural detail — there is little public forensic research.

**Strategic value out of proportion to its market share:** it is an Indian product, in
an Indian government-sponsored problem statement, and it is the one family where an
existing foreign commercial tool is least likely to have good coverage. Genuine
reverse-engineering work here is the most defensible novelty claim the project can make.

**Approach:**
1. Obtain a unit or a pulled disk (§11 step 0 — this is the gating dependency).
2. Full §11 reverse-engineering procedure.
3. Ship carving-only support first (raw H.264 start-code carving works on any recorder
   and is honest, useful output), then the index parser as understanding matures.

**Build priority 5**, but start the *hardware acquisition* for it early, because the
lead time on obtaining the device is the real constraint, not the coding.

---

## 8. Family 6 — Honeywell native

Honeywell sells across multiple ODM sources and generations. Some SKUs are Dahua-derived
**[H]**; some appear to use ext3-based layouts with a proprietary allocation scheme
**[H]**; some are entirely unknown **[U]**.

**Approach:** probe-first, always. The identification engine will route Dahua-derived
Honeywell units to the Dahua plugin automatically — which is the whole point of keying
on format family. Native Honeywell formats are **build priority 6** and ship as
carving-only unless a unit becomes available.

---

## 9. Cross-cutting comparison: what actually differs

Boiling the matrix down to what a parser author cares about.

### 9.1 Identification difficulty

| Family | Signature strength | Notes |
|---|---|---|
| Hikvision | **Excellent** | `HIKVISION@HANGZHOU` ASCII string. Near-zero false positive rate. |
| Dahua | **Very good** | Superblock magic, plus `DHAV`/`dhav` in the data area as a self-validating fallback |
| TP-Link VIGI | Good | Standard FS superblock + characteristic directory naming |
| Uniview | Medium **[H]** | Requires RE to establish |
| Matrix / Honeywell native | **Unknown** | §11 |

### 9.2 Deleted-recovery yield, ranked

1. **Dahua family** — per-frame magic + per-frame timestamp + per-frame channel. Best
   possible case for carving. A destroyed index costs you almost nothing.
2. **Hikvision family** — strong PS signatures, redundant index, per-frame time in
   private headers; channel attribution sometimes lost on carved fragments.
3. **Uniview / Matrix / Honeywell** — at minimum, raw H.264/H.265 start-code carving,
   which recovers playable GOPs but usually **no timestamp and no channel**. Honest
   output: "recovered, time unknown, physical order N".
4. **TP-Link VIGI** — different game: ext4 journal + inode recovery + MP4 box carving,
   with filesystem timestamps.

### 9.3 Timestamp risk, ranked (highest risk first)

1. **Any family with an unverified encoding** — a mis-decoded packing yields a
   *plausible but wrong date*, the worst failure mode in the product. This is why every
   decoder needs known-good vectors (NFR-12) before it ships.
2. **Imported-firmware timezone defaults** — units shipped with UTC+8 defaults and never
   corrected. Affects all Chinese-origin families.
3. **RTC drift with a dead battery** — universal, unrelated to vendor, and the reason
   Method B (reference-object capture at seizure) is in the SOP.
4. **TP-Link VIGI** — lowest risk (standard MP4 time + FS mtime), but the two sources
   can disagree.

### 9.4 Export-file handling (provenance class D — very common in practice)

| Family | What you get on the USB stick | Parseable? |
|---|---|---|
| Dahua | `.dav` files, sometimes with a bundled player | Yes — same DHAV framing as on disk |
| Hikvision | `.mp4` or `.264`, sometimes a self-extracting player | Yes |
| Uniview | `.mp4` / `.ts` | Yes, largely standard |
| TP-Link VIGI | `.mp4` | Yes |
| Matrix / Honeywell | Vendor formats **[U]** | Determine per unit |

Because Dahua export files use the same framing as the disk, **the Dahua plugin's frame
parser serves both the disk path and the export-file path with no extra code.** That is
a real architectural dividend of the plugin contract (doc 3 §4) and a reason to build
Dahua first.

---

## 10. Coverage strategy — what ships when

| Phase | Families | Level | Cumulative claim |
|---|---|---|---|
| M3 | Dahua family (Dahua, CP Plus, Dahua-ODM Godrej/Honeywell) | Full parse + T1–T3 | 1 family, ~3–4 brands |
| M4 | Hikvision family (+ ODM rebrands) | Full parse + T1–T3 | 2 families, ~5–6 brands |
| M9a | TP-Link VIGI via generic FS | Full parse | 3 families |
| M9b | Uniview | Parse if RE completes; else carve-only | 4 families |
| M9c | Matrix SATATYA | Parse if RE completes; else carve-only | 5 families |
| M9d | Honeywell native | Carve-only | 6 families, **8 brands** |

This satisfies "5–6 OEMs" honestly, with the level of support **stated per family** in
the tool and in the report. Claiming "supports 8 vendors" when three are carve-only
would be exactly the kind of overstatement that the negative-findings discipline in this
project exists to prevent.

---

## 11. Reverse-engineering procedure for an unknown format

The repeatable method. Run it per unknown family; it is also the P4 onboarding path in
[doc 2 §4 UJ6](02-prd.md).

**Step 0 — Get hardware.** A working recorder plus a disk you own. Everything else
depends on this and the lead time is measured in weeks. Start now, in parallel with all
other work.

**Step 1 — Baseline capture.** Format a fresh, fully zeroed disk in the recorder. Image
it immediately. This is your *empty* reference: everything non-zero in it is pure
metadata structure, with no video noise. This single step saves days.

**Step 2 — Controlled recording.** Record known content with known ground truth:
- A visible, GPS-synced clock in frame (gives you timestamp ground truth).
- A distinctive scene per channel (gives you channel ground truth).
- Start and stop at recorded wall-clock times.
- Vary parameters across runs: one channel vs. many, different resolutions, motion
  vs. continuous recording.

**Step 3 — Differential imaging.** Image after each controlled change. `cmp`/`bsdiff`
consecutive images. **The bytes that changed when you added one 5-minute recording on
channel 3 are the index entry for that recording.** This is the highest-yield technique
in the entire procedure and it does not require any cleverness.

**Step 4 — Locate the timestamp.** In the diffed index entry, look for 32-bit values
near the Unix epoch for your recording time, and for BCD/bit-packed calendar patterns.
Test the hypothesis against the *other* controlled recordings — a decoder that explains
one timestamp is a coincidence; one that explains twelve is a decoder.

**Step 5 — Map the data area.** Follow the index entry's block pointer to the payload.
Identify the frame framing: look for repeated byte patterns at regular intervals, ASCII
magics, and H.264/H.265 start codes (`00 00 00 01`) with the NAL types that indicate
SPS/PPS/IDR.

**Step 6 — Validate by extraction.** Extract the ES for a known recording, remux, and
compare visually against what you recorded in step 2. If the clock in the recovered
video reads what your ground truth says it should, the parse is right.

**Step 7 — Fill the fixture.** Add the image (or a size-reduced synthetic equivalent —
doc 6 §3) plus a ground-truth JSON to `tests/corpus/`, and the plugin conformance suite
now guards the work permanently.

**Step 8 — Deletion behaviour.** Fill the disk to force overwrite. Diff again. The
index-entry bit that changes when a recording is aged out **is the in-use flag** — and
that flag is the entire basis of Tier-2 recovery.

**Tooling to build once, reuse for every family:** `tools/dump_signature.py` (hexdump +
entropy map + periodicity detection, FR-09) and a small differ that reports changed
extents between two images.

---

## 12. Confidence upgrade tracker

The work list. Every row must reach **[C]** before the corresponding claim appears in a
court-facing report.

| # | Claim | Now | Target | Method | Blocker |
|---|---|---|---|---|---|
| 1 | Hikvision master-sector field offsets | [R] | [C] | Verify on real image | Hardware |
| 2 | HIKBTREE entry layout | [R] | [C] | §11 steps 3–4 | Hardware |
| 3 | Dahua superblock layout | [R] | [C] | §11 steps 1–3 | Hardware |
| 4 | DHAV header field offsets | [R] | [C] | §11 step 5 + export-file cross-check | Dahua unit or `.dav` sample |
| 5 | Dahua timestamp packing | [R] | [C] | §11 step 4, ≥ 12 ground-truth vectors | Hardware |
| 6 | CP Plus ≡ Dahua format | [R] | [C] | Probe a CP Plus disk against the Dahua plugin | CP Plus unit |
| 7 | Godrej SKU → family mapping | [H] | [C] | Probe per SKU | Godrej units |
| 8 | Honeywell SKU → family mapping | [H] | [C] | Probe per SKU | Honeywell units |
| 9 | Uniview structure | [U]/[H] | [C] | Full §11 | Uniview unit |
| 10 | TP-Link VIGI FS + layout | [R] | [C] | Mount read-only, inspect | VIGI unit |
| 11 | Matrix SATATYA structure | [U] | [C] | Full §11 | **Matrix unit — longest lead time, order first** |
| 12 | Honeywell native structure | [U] | [C] | Full §11 | Honeywell native unit |

**The single highest-leverage action for this project is acquiring hardware.** Rows 1–6
unlock the two families that cover most of the market; row 11 unlocks the most
defensible novelty claim. Everything else is engineering that the team can already do.

---

## 13. Sources and method statement

Structural claims marked **[C]** and **[R]** derive from: peer-reviewed digital-forensics
literature on DVR filesystems (notably the published work on the Hikvision HIKBTREE
structure and on DVR video-file carving), publicly available vendor SDK and player
documentation, open-source projects that read vendor export formats, and practitioner
community documentation. Claims marked **[H]** are the team's inferences from ODM
relationships and product observation. Claims marked **[U]** are open.

**Method statement for the record:** all format analysis is clean-room, performed on
lawfully obtained hardware and disks owned by the team, using differential imaging and
black-box observation. No vendor firmware is decompiled, redistributed, or included in
this repository, and no vendor intellectual property is reproduced here. Nothing in this
project circumvents an access control on content — the disks analysed are the team's own.
