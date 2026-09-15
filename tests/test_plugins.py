"""Generic plugin conformance suite — runs against EVERY registered plugin (doc 3 §4.2, AC-12).

`pytest tests/test_plugins.py -k <family>` is the check a new plugin must pass. These tests
use only the contract; nothing here knows any vendor.
"""

from __future__ import annotations

import random

import pytest

from spectra.core.models import DeviceTime, ProbeResult
from spectra.core.source import MIB, RawImageSource
from spectra.identify.engine import build_plan
from spectra.plugins.base import LayoutNotSupported
from spectra.plugins.registry import REGISTRY, get_plugin

PLUGINS = pytest.mark.parametrize("plugin", REGISTRY, ids=[p.family for p in REGISTRY])


def image(tmp_path, data: bytes, name: str) -> RawImageSource:
    path = tmp_path / name
    path.write_bytes(data)
    return RawImageSource(path)


def test_unregistered_family_is_a_clear_error():
    with pytest.raises(KeyError, match="no plugin registered for family 'matrix'"):
        get_plugin("matrix")


@PLUGINS
def test_declares_identity(plugin):
    assert plugin.family and plugin.family == plugin.family.lower()
    assert isinstance(plugin.layout_versions, tuple) and plugin.layout_versions
    assert plugin.plugin_version.count(".") == 2
    assert get_plugin(plugin.family) is plugin


@PLUGINS
@pytest.mark.parametrize("size", [1, 23, 511, 4096, 3 * MIB])
def test_probe_on_non_matching_data_never_claims_a_parse(tmp_path, plugin, size):
    rng = random.Random(size)
    for name, data in {"zero": bytes(size), "random": rng.randbytes(size)}.items():
        src = image(tmp_path, data, f"{name}-{size}.img")
        result = plugin.probe(src, build_plan(src.size))
        assert result is None or (isinstance(result, ProbeResult) and not result.parse_supported)
        src.close()


@PLUGINS
def test_superblock_on_foreign_data_refuses_cleanly(tmp_path, plugin):
    src = image(tmp_path, random.Random(1).randbytes(MIB), "foreign.img")
    with pytest.raises(LayoutNotSupported):
        plugin().superblock(src)
    src.close()


@PLUGINS
def test_carve_signatures_validate_without_crashing_on_noise(tmp_path, plugin):
    rng = random.Random(2)
    signatures = plugin().carve_signatures()
    assert signatures
    for signature in signatures:
        assert signature.pattern
        noise = bytearray(rng.randbytes(256 * 1024))
        for i in range(0, len(noise) - len(signature.pattern), 4096):  # plant the pattern
            noise[i : i + len(signature.pattern)] = signature.pattern
        src = image(tmp_path, bytes(noise), f"noise-{signature.name}.img")
        accepted = [o for o in range(0, len(noise), 4096) if signature.validate(src, o)]
        assert accepted == []  # a bare pattern hit in noise is a false positive
        src.close()


@PLUGINS
def test_decode_time_always_returns_device_time_with_raw_kept(plugin):
    rng = random.Random(3)
    for raw in [0, 1, 0x7FFFFFFF, 0xFFFFFFFF, *(rng.getrandbits(32) for _ in range(200))]:
        decoded = plugin().decode_time(raw, None)
        assert isinstance(decoded, DeviceTime)
        assert decoded.raw == raw and decoded.encoding
