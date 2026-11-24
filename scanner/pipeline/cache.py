"""Filesystem-backed cache helpers for scan pipeline artifacts."""

from __future__ import annotations

import json
import re
from pathlib import Path

from scanner.runtime.durability import atomic_write_bytes
from scanner.runtime.media_fingerprints import sampled_media_fingerprint
from scanner.settings.config import settings


def compute_video_md5(video_path: str) -> str:
    """Return the legacy MD5 sample used by pipeline cache keys."""
    return sampled_media_fingerprint(video_path).digest


def _video_cache_root() -> Path:
    return settings.cache_dir / "videos"


def _video_cache_dir(video_hash: str) -> Path:
    return _video_cache_root() / video_hash


def cached_audio_path(video_hash: str, *, create: bool = True) -> Path:
    """Return the persisted audio extraction path for a video hash."""
    cache_dir = _video_cache_dir(video_hash)
    if create:
        cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / "audio.wav"


def _scene_cache_path(video_hash: str, threshold: float) -> Path:
    cache_dir = _video_cache_dir(video_hash)
    cache_dir.mkdir(parents=True, exist_ok=True)
    threshold_token = str(threshold).replace(".", "_")
    return cache_dir / f"scenes_threshold_{threshold_token}.json"


def load_cached_scenes(video_hash: str, threshold: float) -> list[dict[str, float | int]] | None:
    """Return cached scene boundary metadata when available."""
    cache_file = _scene_cache_path(video_hash, threshold)
    if not cache_file.exists():
        return None
    try:
        with cache_file.open("r", encoding="utf-8") as file_obj:
            payload = json.load(file_obj)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, list):
        return None
    normalized: list[dict[str, float | int]] = []
    for item in payload:
        if not isinstance(item, dict):
            return None
        try:
            normalized.append(
                {
                    "start_seconds": float(item["start_seconds"]),
                    "end_seconds": float(item["end_seconds"]),
                    "index": int(item["index"]),
                }
            )
        except (KeyError, TypeError, ValueError):
            return None
    return normalized


def save_cached_scenes(
    video_hash: str, threshold: float, scenes: list[dict[str, float | int]]
) -> None:
    """Persist scene boundary metadata for reuse across scans."""
    cache_file = _scene_cache_path(video_hash, threshold)
    atomic_write_json(cache_file, scenes)


def _credits_result_cache_path(cache_key: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{64}", cache_key):
        raise ValueError("Credits cache key must be a SHA-256 digest")
    cache_dir = settings.cache_dir / "credits" / cache_key[:2]
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / f"{cache_key}.json"


def load_cached_credits_result(cache_key: str) -> dict[str, object] | None:
    """Return one version-keyed detector result payload when valid JSON is available."""
    cache_file = _credits_result_cache_path(cache_key)
    if not cache_file.exists():
        return None
    try:
        payload = json.loads(cache_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def save_cached_credits_result(cache_key: str, payload: dict[str, object]) -> None:
    """Persist one detector result under its complete behavior identity."""
    atomic_write_json(_credits_result_cache_path(cache_key), payload)


def atomic_write_json(path: Path, payload: object) -> None:
    """Write a JSON cache entry without exposing a partial file to a retry."""
    atomic_write_bytes(path, json.dumps(payload).encode("utf-8"), prefix=f".{path.name}-")


def transcription_cache_path(video_hash: str, model_name: str, *, create: bool = True) -> Path:
    """Return the transcription cache file path scoped by model."""
    cache_dir = _video_cache_dir(video_hash)
    if create:
        cache_dir.mkdir(parents=True, exist_ok=True)
    model_token = re.sub(r"[^a-z0-9._-]+", "_", model_name.strip().lower())
    return cache_dir / f"transcript_{model_token}.json"
