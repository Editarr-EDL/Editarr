"""Data locations, media extensions, item identifiers, and the hard content exclusion."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
from typing import Any

from scanner.runtime.durability import atomic_write_bytes

REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR_NAME = "evaluation_data"
DATA_ROOT_ENV = "EDITARR_EVAL_DATA_ROOT"
MEDIA_ROOT = "video"
RESULTS_DIR = "_results"
FILMS_ROOT = "films"
ITEM_ROOTS = frozenset({MEDIA_ROOT, FILMS_ROOT})

# Unvetted scraped content: never listed, decoded, thumbnailed, or scored.
EXCLUDED_DIRS: tuple[str, ...] = ("video/nude",)

VIDEO_EXTENSIONS = frozenset({".mp4", ".avi", ".mpg", ".mkv", ".mov", ".webm"})
IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".webp", ".gif"})


def default_data_root() -> Path:
    """Data root used without `--data-root`.

    `EDITARR_EVAL_DATA_ROOT` wins; otherwise `evaluation_data/` at the repository root
    when running from a source checkout, else `evaluation_data/` in the working directory.
    """
    configured = os.environ.get(DATA_ROOT_ENV, "").strip()
    if configured:
        return Path(configured).expanduser()
    if (REPO_ROOT / "pyproject.toml").is_file():
        return REPO_ROOT / DATA_DIR_NAME
    return Path.cwd() / DATA_DIR_NAME


def is_excluded(relative_path: str) -> bool:
    """Return whether a data-root-relative POSIX path is in (or is) an excluded folder."""
    parts = PurePosixPath(relative_path).parts
    if any(part.startswith(".") or part == RESULTS_DIR for part in parts):
        return True
    normalized = "/".join(parts).casefold()
    return any(
        normalized == excluded or normalized.startswith(excluded + "/")
        for excluded in EXCLUDED_DIRS
    )


def excluded_real_paths(data_root: Path) -> list[str]:
    """Case-folded real paths of the excluded folders under `data_root`."""
    return [os.path.realpath(data_root / excluded).casefold() for excluded in EXCLUDED_DIRS]


def resolves_into_excluded(data_root: Path, relative_path: str) -> bool:
    """Return whether a data-root path's real target (after symlinks) is in an excluded
    folder; links to files outside the data root, such as films, are allowed."""
    resolved = os.path.realpath(data_root / relative_path).casefold()
    return any(
        resolved == blocked or resolved.startswith(blocked + os.sep)
        for blocked in excluded_real_paths(data_root)
    )


def ensure_allowed(relative_path: str, data_root: Path) -> None:
    """Refuse to touch excluded media by path spelling or resolved target; called before
    any probe, decode or model run."""
    if is_excluded(relative_path) or resolves_into_excluded(data_root, relative_path):
        raise PermissionError(f"Refusing to process excluded path: {relative_path}")


def item_id(relative_path: str) -> str:
    return hashlib.sha1(relative_path.encode("utf-8")).hexdigest()[:16]


def media_kind(name: str) -> str | None:
    suffix = os.path.splitext(name)[1].lower()
    if suffix in VIDEO_EXTENSIONS:
        return "video"
    if suffix in IMAGE_EXTENSIONS:
        return "image"
    return None


def normalize_prefix(prefix: str | None) -> str | None:
    """Accept `movies_fight`, `video/movies_fight`, `films`, or trailing slashes."""
    if not prefix:
        return None
    cleaned = prefix.strip().strip("/")
    if cleaned.split("/", 1)[0] not in ITEM_ROOTS:
        cleaned = f"{MEDIA_ROOT}/{cleaned}"
    return cleaned


def matches_prefix(relative_path: str, prefix: str | None) -> bool:
    return prefix is None or relative_path == prefix or relative_path.startswith(prefix + "/")


class Layout:
    """Resolved locations under one data root."""

    def __init__(self, data_root: Path) -> None:
        self.data_root = data_root
        self.results = data_root / RESULTS_DIR
        self.inventory = self.results / "inventory.json"
        self.frames = self.results / "frames"
        self.models = self.results / "models"
        self.summary = self.results / "summary.json"

    def model_jsonl(self, model_id: str) -> Path:
        return self.models / f"{model_id}.jsonl"

    def model_meta(self, model_id: str) -> Path:
        return self.models / f"{model_id}.meta.json"

    def load_inventory(self) -> dict[str, Any]:
        if not self.inventory.is_file():
            raise FileNotFoundError(f"Run `inventory` first: {self.inventory} is missing")
        return json.loads(self.inventory.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    """Write indented JSON atomically so readers never see a half-written file.

    Each write uses its own private temporary file, so the CLI and the review server
    may replace the same file concurrently.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(payload, indent=2) + "\n").encode("utf-8")
    atomic_write_bytes(path, data, prefix=f".{path.name}-", suffix=".part", mode=0o644)
