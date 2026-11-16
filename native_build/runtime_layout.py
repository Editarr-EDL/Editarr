"""Target-specific names and locations inside a frozen Editarr runtime.

This module intentionally has no PyInstaller, scanner, or host-platform dependency.
Build and installed-artifact verification therefore apply the same release-target
layout rules even when their host is not the artifact target.
"""

from __future__ import annotations

import os
from pathlib import Path

NATIVE_TARGETS = frozenset({"macos-arm64", "windows-x64", "linux-x86_64"})
_ENTRYPOINT_NAMES = ("Editarr", "Editarr.exe")


def _require_target(target: str) -> None:
    if target not in NATIVE_TARGETS:
        raise ValueError(f"unsupported Editarr runtime target: {target}")


def executable_name(target: str) -> str:
    """Return the only accepted frozen entrypoint filename for ``target``."""

    _require_target(target)
    return "Editarr.exe" if target == "windows-x64" else "Editarr"


def helper_name(name: str, target: str) -> str:
    """Return a target-native helper filename under ``_internal/helpers``."""

    _require_target(target)
    if not name or Path(name).name != name or name.endswith(".exe"):
        raise ValueError(f"invalid Editarr helper name: {name!r}")
    return f"{name}.exe" if target == "windows-x64" else name


def resolve_executable(runtime: Path, target: str) -> Path:
    """Resolve one executable while rejecting target/layout ambiguity.

    Raw PyInstaller roots use the target's entrypoint filename.  A macOS bundle
    exposes its deliberately named AppKit launcher instead.  Do not guess from
    the host OS or accept an arbitrary executable: that could silently verify a
    different target's payload.
    """

    _require_target(target)
    if runtime.suffix.lower() == ".app":
        if target != "macos-arm64":
            raise RuntimeError(f"{target} runtime cannot use a macOS .app layout")
        root = runtime / "Contents" / "MacOS"
    else:
        root = runtime

    expected = root / executable_name(target)
    candidates = [
        root / name
        for name in _ENTRYPOINT_NAMES
        if (root / name).exists() or (root / name).is_symlink()
    ]
    if len(candidates) > 1:
        raise RuntimeError(
            "ambiguous frozen Editarr entrypoints: "
            + ", ".join(str(candidate) for candidate in candidates)
        )
    if not candidates:
        raise RuntimeError(f"missing frozen {target} entrypoint: {expected}")
    candidate = candidates[0]
    if candidate != expected:
        raise RuntimeError(
            f"wrong frozen entrypoint for {target}: expected {expected.name}, "
            f"found {candidate.name}"
        )
    if candidate.is_symlink() or not candidate.is_file() or not os.access(candidate, os.X_OK):
        raise RuntimeError(f"frozen {target} entrypoint is not a regular executable: {candidate}")
    return candidate
