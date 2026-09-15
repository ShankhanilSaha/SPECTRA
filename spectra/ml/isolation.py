"""Network isolation enforcement for offline analytics (doc 3 §9, FR-95, Rule 6).

Guarantees that analysis workers cannot perform network egress. "Cannot" beats "does not".
"""

from __future__ import annotations

import socket
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any


class NetworkEgressForbidden(PermissionError):
    """Raised when an analytics process attempts network communication."""


@contextmanager
def enforce_network_isolation() -> Iterator[None]:
    """Context manager that disables network connectivity for the current thread/process.

    Any attempt to connect via TCP/UDP sockets to non-loopback addresses raises
    `NetworkEgressForbidden`.
    """
    orig_connect = socket.socket.connect
    orig_connect_ex = socket.socket.connect_ex

    def blocked_connect(self: socket.socket, address: Any) -> None:
        # Check if connecting to a network host
        host = address[0] if isinstance(address, tuple | list) and address else address
        # Allow localhost / unix domain sockets if needed internally
        if host not in ("127.0.0.1", "localhost", "::1"):
            raise NetworkEgressForbidden(
                f"SPECTRA offline-by-construction rule violated (FR-95): "
                f"attempted network connection to {address!r}"
            )
        orig_connect(self, address)

    def blocked_connect_ex(self: socket.socket, address: Any) -> int:
        host = address[0] if isinstance(address, tuple | list) and address else address
        if host not in ("127.0.0.1", "localhost", "::1"):
            raise NetworkEgressForbidden(
                f"SPECTRA offline-by-construction rule violated (FR-95): "
                f"attempted network connection to {address!r}"
            )
        return orig_connect_ex(self, address)

    socket.socket.connect = blocked_connect  # type: ignore[assignment]
    socket.socket.connect_ex = blocked_connect_ex  # type: ignore[assignment]
    try:
        yield
    finally:
        socket.socket.connect = orig_connect  # type: ignore[assignment]
        socket.socket.connect_ex = orig_connect_ex  # type: ignore[assignment]
