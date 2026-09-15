"""`EvidenceSource` — the read-only I/O boundary (doc 3 §3.1, D1, FR-17, FR-18, FR-75).

Every byte of evidence is read through this module. It is the only place in SPECTRA that
opens an evidence path, and nothing here can write: sources expose no write method, file
handles are opened read-only, and `map()` hands out read-only memoryviews.

Bad-sector policy: a failed read is retried, then narrowed to sectors; each sector that
still fails is zero-filled **and recorded as a gap**, so parsers can tell a hole from
real zeros (`gaps()`, `readable_ranges()`). A short read — the file shrank or its share
disconnected — is not a bad sector and raises `SourceUnavailable` instead (TC-RB-09).
"""

from __future__ import annotations

import bisect
import mmap
import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO, Protocol, runtime_checkable

from spectra.core.models import Extent

SECTOR_SIZE = 512
MIB = 1024 * 1024


class SourceError(Exception):
    """The evidence cannot be opened or addressed as requested."""


class SourceUnavailable(SourceError):
    """The evidence stopped being readable mid-operation (short read, mass read failure)."""


@dataclass(frozen=True, slots=True)
class SourceMember:
    """A file inside a multi-file source (split segment or loose export file)."""

    path: str
    extent: Extent


@dataclass(frozen=True, slots=True)
class SourceIdentity:
    """What the evidence object is. Device facts (serial, model) arrive with `acquire/`."""

    source_format: str  # "raw" | "split_raw" | "ewf" | "file_set"
    paths: tuple[str, ...]
    size: int
    members: tuple[SourceMember, ...]
    embedded_hashes: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class ReadPolicy:
    retries: int = 2
    sector_size: int = SECTOR_SIZE
    # A run of this many consecutive failed sectors means the source has gone away
    # (e.g. a disconnected share), not that the medium has a bad patch.
    abort_after_failed_sectors: int = 2048
    use_mmap: bool = True


@runtime_checkable
class EvidenceSource(Protocol):
    """Read-only, seekable, sparse-aware view of an evidence object. No write method."""

    size: int
    identity: SourceIdentity

    def read(self, offset: int, length: int) -> bytes:
        """Up to `length` bytes from `offset`; shorter only at end of source."""
        ...

    def map(self, offset: int, length: int) -> memoryview:
        """A read-only view of the range (clamped at end of source)."""
        ...

    def readable_ranges(self) -> Iterator[Extent]:
        """The source minus every gap recorded so far."""
        ...

    def gaps(self) -> tuple[Extent, ...]:
        """Zero-filled holes recorded so far, sorted and merged."""
        ...

    def close(self) -> None: ...


@dataclass
class _GapMap:
    extents: list[Extent] = field(default_factory=list)

    def add(self, gap: Extent) -> None:
        merged: list[Extent] = []
        for existing in sorted([*self.extents, gap]):
            if merged and existing.offset <= merged[-1].end:
                last = merged[-1]
                merged[-1] = Extent(last.offset, max(last.end, existing.end) - last.offset)
            else:
                merged.append(existing)
        self.extents = merged


class _BaseSource:
    """Bounds checking, retry/zero-fill policy, and gap bookkeeping shared by all sources."""

    size: int
    identity: SourceIdentity

    def __init__(self, size: int, identity: SourceIdentity, policy: ReadPolicy | None) -> None:
        self.size = size
        self.identity = identity
        self.policy = policy or ReadPolicy()
        self._gaps = _GapMap()
        self._closed = False

    # -- subclass hooks ---------------------------------------------------------------
    def _raw_read(self, offset: int, length: int) -> bytes:
        raise NotImplementedError

    def _map_single(self, offset: int, length: int) -> memoryview | None:
        """Zero-copy view if the implementation can provide one, else None."""
        return None

    # -- public API -------------------------------------------------------------------
    def _clamp(self, offset: int, length: int) -> int:
        if self._closed:
            raise SourceError("source is closed")
        if offset < 0 or length < 0:
            raise ValueError(f"negative offset/length: {offset}, {length}")
        if offset >= self.size:
            return 0
        return min(length, self.size - offset)

    def read(self, offset: int, length: int) -> bytes:
        length = self._clamp(offset, length)
        if length == 0:
            return b""
        return self._read_recovering(offset, length)

    def map(self, offset: int, length: int) -> memoryview:
        length = self._clamp(offset, length)
        if length == 0:
            return memoryview(b"")
        if self.policy.use_mmap:
            view = self._map_single(offset, length)
            if view is not None:
                return view
        return memoryview(self.read(offset, length))

    def gaps(self) -> tuple[Extent, ...]:
        return tuple(self._gaps.extents)

    def readable_ranges(self) -> Iterator[Extent]:
        cursor = 0
        for gap in self._gaps.extents:
            if gap.offset > cursor:
                yield Extent(cursor, gap.offset - cursor)
            cursor = max(cursor, gap.end)
        if cursor < self.size:
            yield Extent(cursor, self.size - cursor)

    def close(self) -> None:
        self._closed = True

    def __enter__(self) -> _BaseSource:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- bad-sector handling ----------------------------------------------------------
    def _attempt(self, offset: int, length: int) -> bytes | None:
        for _ in range(self.policy.retries + 1):
            try:
                data = self._raw_read(offset, length)
            except OSError:
                continue
            if len(data) != length:
                raise SourceUnavailable(
                    f"short read at offset {offset}: wanted {length}, got {len(data)} "
                    "(source truncated or disconnected)"
                )
            return data
        return None

    def _read_recovering(self, offset: int, length: int) -> bytes:
        data = self._attempt(offset, length)
        if data is not None:
            return data
        # Narrow the failure to sectors; zero-fill and record each one that stays bad.
        sector = self.policy.sector_size
        out = bytearray()
        consecutive_failures = 0
        position = offset
        end = offset + length
        while position < end:
            step = min(sector - (position % sector), end - position)
            chunk = self._attempt(position, step)
            if chunk is None:
                consecutive_failures += 1
                if consecutive_failures >= self.policy.abort_after_failed_sectors:
                    raise SourceUnavailable(
                        f"{consecutive_failures} consecutive unreadable sectors ending at "
                        f"offset {position}; source treated as unavailable"
                    )
                self._gaps.add(Extent(position, step))
                chunk = bytes(step)
            else:
                consecutive_failures = 0
            out += chunk
            position += step
        return bytes(out)


