"""EvidenceSource read-only boundary (D1, FR-17, FR-18, FR-75, TC-RB-01, TC-RB-09)."""

from __future__ import annotations

import hashlib
import random
import sys
from pathlib import Path

import pytest

from spectra.core.hashing import Hasher, hash_source
from spectra.core.models import Extent
from spectra.core.source import (
    EvidenceSource,
    FileSetSource,
    RawImageSource,
    ReadPolicy,
    SourceError,
    SourceUnavailable,
    open_source,
    windows,
)


def write(path: Path, data: bytes) -> Path:
    path.write_bytes(data)
    return path


def test_hasher_matches_known_vectors():
    h = Hasher()
    h.update(b"a")
    h.update(memoryview(b"bc"))
    d = h.digests()
    assert d.md5 == "900150983cd24fb0d6963f7d28e17f72"
    assert d.sha256 == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    assert d.size == 3


def test_raw_image_read_map_and_bounds(tmp_path):
    data = bytes(range(256)) * 400
    src = RawImageSource(write(tmp_path / "disk.img", data))
    assert isinstance(src, EvidenceSource)
    assert src.size == len(data)
    assert src.read(10, 5) == data[10:15]
    assert src.read(len(data) - 3, 100) == data[-3:]  # clamped at end
    assert src.read(len(data) + 5, 10) == b""
    view = src.map(70000, 30000)
    assert view.readonly
    assert bytes(view) == data[70000:100000]
    with pytest.raises(ValueError):
        src.read(-1, 1)
    src.close()


def test_sources_expose_no_write_path(tmp_path):
    src = RawImageSource(write(tmp_path / "disk.img", b"\x00" * 4096))
    for name in ("write", "truncate", "writelines", "flush"):
        assert not hasattr(src, name)
    with pytest.raises(TypeError):
        src.map(0, 16)[0] = 1  # read-only view
    src.close()


def test_split_raw_segments_form_one_address_space(tmp_path):
    parts = [b"A" * 1000, b"B" * 1500, b"C" * 10]
    for i, part in enumerate(parts, start=1):
        write(tmp_path / f"case.{i:03d}", part)
    src = open_source(tmp_path / "case.001")
    assert src.identity.source_format == "split_raw"
    assert src.size == 2510
    assert src.read(990, 20) == b"A" * 10 + b"B" * 10
    assert bytes(src.map(2495, 15)) == b"B" * 5 + b"C" * 10
    assert [m.extent for m in src.identity.members] == [
        Extent(0, 1000), Extent(1000, 1500), Extent(2500, 10)
    ]
    assert hash_source(src, chunk=333).sha256 == hashlib.sha256(b"".join(parts)).hexdigest()
    src.close()


def test_split_raw_with_missing_segment_is_refused(tmp_path):
    write(tmp_path / "case.001", b"A")
    write(tmp_path / "case.003", b"C")
    with pytest.raises(SourceError, match="missing segment"):
        open_source(tmp_path / "case.001")


def test_file_set_is_ordered_deterministically_with_member_extents(tmp_path):
    (tmp_path / "ch2").mkdir()
    write(tmp_path / "ch2" / "b.dav", b"22")
    write(tmp_path / "a.dav", b"1")
    write(tmp_path / "empty.dav", b"")
    src = FileSetSource(tmp_path)
    names = [m.path for m in src.identity.members]
    assert names == ["a.dav", "ch2/b.dav", "empty.dav"]
    assert src.read(0, 10) == b"122"
    src.close()


def test_zero_length_image_is_addressable_and_empty(tmp_path):
    src = open_source(write(tmp_path / "empty.img", b""))
    assert src.size == 0
    assert src.read(0, 10) == b""
    assert list(src.readable_ranges()) == []
    src.close()


class FlakySource(RawImageSource):
    """Raises OSError for any read touching the bad range — a simulated bad patch."""

    bad = Extent(1024, 1024)  # sectors 2 and 3

    def _raw_read(self, offset, length):
        if offset < self.bad.end and offset + length > self.bad.offset:
            raise OSError(5, "I/O error")
        return super()._raw_read(offset, length)


