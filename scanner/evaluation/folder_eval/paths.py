"""Data locations, media extensions, item identifiers, and the hard content exclusion."""

from __future__ import annotations

import hashlib
import json
import os
import stat
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


def resolves_into_excluded(data_root: Path, relative_path: str | Path) -> bool:
    """Return whether a data-root path's real target (after symlinks) is in an excluded
    folder; links to files outside the data root, such as films, are allowed. An
    absolute `relative_path` (e.g. a configured film file) is checked as is."""
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


def frame_reference_ok(item_id: str, relative_file: str) -> bool:
    """Whether an inventory frame reference names a file directly in `frames/<item id>/`:
    relative, POSIX, no `..`, `.` or empty parts, so it can never point elsewhere."""
    if not isinstance(relative_file, str) or "\\" in relative_file:
        return False
    # Split the raw text: PurePosixPath would silently drop `.` and empty parts.
    parts = relative_file.split("/")
    return (
        len(parts) == 3
        and parts[:2] == ["frames", item_id]
        and all(part not in ("", ".", "..") for part in parts[1:])
    )


def cached_frame_ok(results: Path, item_id: str, relative_file: str) -> bool:
    """Whether `results/relative_file` is a usable cached frame of item `item_id`: a
    well-formed reference (see `frame_reference_ok`) to a non-empty regular file, with no
    symlink from `_results` down to it (so neither a planted link, a linked frame folder,
    nor a reference such as `../video/nude/x.jpg` feeds another file to a model)."""
    if not frame_reference_ok(item_id, relative_file):
        return False
    path = results / relative_file
    try:
        info = os.lstat(path)
    except OSError:
        return False
    if not stat.S_ISREG(info.st_mode) or info.st_size == 0:
        return False
    return not has_symlink_below(results.parent, path)


def ensure_frames_cached(results: Path, item_id: str, frames: list[dict[str, Any]]) -> None:
    """Refuse to score frames that are not genuine cached frame files of `item_id`."""
    for frame in frames:
        if not cached_frame_ok(results, item_id, frame["file"]):
            raise PermissionError(
                f"Refusing frame {frame['file']}: not a regular file in frames/{item_id}/"
            )


def has_symlink_below(root: Path, path: str | os.PathLike[str]) -> bool:
    """Whether `path` (inside `root`) or any folder between them is a symlink."""
    expected = os.path.join(os.path.realpath(root), os.path.relpath(path, root))
    return os.path.realpath(path) != os.path.normpath(expected)


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


class MissingInput(FileNotFoundError):
    """A step's input (inventory or subset) does not exist yet; the message says which
    command creates it."""


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
            raise MissingInput(
                f"Run `inventory` (or `films`) first: {self.inventory} is missing"
            )
        return json.loads(self.inventory.read_text(encoding="utf-8"))


def ensure_results_destination(path: Path) -> None:
    """Refuse to write `path` when it lies in a `_results` tree redirected by a symlink:
    `_results` itself or any folder between it and `path`. A link at `path` itself is
    replaced by atomic writes and refused by `open_append`, never followed.

    Checked right before writing; the data root is assumed to be writable only by its
    owner, so this guards against leftover or planted links, not a concurrent attacker.
    """
    root = next((parent for parent in path.parents if parent.name == RESULTS_DIR), None)
    if root is not None and has_symlink_below(root.parent, path.parent):
        raise PermissionError(f"Refusing to write through a symlink in {RESULTS_DIR}: {path}")


def open_regular(path: Path, flags: int) -> int:
    """Open (or create) `path` as a regular file inside a contained `_results` tree, never
    through a symlink, even where O_NOFOLLOW is missing (Windows): the name must not be a
    link beforehand, and after opening it must still name the very file opened (same
    device and inode) before the caller writes anything."""
    ensure_results_destination(path)
    if os.path.islink(path):
        raise PermissionError(f"Refusing to open through a symlink: {path}")
    descriptor = os.open(path, flags | getattr(os, "O_NOFOLLOW", 0), 0o644)
    try:
        opened, named = os.fstat(descriptor), os.lstat(path)
        same = (opened.st_dev, opened.st_ino) == (named.st_dev, named.st_ino)
        if not stat.S_ISREG(opened.st_mode) or stat.S_ISLNK(named.st_mode) or not same:
            raise PermissionError(f"Refusing to open {path}: not the regular file checked")
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def open_append(path: Path) -> int:
    """Open `path` for single-write appends (see `open_regular`)."""
    return open_regular(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT)


def write_json(path: Path, payload: Any) -> None:
    """Write indented JSON atomically so readers never see a half-written file.

    Each write uses its own private temporary file in the same (checked) folder, so the
    CLI and the review server may replace the same file concurrently.
    """
    ensure_results_destination(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    ensure_results_destination(path)
    data = (json.dumps(payload, indent=2) + "\n").encode("utf-8")
    atomic_write_bytes(path, data, prefix=f".{path.name}-", suffix=".part", mode=0o644)
