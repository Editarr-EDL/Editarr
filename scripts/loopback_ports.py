"""Loopback listener ports for installed-artifact and proxy verification.

Every issued port comes with its successor reserved and bind-checked, because the
production browser spec restarts the upstream on ``port + 1``. With
``EDITARR_VERIFY_PORTS=A-B`` both ports of each pair stay inside ``A-B``, so
concurrent verifiers can share one host by owning disjoint spans.
"""

from __future__ import annotations

import os
import socket

_ISSUED: set[int] = set()
_EPHEMERAL_ATTEMPTS = 50


def _span() -> tuple[int, int] | None:
    value = os.environ.get("EDITARR_VERIFY_PORTS")
    if not value:
        return None
    first, separator, last = value.partition("-")
    if not (separator and first.isdigit() and last.isdigit()):
        raise ValueError(f"EDITARR_VERIFY_PORTS must be A-B, got {value!r}")
    span = int(first), int(last)
    if not 1 <= span[0] < span[1] <= 65535:
        raise ValueError(f"EDITARR_VERIFY_PORTS must hold at least two ports, got {value!r}")
    return span


def _free(port: int) -> bool:
    with socket.socket() as listener:
        try:
            listener.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def _claim(port: int) -> bool:
    pair = (port, port + 1)
    if port + 1 > 65535 or _ISSUED.intersection(pair) or not all(map(_free, pair)):
        return False
    _ISSUED.update(pair)
    return True


def _ephemeral() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def available_port() -> int:
    """Issue a free loopback port whose successor is also free and unissued."""
    span = _span()
    if span is None:
        for _ in range(_EPHEMERAL_ATTEMPTS):
            port = _ephemeral()
            if _claim(port):
                return port
        raise RuntimeError("no free ephemeral loopback port pair")
    first, last = span
    for port in range(first, last):
        if _claim(port):
            return port
    raise RuntimeError(f"no free loopback port pair in {first}-{last}")
