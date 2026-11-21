"""Attempt-local spool of detected scenes awaiting a deferred description.

When descriptions are deferred until the detector is released, each scene's detection
result is written to its own file under ``visual-results/pending/`` and only a small
ordered index stays in memory. The description phase loads, describes, and deletes one
file at a time. Files live in the scan attempt's temporary work directory, so they never
outlive the attempt and are not a resume cache.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from scanner.models import FindingCreate

from .cache import atomic_write_json

PENDING_SCENE_VERSION = 1


@dataclass(frozen=True)
class PendingScene:
    """Index entry for one spooled scene: its scan order, scene index, and file."""

    order: int
    scene_index: int
    path: Path


def pending_dir(result_dir: Path) -> Path:
    """The spool directory, separate from the completed ``scene_*.json`` caches."""
    return result_dir / "pending"


def spool_scene(
    result_dir: Path,
    order: int,
    scene_index: int,
    frames: list[tuple[Path, float]],
    findings: list[FindingCreate],
) -> PendingScene:
    """Atomically write one scene's detections and return its index entry.

    Only frames at a finding's timestamp are kept: the description step looks up
    frames solely by finding timestamps, so the result is unchanged.
    """
    directory = pending_dir(result_dir)
    directory.mkdir(parents=True, exist_ok=True)
    flagged = {finding.start_seconds for finding in findings}
    path = directory / f"scene_{scene_index:04d}.json"
    atomic_write_json(
        path,
        {
            "version": PENDING_SCENE_VERSION,
            "scene_index": scene_index,
            "frames": [[str(frame), stamp] for frame, stamp in frames if stamp in flagged],
            "findings": [finding.model_dump(mode="json") for finding in findings],
        },
    )
    return PendingScene(order, scene_index, path)


def load_scene(entry: PendingScene) -> tuple[list[tuple[Path, float]], list[FindingCreate]]:
    """Read and validate one spooled scene; raise RuntimeError if it is unusable."""
    try:
        payload = json.loads(entry.path.read_text(encoding="utf-8"))
        if (
            not isinstance(payload, dict)
            or payload.get("version") != PENDING_SCENE_VERSION
            or payload.get("scene_index") != entry.scene_index
        ):
            raise ValueError("unexpected version or scene")
        frames = [(Path(str(path)), float(stamp)) for path, stamp in payload["frames"]]
        findings = [FindingCreate(**item) for item in payload["findings"]]
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise RuntimeError(
            f"Pending visual scene {entry.scene_index} could not be read: {entry.path}"
        ) from exc
    return frames, findings


def discard_all(result_dir: Path) -> None:
    """Remove any remaining spooled scenes."""
    shutil.rmtree(pending_dir(result_dir), ignore_errors=True)
