"""Report generation — the twelve-section report, its §7 negative findings, and the
BSA s. 63(4) certificate (doc 3 §10, FR-80..FR-88).

Everything here is derived from the case database on every call. Nothing in this package
stores a rendered conclusion, and nothing offers a way to suppress one: §7 is generated,
not written, and the examiner may add to it but never remove from it (FR-81).
"""

from __future__ import annotations

from spectra.report.negative import Finding, negative_findings, summarise

__all__ = ["Finding", "negative_findings", "summarise"]
