#!/usr/bin/env python3
"""SPECTRA Tier S Synthetic Disk Image Corpus Generator (doc 6 §3.1, CLAUDE.md §15.1).

Builds small, fully specified, deterministic disk images and export files along with their
accompanying `ground_truth.json` oracles for automated validation.

Corpus items generated:
- S-01: Dahua-family layout, 4 channels, valid DHAV framing, sequential timestamps.
- S-02: S-01 with 30 % of index entries marked free, data blocks intact (T2 orphan oracle).
- S-03: S-01 with 20 % of blocks partially overwritten (T3 carving oracle).
- S-04: Hikvision-family layout, master sector ('HIKVISION@HANGZHOU') + dual HIKBTREE copies.
- S-05: S-04 with primary HIKBTREE corrupted, backup intact (redundancy fallback oracle).
- S-06: Bad-sector image: known unreadable LBA spans / gaps (T4 bad-sector oracle).
- S-07: Known clock offset (+00:17:42, UTC+05:30) (timestamp offset oracle).
- S-08: Clock changed mid-recording (device clock stepped back 2 hours) (multi-segment oracle).
- S-09: Truncated image (cut at 60 % of total length) (parser robustness oracle).
- S-10: Ambiguity fixture (Hikvision master sector + Dahua DHAV stream) (TC-ID-02 oracle).
- S-11: Empty / unrecorded zeroed disk (zero false-positive oracle).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

DHAV_START_MAGIC = b"DHAV"
DHAV_END_MAGIC = b"dhav"
HIK_MASTER_MAGIC = b"HIKVISION@HANGZHOU"
HIKBTREE_MAGIC = b"HIKBTREE"

FRAME_TYPE_I = 0xFD
FRAME_TYPE_P = 0xFC
FRAME_TYPE_AUDIO = 0xF0

CODEC_H264 = 0x4
CODEC_H265 = 0xC

SECTOR_SIZE = 512
MIB = 1024 * 1024


def pack_dhav_date(dt: datetime) -> int:
    """Pack datetime into Dahua 32-bit LE date field."""
    return (
        ((dt.year - 2000) << 26)
        | (dt.month << 22)
        | (dt.day << 17)
        | (dt.hour << 12)
        | (dt.minute << 6)
        | dt.second
    )


def make_h264_nal_units(frame_count: int = 25) -> list[tuple[bytes, bool]]:
    """Produce minimal valid H.264 Annex-B NAL units (SPS+PPS+IDR for key, non-IDR for P)."""
    # Minimal baseline SPS (profile 66, level 3.0, 320x240)
    sps = b"\x00\x00\x00\x01\x67\x42\x00\x1e\x96\x54\x05\x01\xe9\x80\x80\x40"
    # Minimal PPS
    pps = b"\x00\x00\x00\x01\x68\xce\x3c\x80"
    # IDR slice payload (I-frame)
    idr = b"\x00\x00\x00\x01\x65\x88\x84\x00\x10" + bytes(120)
    # Non-IDR slice payload (P-frame)
    p_slice = b"\x00\x00\x00\x01\x41\x9a\x01\x02" + bytes(64)

    units: list[tuple[bytes, bool]] = []
    for i in range(frame_count):
        if i % 25 == 0:
            units.append((sps + pps + idr, True))
        else:
            units.append((p_slice, False))
    return units


def make_dhav_frame(
    payload: bytes,
    *,
    is_key: bool,
    channel: int = 0,
    seq: int = 0,
    date_val: int = 0,
    tick: int = 0,
    width: int = 320,
    height: int = 240,
    fps: int = 25,
    codec_id: int = CODEC_H264,
) -> bytes:
    """Construct one DHAV frame with 24-byte header, extension, payload, and trailer."""
    ext = (
        bytes([0x80, 0, width // 8, height // 8, 0x81, 0, codec_id, fps])
        if is_key
        else b""
    )
    frame_type = FRAME_TYPE_I if is_key else FRAME_TYPE_P
    length = 24 + len(ext) + len(payload) + 8
    header = struct.pack(
        "<4sBBBBIIIHBB",
        DHAV_START_MAGIC,
        frame_type,
        0,
        channel,
        0,
        seq,
        length,
        date_val,
        tick,
        len(ext),
        0,
    )
    trailer = DHAV_END_MAGIC + struct.pack("<I", length)
    return header + ext + payload + trailer


def generate_dhav_stream(
    units: list[tuple[bytes, bool]],
    *,
    start_time: datetime,
    channel: int = 0,
    fps: int = 25,
    tick_start: int = 1000,
) -> tuple[bytes, bytes, list[dict[str, Any]]]:
    """Generate a sequence of DHAV frames.

    Returns:
        (dhav_stream_bytes, raw_elementary_stream_bytes, frame_metadata_list)
    """
    dhav_bytes = bytearray()
    es_bytes = bytearray()
    frames_meta = []
    ms_per_frame = int(1000 / fps)

    for i, (unit, is_key) in enumerate(units):
        cur_time = start_time + timedelta(milliseconds=i * ms_per_frame)
        tick = (tick_start + i * ms_per_frame) % 0x10000
        packed_date = pack_dhav_date(cur_time)
        frame_bytes = make_dhav_frame(
            unit,
            is_key=is_key,
            channel=channel,
            seq=i,
            date_val=packed_date,
            tick=tick,
            fps=fps,
        )
        dhav_bytes.extend(frame_bytes)
        es_bytes.extend(unit)
        frames_meta.append(
            {
                "index": i,
                "is_key": is_key,
                "pts_ms": i * ms_per_frame,
                "iso_time": cur_time.isoformat(),
                "channel": channel,
                "size": len(frame_bytes),
            }
        )

    return bytes(dhav_bytes), bytes(es_bytes), frames_meta


@dataclass
class GroundTruth:
    fixture_id: str
    description: str
    family: str
    format: str
    size_bytes: int
    sha256: str
    md5: str
    recordings: list[dict[str, Any]] = field(default_factory=list)
    oracles: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True)


class CorpusBuilder:
    """Builder for all Tier S synthetic images and ground_truth.json files."""

    def __init__(self, out_dir: Path) -> None:
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)

    def write_fixture(self, fixture_id: str, filename: str, data: bytes, gt: GroundTruth) -> Path:
        fdir = self.out_dir / fixture_id
        fdir.mkdir(parents=True, exist_ok=True)
        img_path = fdir / filename
        img_path.write_bytes(data)

        # Update hashes and size
        gt.size_bytes = len(data)
        gt.sha256 = hashlib.sha256(data).hexdigest()
        gt.md5 = hashlib.md5(data, usedforsecurity=False).hexdigest()

        gt_path = fdir / "ground_truth.json"
        gt_path.write_text(gt.to_json(), encoding="utf-8")
        return fdir

    def build_s01(self) -> Path:
        """S-01: Dahua-family baseline layout, 4 channels, 24 h simulated window, no deletions."""
        start_time = datetime(2026, 3, 5, 0, 0, 0)
        img_data = bytearray(4 * MIB)  # 4 MiB disk image
        # Offset 0..512: Superblock / disk identifier
        img_data[0:4] = b"DHFS"
        img_data[4:8] = struct.pack("<I", len(img_data))
        img_data[8:12] = struct.pack("<I", 4)  # 4 channels

        recordings_gt = []
        offset = 64 * 1024  # 64 KiB data area start

        for ch in range(4):
            units = make_h264_nal_units(frame_count=50)
            ch_start = start_time + timedelta(hours=ch * 6)
            dhav, es, _ = generate_dhav_stream(units, start_time=ch_start, channel=ch)
            rec_len = len(dhav)

            img_data[offset : offset + rec_len] = dhav
            es_sha256 = hashlib.sha256(es).hexdigest()

            recordings_gt.append(
                {
                    "recording_id": f"s01_ch{ch}",
                    "channel": ch,
                    "codec": "h264",
                    "t_start": ch_start.isoformat(),
                    "t_end": (ch_start + timedelta(seconds=2)).isoformat(),
                    "frame_count": 50,
                    "extents": [[offset, rec_len]],
                    "es_sha256": es_sha256,
                    "recovery_tier": "T1",
                }
            )
            offset += rec_len + 32 * 1024  # leave space between recordings

        gt = GroundTruth(
            fixture_id="S-01",
            description="Dahua-family baseline layout, 4 channels, no deletions",
            family="dahua",
            format="raw",
            size_bytes=len(img_data),
            sha256="",
            md5="",
            recordings=recordings_gt,
            oracles={
                "t1_recordings_count": 4,
                "channels": [0, 1, 2, 3],
                "expected_support": "carve_only",  # raw disk requires carving until Dahua layout RE
            },
        )
        return self.write_fixture("S-01", "s01_dahua_baseline.raw", bytes(img_data), gt)

    def build_s02(self) -> Path:
        """S-02: S-01 with 30 % of index entries marked free, blocks intact (T2 orphan oracle)."""
        # S-02 has 4 recordings on disk; 1 marked as orphaned in the simulated index
        start_time = datetime(2026, 3, 5, 10, 0, 0)
        img_data = bytearray(4 * MIB)
        img_data[0:4] = b"DHFS"

        recordings_gt = []
        offset = 64 * 1024
        orphan_ids = ["s02_ch1"]

        for ch in range(4):
            units = make_h264_nal_units(frame_count=40)
            ch_start = start_time + timedelta(hours=ch)
            dhav, es, _ = generate_dhav_stream(units, start_time=ch_start, channel=ch)
            rec_len = len(dhav)
            img_data[offset : offset + rec_len] = dhav

            is_orphan = ch == 1
            rec_id = f"s02_ch{ch}"
            recordings_gt.append(
                {
                    "recording_id": rec_id,
                    "channel": ch,
                    "codec": "h264",
                    "t_start": ch_start.isoformat(),
                    "t_end": (ch_start + timedelta(seconds=1, milliseconds=600)).isoformat(),
                    "frame_count": 40,
                    "extents": [[offset, rec_len]],
                    "es_sha256": hashlib.sha256(es).hexdigest(),
                    "recovery_tier": "T2" if is_orphan else "T1",
                    "is_orphan": is_orphan,
                }
            )
            offset += rec_len + 32 * 1024

        gt = GroundTruth(
            fixture_id="S-02",
            description="S-01 with orphan index entries; data blocks intact (T2 oracle)",
            family="dahua",
            format="raw",
            size_bytes=len(img_data),
            sha256="",
            md5="",
            recordings=recordings_gt,
            oracles={
                "t2_orphan_recordings": orphan_ids,
                "total_recordings": 4,
            },
        )
        return self.write_fixture("S-02", "s02_orphan_t2.raw", bytes(img_data), gt)

    def build_s03(self) -> Path:
        """S-03: S-01 with 20 % of blocks partially overwritten (T3 carving oracle)."""
        start_time = datetime(2026, 3, 5, 12, 0, 0)
        img_data = bytearray(4 * MIB)
        img_data[0:4] = b"DHFS"

        offset = 64 * 1024
        units = make_h264_nal_units(frame_count=50)
        dhav, es, frames = generate_dhav_stream(units, start_time=start_time, channel=0)
        img_data[offset : offset + len(dhav)] = dhav

        # Overwrite 16 KiB starting 2080 bytes into the recording (inside frame 19). The
        # recording is only 5378 bytes long, so the overwrite runs past its end: nothing of
        # it survives after the overwrite point, including the second I-frame.
        overwrite_start = offset + frames[20]["size"] * 20
        overwrite_len = 16 * 1024
        img_data[overwrite_start : overwrite_start + overwrite_len] = b"\xAA" * overwrite_len

        # What is left of the recording is its extent minus the overwritten span. A piece
        # the overwrite fully covers does not survive and is not listed.
        rec_start, rec_end = offset, offset + len(dhav)
        overwrite_end = overwrite_start + overwrite_len
        surviving_ranges = [
            [start, end - start]
            for start, end in (
                (rec_start, min(rec_end, overwrite_start)),
                (max(rec_start, overwrite_end), rec_end),
            )
            if end > start
        ]

        gt = GroundTruth(
            fixture_id="S-03",
            description="S-01 with partially overwritten blocks (T3 carving oracle)",
            family="dahua",
            format="raw",
            size_bytes=len(img_data),
            sha256="",
            md5="",
            recordings=[
                {
                    "recording_id": "s03_damaged",
                    "channel": 0,
                    "recovery_tier": "T3",
                    "surviving_ranges": surviving_ranges,
                }
            ],
            oracles={
                "overwrite_start": overwrite_start,
                "overwrite_len": overwrite_len,
                "surviving_ranges": surviving_ranges,
            },
        )
        return self.write_fixture("S-03", "s03_partially_overwritten_t3.raw", bytes(img_data), gt)

    def build_s04(self) -> Path:
        """S-04: Hikvision-family baseline layout: master sector + both HIKBTREE copies."""
        img_data = bytearray(4 * MIB)
        # Sector 0: Master sector
        img_data[0:18] = HIK_MASTER_MAGIC
        # Offsets to primary and backup HIKBTREE
        primary_tree_offset = 64 * 1024    # 64 KiB
        backup_tree_offset = 128 * 1024    # 128 KiB
        struct.pack_into("<QQ", img_data, 32, primary_tree_offset, backup_tree_offset)

        # Primary HIKBTREE
        img_data[primary_tree_offset : primary_tree_offset + 8] = HIKBTREE_MAGIC
        img_data[primary_tree_offset + 8 : primary_tree_offset + 12] = struct.pack(
            "<I", 1
        )  # 1 entry
        # Backup HIKBTREE
        img_data[backup_tree_offset : backup_tree_offset + 8] = HIKBTREE_MAGIC
        img_data[backup_tree_offset + 8 : backup_tree_offset + 12] = struct.pack("<I", 1)

        gt = GroundTruth(
            fixture_id="S-04",
            description="Hikvision-family layout, master sector + dual HIKBTREE copies",
            family="hikvision",
            format="raw",
            size_bytes=len(img_data),
            sha256="",
            md5="",
            recordings=[],
            oracles={
                "master_sector_offset": 0,
                "primary_tree_offset": primary_tree_offset,
                "backup_tree_offset": backup_tree_offset,
                "expected_index_used": "primary",
            },
        )
        return self.write_fixture("S-04", "s04_hikvision_baseline.raw", bytes(img_data), gt)

    def build_s05(self) -> Path:
        """S-05: S-04 with primary HIKBTREE corrupted, backup intact."""
        img_data = bytearray(4 * MIB)
        img_data[0:18] = HIK_MASTER_MAGIC
        primary_tree_offset = 64 * 1024
        backup_tree_offset = 128 * 1024
        struct.pack_into("<QQ", img_data, 32, primary_tree_offset, backup_tree_offset)

        # Corrupt primary: fill with zeros or corrupt magic
        img_data[primary_tree_offset : primary_tree_offset + 16] = b"\x00" * 16
        # Intact backup
        img_data[backup_tree_offset : backup_tree_offset + 8] = HIKBTREE_MAGIC
        img_data[backup_tree_offset + 8 : backup_tree_offset + 12] = struct.pack("<I", 1)

        gt = GroundTruth(
            fixture_id="S-05",
            description="S-04 with primary HIKBTREE corrupted, backup intact",
            family="hikvision",
            format="raw",
            size_bytes=len(img_data),
            sha256="",
            md5="",
            recordings=[],
            oracles={
                "primary_corrupted": True,
                "backup_valid": True,
                "expected_index_used": "backup",
            },
        )
        return self.write_fixture("S-05", "s05_hikvision_backup_fallback.raw", bytes(img_data), gt)

    def build_s06(self) -> Path:
        """S-06: Bad-sector image: 2 % of LBAs unreadable (T4 bad-sector oracle)."""
        img_data = bytearray(2 * MIB)
        img_data[0:4] = b"DHFS"

        # Mark specific sectors as simulated bad sectors
        bad_lbas = [100, 101, 102, 500, 501, 1200, 1201, 2048]
        bad_ranges = []
        for lba in bad_lbas:
            off = lba * SECTOR_SIZE
            img_data[off : off + SECTOR_SIZE] = b"\x00" * SECTOR_SIZE
            bad_ranges.append([off, SECTOR_SIZE])

        gt = GroundTruth(
            fixture_id="S-06",
            description="Bad-sector disk image with known unreadable LBA ranges (T4 oracle)",
            family="generic",
            format="raw",
            size_bytes=len(img_data),
            sha256="",
            md5="",
            recordings=[],
            oracles={
                "bad_lbas": bad_lbas,
                "bad_ranges": bad_ranges,
                "bad_sector_count": len(bad_lbas),
            },
        )
        return self.write_fixture("S-06", "s06_bad_sectors.raw", bytes(img_data), gt)

    def build_s07(self) -> Path:
        """S-07: Known clock offset (+00:17:42, timezone UTC+05:30)."""
        offset_seconds = 17 * 60 + 42  # 1062 seconds
        # Generate export .dav file with known timestamps
        ref_utc_start = datetime(2026, 3, 5, 8, 30, 0)
        # Device local clock = ref_utc_start + 05:30 + offset
        local_delta = timedelta(hours=5, minutes=30, seconds=offset_seconds)
        device_local_start = ref_utc_start + local_delta

        units = make_h264_nal_units(frame_count=25)
        dhav, es, frames = generate_dhav_stream(units, start_time=device_local_start, channel=1)

        gt = GroundTruth(
            fixture_id="S-07",
            description="Recordings with known clock offset +00:17:42 and UTC+05:30",
            family="dahua",
            format="dav",
            size_bytes=len(dhav),
            sha256="",
            md5="",
            recordings=[
                {
                    "recording_id": "s07_offset_clip",
                    "channel": 1,
                    "codec": "h264",
                    "t_device_local": device_local_start.isoformat(),
                    "t_reference_utc": ref_utc_start.isoformat(),
                    "offset_seconds": offset_seconds,
                    "frame_count": 25,
                    "es_sha256": hashlib.sha256(es).hexdigest(),
                }
            ],
            oracles={
                "device_timezone": "UTC+05:30",
                "offset_seconds": offset_seconds,
                "reference_utc_start": ref_utc_start.isoformat(),
            },
        )
        return self.write_fixture("S-07", "s07_clock_offset.dav", dhav, gt)

    def build_s08(self) -> Path:
        """S-08: Clock changed mid-recording (device clock stepped back 2 hours)."""
        time_part1 = datetime(2026, 3, 5, 14, 0, 0)
        units1 = make_h264_nal_units(frame_count=25)
        dhav1, es1, _ = generate_dhav_stream(units1, start_time=time_part1, channel=1)

        # Step back 2 hours
        time_part2 = datetime(2026, 3, 5, 12, 0, 0)
        units2 = make_h264_nal_units(frame_count=25)
        dhav2, es2, _ = generate_dhav_stream(units2, start_time=time_part2, channel=1)

        combined_dhav = dhav1 + dhav2

        gt = GroundTruth(
            fixture_id="S-08",
            description="Clock shifted backwards 2 hours mid-recording (multi-segment oracle)",
            family="dahua",
            format="dav",
            size_bytes=len(combined_dhav),
            sha256="",
            md5="",
            recordings=[
                {
                    "segment": 1,
                    "t_device_start": time_part1.isoformat(),
                    "frame_count": 25,
                },
                {
                    "segment": 2,
                    "t_device_start": time_part2.isoformat(),
                    "frame_count": 25,
                },
            ],
            oracles={
                "clock_rollback_detected": True,
                "discontinuity_offset": len(dhav1),
                "rollback_delta_seconds": -7200,
            },
        )
        return self.write_fixture("S-08", "s08_clock_rollback.dav", combined_dhav, gt)

    def build_s09(self) -> Path:
        """S-09: Truncated image (valid image cut at 60 % of total length)."""
        start_time = datetime(2026, 3, 5, 10, 0, 0)
        units = make_h264_nal_units(frame_count=100)
        dhav, _, _ = generate_dhav_stream(units, start_time=start_time, channel=1)

        full_len = len(dhav)
        truncated_len = int(full_len * 0.6)
        truncated_dhav = dhav[:truncated_len]

        gt = GroundTruth(
            fixture_id="S-09",
            description="Truncated export file cut at 60 % of total size",
            family="dahua",
            format="dav",
            size_bytes=len(truncated_dhav),
            sha256="",
            md5="",
            recordings=[],
            oracles={
                "original_size": full_len,
                "truncated_size": truncated_len,
                "must_not_crash": True,
                "report_truncation": True,
            },
        )
        return self.write_fixture("S-09", "s09_truncated.dav", truncated_dhav, gt)

    def build_s10(self) -> Path:
        """S-10: Ambiguity fixture (Hikvision master sector + Dahua DHAV stream)."""
        data = bytearray(3 * MIB)
        # Hikvision master sector signature at offset 0
        data[0:18] = HIK_MASTER_MAGIC

        # Dahua stream in the sweep/data region
        units = make_h264_nal_units(frame_count=20)
        start = datetime(2026, 3, 5, 12, 0, 0)
        dhav, _, _ = generate_dhav_stream(units, start_time=start, channel=1)
        data[MIB + 7 : MIB + 7 + len(dhav)] = dhav

        gt = GroundTruth(
            fixture_id="S-10",
            description="Deliberately ambiguous image with Hikvision and Dahua signatures",
            family="ambiguous",
            format="raw",
            size_bytes=len(data),
            sha256="",
            md5="",
            recordings=[],
            oracles={
                "expected_status": "ambiguous",
                "candidate_families": ["hikvision_test", "dahua"],
                "must_not_auto_select": True,
            },
        )
        return self.write_fixture("S-10", "s10_ambiguous.raw", bytes(data), gt)

    def build_s11(self) -> Path:
        """S-11: Empty / unrecorded disk (all zeros)."""
        data = bytes(2 * MIB)

        gt = GroundTruth(
            fixture_id="S-11",
            description="Empty / zero-filled disk with zero recordings",
            family="unknown",
            format="raw",
            size_bytes=len(data),
            sha256="",
            md5="",
            recordings=[],
            oracles={
                "expected_recordings_count": 0,
                "expected_status": "unknown",
                "must_not_fabricate": True,
            },
        )
        return self.write_fixture("S-11", "s11_empty_disk.raw", data, gt)

    def build_all(self) -> dict[str, Path]:
        """Generate the complete S-01 through S-11 corpus."""
        return {
            "S-01": self.build_s01(),
            "S-02": self.build_s02(),
            "S-03": self.build_s03(),
            "S-04": self.build_s04(),
            "S-05": self.build_s05(),
            "S-06": self.build_s06(),
            "S-07": self.build_s07(),
            "S-08": self.build_s08(),
            "S-09": self.build_s09(),
            "S-10": self.build_s10(),
            "S-11": self.build_s11(),
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate SPECTRA Tier S synthetic test corpus")
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("tests/corpus"),
        help="Destination directory for generated corpus fixtures",
    )
    parser.add_argument(
        "--fixture",
        type=str,
        default="all",
        help="Specific fixture to build (e.g. S-01, S-02) or 'all'",
    )
    args = parser.parse_args()

    builder = CorpusBuilder(args.out_dir)
    if args.fixture == "all":
        results = builder.build_all()
        print(f"Successfully generated {len(results)} Tier S fixtures in {args.out_dir}")
        for fid, path in results.items():
            print(f"  [{fid}] -> {path}")
    else:
        fid = args.fixture.upper()
        build_fn = getattr(builder, f"build_{fid.lower()}", None)
        if not build_fn:
            raise SystemExit(f"Unknown fixture ID: {fid}")
        path = build_fn()
        print(f"Successfully generated {fid} at {path}")


if __name__ == "__main__":
    main()
