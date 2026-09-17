"""Plugin registry (doc 3 §4.2). Adding a format family = one import + one entry (AC-12)."""

from __future__ import annotations

from spectra.plugins.base import VendorPlugin
from spectra.plugins.dahua import DahuaPlugin
from spectra.plugins.hikvision import HikvisionPlugin

REGISTRY: tuple[type[VendorPlugin], ...] = (
    DahuaPlugin,
    HikvisionPlugin,
)


def get_plugin(family: str) -> type[VendorPlugin]:
    for plugin in REGISTRY:
        if plugin.family == family:
            return plugin
    raise KeyError(f"no plugin registered for family {family!r}")
