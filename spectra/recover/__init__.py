"""Recovery engine — tiers T1–T4, merge, and the byte-level coverage map (doc 3 §7).

Vendor-free by contract (doc 8 §3.4). Everything here drives off the `VendorPlugin`
surface — `frames()`, `carve_signatures()`, `enumerate()` — so a new family needs one
plugin module and one registry line, and never a change in this package (AC-12).

There is no `if family == ...` in this package, and there must never be one.
"""

from __future__ import annotations

from spectra.recover.coverage import (
    BUCKET_PRECEDENCE,
    Bucket,
    CoverageMap,
    CoverageRun,
    claim,
    resolve,
)

__all__ = [
    "BUCKET_PRECEDENCE",
    "Bucket",
    "CoverageMap",
    "CoverageRun",
    "claim",
    "resolve",
]
