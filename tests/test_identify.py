"""Identification engine (FR-01..FR-04, AC-01, TC-ID-02, TC-ID-03, TC-ID-05, TC-RB-01, TC-RB-02)."""

from __future__ import annotations

import random
from typing import ClassVar

import pytest

from spectra.core.models import Extent, ProbeResult, SignatureMatch
from spectra.core.source import MIB, RawImageSource
from spectra.identify.engine import IdentifyError, build_plan, identify
from spectra.plugins.dahua import DahuaPlugin
from tests import dhavgen


def image(tmp_path, data: bytes, name: str = "disk.img") -> RawImageSource:
    path = tmp_path / name
    path.write_bytes(data)
    return RawImageSource(path)


class FakeHikPlugin:
    """Stands in for a second family: matches a master-sector string at offset 0."""

    family: ClassVar[str] = "hikvision_test"
    layout_versions: ClassVar[tuple[str, ...]] = ("test",)
    plugin_version: ClassVar[str] = "0.0.0"

    @classmethod
    def probe(cls, src, plan):
        data = src.read(0, 18)
        if data != b"HIKVISION@HANGZHOU":
            return None
        return ProbeResult(cls.family, "test", 0.99, True, (SignatureMatch(0, data, "master"),),
                           cls.plugin_version)


class CrashingPlugin:
    family: ClassVar[str] = "crashy"
    layout_versions: ClassVar[tuple[str, ...]] = ()
    plugin_version: ClassVar[str] = "0.0.0"

    @classmethod
    def probe(cls, src, plan):
        raise struct_error()


def struct_error():
    import struct

    return struct.error("unpack requires a buffer of 24 bytes")


def test_plan_covers_fr01_offsets_without_overlap():
    plan = build_plan(100 * MIB, sweep_windows=10)
    assert plan.lba0 == Extent(0, 512)
    assert plan.head == Extent(0, MIB)
    assert plan.tail == Extent(99 * MIB, MIB)
    assert len(plan.sweep) == 10
    ends = [e.end for e in plan.sweep]
    assert all(a.end <= b.offset for a, b in zip(plan.sweep, plan.sweep[1:], strict=False))
    assert plan.sweep[0].offset >= MIB and max(ends) <= 99 * MIB
    tiny = build_plan(100)
    assert tiny.head == Extent(0, 100) and tiny.tail == Extent(0, 100) and tiny.sweep == ()


def test_tc_rb_01_zero_length_is_a_clean_error(tmp_path):
    with pytest.raises(IdentifyError, match="zero-length"):
        identify(image(tmp_path, b""))


def test_tc_id_03_random_data_is_unknown(tmp_path):
    result = identify(image(tmp_path, random.Random(3).randbytes(4 * MIB)))
    assert result.status == "unknown"
    assert result.candidates == () and result.selected is None
    assert result.support == "none"


def test_tc_rb_02_ntfs_image_is_not_a_dvr_format(tmp_path):
    boot = bytearray(4 * MIB)
    boot[0:3] = b"\xebR\x90"
    boot[3:11] = b"NTFS    "
    boot[510:512] = b"\x55\xaa"
    result = identify(image(tmp_path, bytes(boot)))
    assert result.status == "unknown"
    assert {o.description for o in result.observations} >= {"NTFS boot sector",
                                                             "MBR / boot-sector signature"}


def test_tc_id_02_two_matching_families_are_reported_not_resolved(tmp_path):
    body, _ = dhavgen.stream([(b"\x00\x00\x01\x65" + bytes(64), True)] * 8)
    data = bytearray(3 * MIB)
    data[0:18] = b"HIKVISION@HANGZHOU"
    data[MIB + 7 : MIB + 7 + len(body)] = body
    result = identify(image(tmp_path, bytes(data)), plugins=(DahuaPlugin, FakeHikPlugin))
    assert result.status == "ambiguous"
    assert result.selected is None
    assert result.support == "pending_selection"
    assert [c.family for c in result.candidates] == ["hikvision_test", "dahua"]


def test_tc_id_05_matched_bytes_and_offsets_are_exact(tmp_path):
    body, _ = dhavgen.stream([(b"\x00\x00\x01\x65" + bytes(40), True)] * 4)
    data = bytearray(2 * MIB)
    offset = 1234
    data[offset : offset + len(body)] = body
    result = identify(image(tmp_path, bytes(data)), plugins=(DahuaPlugin,))
    assert result.status == "identified"
    header, trailer = result.selected.matches[:2]
    first_len = int.from_bytes(body[12:16], "little")
    assert header.offset == offset and header.data == body[:24]
    assert trailer.offset == offset + first_len - 8
    assert trailer.data == body[first_len - 8 : first_len]


def test_plan_rejects_invalid_parameters():
    with pytest.raises(ValueError):
        build_plan(MIB, sweep_windows=-1)
    with pytest.raises(ValueError):
        build_plan(MIB, window=0)


class ImpostorPlugin:
    """Claims a family other than its own — a contract violation, not a match."""

    family: ClassVar[str] = "impostor"
    layout_versions: ClassVar[tuple[str, ...]] = ("x",)
    plugin_version: ClassVar[str] = "0.0.0"

    @classmethod
    def probe(cls, src, plan):
        return ProbeResult("dahua", None, 0.99, False, (), cls.plugin_version)


def test_probe_claiming_another_family_is_rejected(tmp_path):
    result = identify(image(tmp_path, bytes(MIB)), plugins=(ImpostorPlugin,))
    assert result.status == "unknown" and result.candidates == ()
    assert result.errors[0].error_type == "ContractViolation"


def test_crashing_probe_is_contained_and_reported(tmp_path):
    result = identify(image(tmp_path, bytes(MIB)), plugins=(CrashingPlugin,))
    assert result.status == "unknown"
    assert result.errors[0].error_type == "error"
    assert "unpack requires" in result.errors[0].message
