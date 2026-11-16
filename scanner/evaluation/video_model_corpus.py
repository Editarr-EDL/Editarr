"""Build a human-labeled visual-model evaluation corpus from source videos."""

from __future__ import annotations

import json
import re
from pathlib import Path

from scanner.models import McfCategoryFamily
from scanner.pipeline.cache import atomic_write_json
from scanner.pipeline.scenes import Scene, sample_scene_frames


def _parse_categories(categories: str) -> tuple[McfCategoryFamily, ...]:
    values = [value.strip() for value in categories.split(",") if value.strip()]
    try:
        return tuple(dict.fromkeys(McfCategoryFamily(value) for value in values))
    except ValueError as exc:
        supported = ", ".join(category.value for category in McfCategoryFamily)
        raise ValueError(f"Invalid category. Use comma-separated values from: {supported}") from exc


def _load_manifest(manifest_path: Path) -> dict[str, object]:
    if not manifest_path.exists():
        return {"frames": []}
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read corpus manifest: {manifest_path}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("frames"), list):
        raise ValueError(f"Corpus manifest has no 'frames' array: {manifest_path}")
    return payload


def _next_capture_dir(frames_dir: Path) -> Path:
    highest = 0
    for path in frames_dir.glob("capture_*"):
        match = re.fullmatch(r"capture_(\d+)", path.name)
        if match:
            highest = max(highest, int(match.group(1)))
    return frames_dir / f"capture_{highest + 1:04d}"


def add_labeled_range(
    video_path: Path,
    corpus_dir: Path,
    *,
    start_seconds: float,
    end_seconds: float,
    categories: str = "",
    interval_seconds: float = 1.0,
) -> dict[str, object]:
    """Extract a labeled range from a video and append it to a corpus manifest."""
    if not video_path.is_file():
        raise FileNotFoundError(f"Video not found: {video_path}")
    if start_seconds < 0 or end_seconds <= start_seconds:
        raise ValueError("End time must be greater than start time, and start cannot be negative")
    if interval_seconds <= 0:
        raise ValueError("Frame interval must be greater than zero")

    expected_categories = _parse_categories(categories)
    corpus_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = corpus_dir / "manifest.json"
    manifest = _load_manifest(manifest_path)
    frames_dir = corpus_dir / "frames"
    capture_dir = _next_capture_dir(frames_dir)
    frames = sample_scene_frames(
        str(video_path),
        Scene(start_seconds=start_seconds, end_seconds=end_seconds, index=0),
        capture_dir,
        interval_seconds=interval_seconds,
    )
    if not frames:
        raise RuntimeError("No frames could be extracted from the requested video range")

    manifest_frames = manifest["frames"]
    assert isinstance(manifest_frames, list)
    for frame_path, timestamp in frames:
        manifest_frames.append(
            {
                "path": str(frame_path.relative_to(corpus_dir)),
                "expected_categories": [category.value for category in expected_categories],
                "timestamp_seconds": timestamp,
                "source_video": str(video_path),
            }
        )
    atomic_write_json(manifest_path, manifest)
    return {
        "added_frames": len(frames),
        "manifest": str(manifest_path),
        "capture_dir": str(capture_dir),
        "expected_categories": [category.value for category in expected_categories],
    }
