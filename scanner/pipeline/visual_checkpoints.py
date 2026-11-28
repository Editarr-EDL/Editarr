"""Private, restart-safe checkpoints for completed visual scene work."""

import hashlib
import json
import shutil
from pathlib import Path

from scanner.runtime.durability import atomic_write_bytes, sync_directory
from scanner.runtime.runtime_paths import ensure_user_private_directory
from scanner.settings.config import settings
from scanner.settings.version_info import version_information

from .provenance import stage_model_identity


def _digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _scan_root(data_dir: Path, scan_id: str) -> Path:
    return data_dir / "scan-checkpoints" / _digest(scan_id)


def visual_checkpoint_dir(scan_id: str, media_key: str, visual: dict, description: dict) -> Path:
    """Keep a job's progress bound to the same source, models and visual settings."""
    identity = _digest({
        "format": 1,
        "media": media_key,
        "behavior": version_information().scanner_behavior,
        "visual": visual,
        "description": description,
        "detector_model": stage_model_identity("visual", visual),
        "description_model": stage_model_identity("visual_qwen", description),
        "nudity_confidence_threshold": settings.nudity_confidence_threshold,
        "nudity_risk_labels": sorted(settings.nudity_risk_labels),
        "extracted_frame_max_height": settings.extracted_frame_max_height,
    })
    root = ensure_user_private_directory(settings.data_dir / "scan-checkpoints")
    scan_root = ensure_user_private_directory(_scan_root(settings.data_dir, scan_id))
    checkpoint = ensure_user_private_directory(scan_root / identity)
    for parent in (root.parent, root, scan_root):
        sync_directory(parent)
    return checkpoint


def scene_checkpoint_path(root: Path, scene, *, phase: str) -> Path:
    """Match exact boundaries as well as scene order when reusing a checkpoint."""
    signature = _digest([scene.index, scene.start_seconds, scene.end_seconds])
    return root / f"{phase}-{signature}.json"


def save_scene_checkpoint(path: Path, findings) -> None:
    """Persist the result and filename together before the worker reports progress."""
    atomic_write_bytes(
        path,
        json.dumps([finding.model_dump(mode="json") for finding in findings]).encode(),
        prefix=f".{path.name}-",
        sync_dir=True,
    )


def discard_visual_checkpoints(data_dir: Path, scan_id: str) -> None:
    """Remove one job's temporary checkpoints after its stage is committed or cancelled."""
    shutil.rmtree(_scan_root(data_dir, scan_id), ignore_errors=True)
