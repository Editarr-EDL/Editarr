"""Ownership-safe, disposable workspaces for scan frame extraction."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from pathlib import Path

from scanner.runtime.runtime_paths import ensure_user_private_directory

_MARKER_NAME = ".editarr-scan-workspace.json"
_PREFIX = "scan-"
_STALE_AFTER_SECONDS = 24 * 60 * 60
_STALE_CLEANUP_LIMIT = 20


def _safe_scan_token(scan_id: str) -> str:
    token = "".join(
        character if character.isalnum() or character in "-_" else "-" for character in scan_id
    )
    return token[:64]


def _owner_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    else:
        return True


def create_scan_workspace(root: Path, scan_id: str) -> Path:
    """Create a uniquely owned workspace for a single scan attempt.

    The marker lets stale cleanup distinguish Editarr-owned directories from any
    unrelated temporary sibling.  Checkpoints/replays live outside this workspace.
    """

    root = ensure_user_private_directory(root)
    workspace = Path(tempfile.mkdtemp(prefix=f"{_PREFIX}{_safe_scan_token(scan_id)}-", dir=root))
    marker = workspace / _MARKER_NAME
    try:
        marker.write_text(
            json.dumps({"scan_id": scan_id, "pid": os.getpid(), "created_at": time.time()}),
            encoding="utf-8",
        )
    except OSError:
        # A markerless directory is not safe for normal ownership cleanup.  Remove
        # the just-created path directly before surfacing the allocation failure.
        shutil.rmtree(workspace, ignore_errors=True)
        raise
    return workspace


def remove_scan_workspace(workspace: Path) -> None:
    """Remove only a workspace created by :func:`create_scan_workspace`."""

    if workspace.name.startswith(_PREFIX) and (workspace / _MARKER_NAME).is_file():
        shutil.rmtree(workspace, ignore_errors=True)


def cleanup_stale_scan_workspaces(
    root: Path,
    *,
    now: float | None = None,
    stale_after_seconds: float = _STALE_AFTER_SECONDS,
    limit: int = _STALE_CLEANUP_LIMIT,
) -> list[Path]:
    """Remove a bounded set of old, dead-owner Editarr workspaces under ``root``.

    Active workers keep their directory because their recorded PID is live.  Missing,
    malformed, symlinked, or unmarked directories are never touched.
    """

    if not root.is_dir():
        return []
    cutoff = (time.time() if now is None else now) - stale_after_seconds
    candidates: list[tuple[float, Path]] = []
    for workspace in root.iterdir():
        if (
            workspace.is_symlink()
            or not workspace.is_dir()
            or not workspace.name.startswith(_PREFIX)
        ):
            continue
        marker = workspace / _MARKER_NAME
        if marker.is_symlink() or not marker.is_file():
            continue
        try:
            details = json.loads(marker.read_text(encoding="utf-8"))
            created_at = float(details["created_at"])
            pid = int(details["pid"])
        except (OSError, TypeError, ValueError, KeyError, json.JSONDecodeError):
            continue
        if created_at > cutoff or _owner_is_running(pid):
            continue
        candidates.append((created_at, workspace))

    removed: list[Path] = []
    for _created_at, workspace in sorted(candidates)[:limit]:
        remove_scan_workspace(workspace)
        if not workspace.exists():
            removed.append(workspace)
    return removed
