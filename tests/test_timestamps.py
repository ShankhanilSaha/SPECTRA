"""Timestamp decoder vectors (FR-54, NFR-12, TC-TS-01).

Every decoder needs known-good vectors before it ships. For DHAV these are SPEC-CONFORMANCE
vectors: the integers were packed by hand from the [R] bit layout in doc 4 §3.2, so they
prove the decoder implements that layout — not that real recorders use it. Doc 4 §12 row 5
stays [R] until ≥ 12 ground-truth vectors from a real unit (in-frame GPS clock) are added
to `DHAV_GROUND_TRUTH` below.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from spectra.plugins.dahua import TIME_ENCODING, DahuaPlugin, decode_datetime

# (raw date field, expected device-local wall clock or None)
DHAV_CONFORMANCE = [
    (0x00420000, datetime(2000, 1, 1, 0, 0, 0)),      # year field 0
    (0x68CAE80A, datetime(2026, 3, 5, 14, 32, 10)),   # demo vector (doc 8 §4)
    (0xFF3F7EFB, datetime(2063, 12, 31, 23, 59, 59)), # largest representable year
    (0x6A5C8000, datetime(2026, 9, 14, 8, 0, 0)),
    (0x68B90000, datetime(2026, 2, 28, 16, 0, 0)),
    (0x64B40000, datetime(2025, 2, 26, 0, 0, 0)),
    (0x60BA3CEF, datetime(2024, 2, 29, 3, 51, 47)),   # leap day
]
DHAV_INVALID = [
    0x00000000,  # month 0, day 0
    0xFFFFFFFF,  # month 15, hour 31, minute 63
    0x7FFFFFFF,  # the 32-bit placeholder (CLAUDE.md §17 item 7) → not a calendar time here
    0x6B4A0000,  # 2026-13-05
    0x68BC0000,  # 2026-02-30
    0x64BAC000,  # 2025-02-29 (not a leap year)
    0x68CB8000,  # 2026-03-05 24:00:00
    0x68CAEF00,  # 2026-03-05 14:60:00
]
# Filled from real hardware; empty until then, and the test says so rather than passing.
DHAV_GROUND_TRUTH: list[tuple[int, datetime]] = []


def _pack(y, mo, d, h, mi, s):
    return ((y - 2000) << 26) | (mo << 22) | (d << 17) | (h << 12) | (mi << 6) | s


def test_conformance_vectors_were_packed_per_the_documented_layout():
    # Guards the literals above against typos by re-deriving them independently.
    for raw, expected in DHAV_CONFORMANCE:
        e = expected
        assert raw == _pack(e.year, e.month, e.day, e.hour, e.minute, e.second), hex(raw)


@pytest.mark.parametrize(("raw", "expected"), DHAV_CONFORMANCE)
def test_dhav_decodes_conformance_vectors(raw, expected):
    decoded = decode_datetime(raw)
    assert decoded.local == expected
    assert decoded.raw == raw  # raw kept exactly as stored
    assert decoded.encoding == TIME_ENCODING


@pytest.mark.parametrize("raw", DHAV_INVALID)
def test_dhav_invalid_calendar_values_give_time_unknown_not_a_guess(raw):
    decoded = decode_datetime(raw)
    assert decoded.local is None
    assert decoded.raw == raw


def test_dhav_six_byte_raw_form_matches_int_form_and_keeps_tick():
    raw = (0x68CAE80A).to_bytes(4, "little") + (0xFFFE).to_bytes(2, "little")
    decoded = DahuaPlugin().decode_time(raw, None)
    assert decoded.local == datetime(2026, 3, 5, 14, 32, 10)
    assert decoded.raw_repr() == raw.hex()


def test_dhav_rejects_out_of_range_raw():
    with pytest.raises(ValueError):
        decode_datetime(1 << 32)
    with pytest.raises(ValueError):
        decode_datetime(b"\x00\x01\x02")


def test_dhav_ground_truth_vectors_from_hardware():
    if not DHAV_GROUND_TRUTH:
        pytest.skip("no ground-truth DHAV vectors from a real unit yet (doc 4 §12 row 5 stays [R])")
    assert len(DHAV_GROUND_TRUTH) >= 12
    for raw, expected in DHAV_GROUND_TRUTH:
        assert decode_datetime(raw).local == expected