class _SegmentedFileSource(_BaseSource):
    """Contiguous address space over an ordered list of read-only files."""

    def __init__(
        self,
        paths: Sequence[Path],
        member_names: Sequence[str],
        source_format: str,
        policy: ReadPolicy | None,
    ) -> None:
        self._handles: list[BinaryIO] = []
        self._starts: list[int] = []
        members: list[SourceMember] = []
        cursor = 0
        try:
            for path, name in zip(paths, member_names, strict=True):
                handle = open(path, "rb", buffering=0)  # read-only by construction (D1)
                self._handles.append(handle)
                size = handle.seek(0, 2)
                self._starts.append(cursor)
                members.append(SourceMember(name, Extent(cursor, size)))
                cursor += size
        except OSError as exc:
            self._close_handles()
            raise SourceError(f"cannot open evidence file read-only: {exc}") from exc
        identity = SourceIdentity(
            source_format=source_format,
            paths=tuple(str(p) for p in paths),
            size=cursor,
            members=tuple(members),
        )
        super().__init__(cursor, identity, policy)

    def _locate(self, offset: int) -> int:
        return bisect.bisect_right(self._starts, offset) - 1

    def _raw_read(self, offset: int, length: int) -> bytes:
        out = bytearray()
        index = self._locate(offset)
        while length > 0 and index < len(self._handles):
            member = self.identity.members[index].extent
            if member.length == 0 or offset >= member.end:
                index += 1
                continue
            take = min(length, member.end - offset)
            handle = self._handles[index]
            handle.seek(offset - member.offset)
            chunk = handle.read(take)
            out += chunk
            if len(chunk) != take:
                break
            offset += take
            length -= take
            index += 1
        return bytes(out)

    def _map_single(self, offset: int, length: int) -> memoryview | None:
        index = self._locate(offset)
        member = self.identity.members[index].extent
        if not member.contains(Extent(offset, length)):
            return None
        local = offset - member.offset
        aligned = local - (local % mmap.ALLOCATIONGRANULARITY)
        try:
            mapping = mmap.mmap(
                self._handles[index].fileno(),
                length + (local - aligned),
                access=mmap.ACCESS_READ,
                offset=aligned,
            )
        except (OSError, ValueError):
            return None
        return memoryview(mapping)[local - aligned : local - aligned + length]

    def _close_handles(self) -> None:
        for handle in self._handles:
            handle.close()
        self._handles = []

    def close(self) -> None:
        self._close_handles()
        super().close()


class RawImageSource(_SegmentedFileSource):
    """`.dd` / `.img` raw images, including split segments `.001`, `.002`, …"""

    def __init__(self, path: Path | str, policy: ReadPolicy | None = None) -> None:
        path = Path(path)
        segments = discover_segments(path)
        source_format = "split_raw" if len(segments) > 1 else "raw"
        super().__init__(segments, [s.name for s in segments], source_format, policy)