def test_bad_sectors_are_zero_filled_and_recorded_as_gaps(tmp_path):
    data = b"\xff" * 8192
    src = FlakySource(write(tmp_path / "disk.img", data), ReadPolicy(retries=1))
    out = src.read(0, 8192)
    assert out[:1024] == data[:1024]
    assert out[1024:2048] == bytes(1024)  # a hole, not real zeros …
    assert out[2048:] == data[2048:]
    assert src.gaps() == (Extent(1024, 1024),)  # … and it is recorded as one
    assert list(src.readable_ranges()) == [Extent(0, 1024), Extent(2048, 6144)]
    src.close()


class VanishingSource(RawImageSource):
    def _raw_read(self, offset, length):
        return b""  # the share went away


def test_short_read_raises_instead_of_zero_filling(tmp_path):
    src = VanishingSource(write(tmp_path / "disk.img", b"\x01" * 4096))
    with pytest.raises(SourceUnavailable, match="short read"):
        src.read(0, 4096)
    assert src.gaps() == ()
    src.close()


def test_mass_read_failure_means_source_unavailable_not_a_disk_of_zeros(tmp_path):
    """TC-RB-09: a share that disconnects fails every sector; that must stop the operation
    rather than zero-fill the rest of the image."""

    class DeadSource(RawImageSource):
        def _raw_read(self, offset, length):
            raise OSError(5, "I/O error")

    src = DeadSource(write(tmp_path / "disk.img", b"\x01" * 8192),
                     ReadPolicy(retries=0, abort_after_failed_sectors=4))
    with pytest.raises(SourceUnavailable, match="4 consecutive unreadable sectors"):
        src.read(0, 8192)
    src.close()