class FileSetSource(_SegmentedFileSource):
    """Loose vendor export files as one addressable set (FR-18, provenance class D).

    Members are ordered by relative POSIX path so the address space is deterministic
    (NFR-08). `identity.members` gives each file's extent; parsers must not let a frame
    span two members.
    """

    def __init__(self, root: Path | str, policy: ReadPolicy | None = None) -> None:
        root = Path(root)
        if not root.is_dir():
            raise SourceError(f"not a directory: {root}")
        files = sorted(
            (p for p in root.rglob("*") if p.is_file() and not p.is_symlink()),
            key=lambda p: p.relative_to(root).as_posix(),
        )
        if not files:
            raise SourceError(f"no files in export directory: {root}")
        names = [p.relative_to(root).as_posix() for p in files]
        super().__init__(files, names, "file_set", policy)


class EwfImageSource(_BaseSource):
    """E01/Ex01 via pyewf (libewf). Reads the decompressed media, not the segment files.

    LIMITATION: libewf returns a chunk that fails its checksum or decompression as zeros
    without raising, and pyewf exposes no checksum-error list, so such holes cannot be
    recorded in `gaps()`. Corruption is detectable only by comparing the hash stored in the
    image with the hash of the media read back — which ingest does and records
    (`services.evidence`). An E01 with no stored hash gives no such protection.
    """

    def __init__(self, path: Path | str, policy: ReadPolicy | None = None) -> None:
        try:
            import pyewf  # type: ignore[import-not-found]
        except ImportError as exc:
            raise SourceError(
                "E01 support needs pyewf: install the 'ewf' extra (libewf-python)"
            ) from exc
        path = Path(path)
        try:
            filenames = pyewf.glob(str(path))
            handle = pyewf.handle()
            handle.open(filenames, "r")
        except OSError as exc:
            raise SourceError(f"cannot open E01 image read-only: {exc}") from exc
        self._handle = handle
        size = int(handle.get_media_size())
        hashes: list[tuple[str, str]] = []
        for name in ("MD5", "SHA1"):
            try:
                value = handle.get_hash_value(name)
            except Exception:  # noqa: BLE001 — absent hash values raise library-specific errors
                value = None
            if value:
                hashes.append((name.lower(), value.lower()))
        identity = SourceIdentity(
            source_format="ewf",
            paths=tuple(sorted(filenames)),
            size=size,
            members=(),
            embedded_hashes=tuple(hashes),
        )
        super().__init__(size, identity, policy)

    def _raw_read(self, offset: int, length: int) -> bytes:
        return bytes(self._handle.read_buffer_at_offset(length, offset))

    def close(self) -> None:
        if not self._closed:
            self._handle.close()
        super().close()


_SEGMENT_RE = re.compile(r"^(?P<stem>.+)\.(?P<num>\d{3,})$")


def discover_segments(path: Path) -> list[Path]:
    """Return the ordered segment list for a split raw image, or `[path]`.

    Splitting is recognised from a numeric extension (`image.001`). Segments must be
    contiguously numbered from the one given; a missing segment is an error, never a
    silently misaligned address space.
    """
    match = _SEGMENT_RE.match(path.name)
    if not match:
        return [path]
    stem, width, first = match["stem"], len(match["num"]), int(match["num"])
    siblings = {}
    for candidate in path.parent.iterdir():
        m = _SEGMENT_RE.match(candidate.name)
        if m and m["stem"] == stem and len(m["num"]) == width and candidate.is_file():
            siblings[int(m["num"])] = candidate
    if first not in siblings:
        raise SourceError(f"split image segment not found: {path}")
    numbers = sorted(n for n in siblings if n >= first)
    expected = list(range(first, first + len(numbers)))
    if numbers != expected:
        missing = sorted(set(range(first, max(numbers) + 1)) - set(numbers))
        raise SourceError(f"split image {path.name}: missing segment(s) {missing}")
    return [siblings[n] for n in numbers]


def open_source(path: Path | str, policy: ReadPolicy | None = None) -> EvidenceSource:
    """Open evidence read-only, choosing the implementation from the path."""
    path = Path(path)
    if path.is_dir():
        return FileSetSource(path, policy)
    if not path.exists():
        raise SourceError(f"evidence path does not exist: {path}")
    suffix = path.suffix.lower()
    if suffix in {".e01", ".ex01"}:
        return EwfImageSource(path, policy)
    return RawImageSource(path, policy)


def windows(
    src: EvidenceSource, chunk: int = 64 * MIB, overlap: int = 0
) -> Iterator[tuple[int, memoryview]]:
    """Yield `(offset, view)` windows of `chunk` bytes, each extended by `overlap` bytes
    into the next so a signature straddling a boundary is not missed (doc 3 §7.2)."""
    if chunk <= 0 or overlap < 0:
        raise ValueError("chunk must be positive and overlap non-negative")
    offset = 0
    while offset < src.size:
        yield offset, src.map(offset, chunk + overlap)
        offset += chunk