def test_isolated_bad_sectors_reset_the_consecutive_failure_count(tmp_path):
    class Alternating(RawImageSource):
        def _raw_read(self, offset, length):
            if length > 512 or (offset // 512) % 2:
                raise OSError(5, "I/O error")
            return super()._raw_read(offset, length)

    src = Alternating(write(tmp_path / "disk.img", b"\x07" * 4096),
                      ReadPolicy(retries=0, abort_after_failed_sectors=2))
    data = src.read(0, 4096)
    assert data == (b"\x07" * 512 + bytes(512)) * 4
    assert src.gaps() == tuple(Extent(o, 512) for o in (512, 1536, 2560, 3584))
    src.close()


def test_closed_source_refuses_reads(tmp_path):
    src = RawImageSource(write(tmp_path / "disk.img", b"\x00" * 16))
    src.close()
    with pytest.raises(SourceError, match="closed"):
        src.read(0, 1)


def test_map_falls_back_to_a_copy_when_mmap_is_unavailable(tmp_path, monkeypatch):
    import spectra.core.source as source_module

    data = bytes(range(256)) * 64
    path = write(tmp_path / "disk.img", data)

    def no_mmap(*args, **kwargs):
        raise OSError("mmap not permitted")

    monkeypatch.setattr(source_module.mmap, "mmap", no_mmap)
    for policy in (ReadPolicy(), ReadPolicy(use_mmap=False)):
        src = RawImageSource(path, policy)
        view = src.map(1000, 3000)
        assert view.readonly and bytes(view) == data[1000:4000]
        assert bytes(src.map(len(data), 10)) == b""
        src.close()


def test_open_errors_are_clean(tmp_path):
    with pytest.raises(SourceError, match="does not exist"):
        open_source(tmp_path / "missing.img")
    with pytest.raises(SourceError, match="segment not found"):
        RawImageSource(tmp_path / "missing.001")
    with pytest.raises(SourceError, match="not a directory"):
        FileSetSource(write(tmp_path / "file.dav", b"x"))
    (tmp_path / "empty").mkdir()
    with pytest.raises(SourceError, match="no files"):
        FileSetSource(tmp_path / "empty")


def test_unopenable_evidence_file_is_a_source_error(tmp_path, monkeypatch):
    import spectra.core.source as source_module

    path = write(tmp_path / "disk.img", b"\x00" * 64)

    def denied(*args, **kwargs):
        raise PermissionError(13, "Access is denied", str(path))

    monkeypatch.setattr(source_module, "open", denied, raising=False)
    with pytest.raises(SourceError, match="cannot open evidence file read-only"):
        RawImageSource(path)


def test_image_that_shrinks_after_opening_is_unavailable_not_zero_filled(tmp_path):
    for i, part in enumerate([b"A" * 1000, b"", b"B" * 1000], start=1):
        write(tmp_path / f"case.{i:03d}", part)
    src = open_source(tmp_path / "case.001")
    assert src.read(990, 20) == b"A" * 10 + b"B" * 10  # reads step over the empty segment
    (tmp_path / "case.003").write_bytes(b"B" * 10)  # the share now serves a truncated file
    with pytest.raises(SourceUnavailable, match="short read"):
        src.read(1000, 1000)
    assert src.gaps() == ()
    src.close()


def test_e01_without_pyewf_says_how_to_get_it(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "pyewf", None)  # import pyewf → ImportError
    with pytest.raises(SourceError, match="install the 'ewf' extra"):
        open_source(write(tmp_path / "disk.E01", b"EVF"))


def test_hash_source_reports_progress(tmp_path):
    src = RawImageSource(write(tmp_path / "disk.img", b"\x05" * 2500))
    seen = []
    digests = hash_source(src, chunk=1000, progress=lambda done, total: seen.append((done, total)))
    assert seen == [(1000, 2500), (2000, 2500), (2500, 2500)]
    assert digests.to_json() == {"md5": digests.md5, "sha256": digests.sha256, "size": 2500}
    src.close()


# -- E01 (FR-17) ---------------------------------------------------------------------------

def _require_pyewf():
    pytest.importorskip("pyewf", reason="pyewf (libewf-python) not installed")


@pytest.mark.ewf
@pytest.mark.parametrize("compress", [False, True])
def test_e01_reads_decompressed_media_and_exposes_stored_hash(tmp_path, compress):
    _require_pyewf()
    from tests.ewfgen import write_e01

    media = bytes(range(256)) * 4096 + b"\x00" * 65536 + hashlib.sha256(b"x").digest() * 64
    path = write_e01(tmp_path / "disk.E01", media, compress=compress)
    src = open_source(path)
    assert src.identity.source_format == "ewf"
    assert src.size == len(media)
    assert src.read(1_000_000, 4096) == media[1_000_000:1_004_096]
    assert bytes(src.map(len(media) - 100, 500)) == media[-100:]
    stored = dict(src.identity.embedded_hashes)["md5"]
    assert stored == hashlib.md5(media, usedforsecurity=False).hexdigest()
    # The hash is of the media, not of the .E01 container file.
    assert hash_source(src).sha256 == hashlib.sha256(media).hexdigest()
    assert hash_source(src).sha256 != hashlib.sha256(path.read_bytes()).hexdigest()
    src.close()
    with pytest.raises(SourceError, match="closed"):
        src.read(0, 1)


@pytest.mark.ewf
def test_e01_that_is_not_an_e01_is_a_clean_error(tmp_path):
    _require_pyewf()
    with pytest.raises(SourceError, match="cannot open E01"):
        open_source(write(tmp_path / "fake.E01", b"not an expert witness file" * 100))


@pytest.mark.ewf
def test_e01_corrupt_chunk_reads_as_zeros_without_a_gap_documented_limitation(tmp_path):
    """libewf zero-fills a chunk that fails its checksum and pyewf cannot report it. This test
    pins that behaviour so a pyewf upgrade that starts reporting errors is noticed, and so the
    reliance on the stored-hash comparison at ingest stays visible."""
    _require_pyewf()
    from tests.ewfgen import CHUNK, write_e01

    media = random.Random(11).randbytes(CHUNK * 3)  # non-repeating, so the find is unambiguous
    path = write_e01(tmp_path / "disk.E01", media, compress=False)
    blob = bytearray(path.read_bytes())
    position = blob.find(media[CHUNK + 100 : CHUNK + 132])
    assert position > 0 and blob.count(media[CHUNK + 100 : CHUNK + 132]) == 1
    blob[position + 5] ^= 0xFF
    path.write_bytes(bytes(blob))
    src = open_source(path)
    assert src.read(CHUNK, CHUNK) == bytes(CHUNK)
    assert src.gaps() == ()
    assert hash_source(src).md5 != dict(src.identity.embedded_hashes)["md5"]
    src.close()


def test_windows_overlap_covers_boundaries(tmp_path):
    data = bytes(range(256)) * 16
    src = RawImageSource(write(tmp_path / "disk.img", data))
    seen = list(windows(src, chunk=1000, overlap=8))
    assert [o for o, _ in seen] == [0, 1000, 2000, 3000, 4000]
    assert bytes(seen[0][1]) == data[:1008]
    assert bytes(seen[-1][1]) == data[4000:]
    for chunk, overlap in ((0, 0), (10, -1)):
        with pytest.raises(ValueError):
            next(windows(src, chunk=chunk, overlap=overlap))
    src.close()
