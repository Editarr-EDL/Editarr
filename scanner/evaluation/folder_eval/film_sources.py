"""Where the local film files and the ground-truth CSV directory live.

The committed `films.json` names films only (title, year, IMDb id). Their file paths and
the ground-truth directory are machine-specific and resolve, highest precedence first:

1. `films --film IMDB_ID=PATH` (repeatable) and `films --ground-truth-dir DIR`;
2. `EDITARR_EVAL_GROUND_TRUTH_DIR` (ground-truth directory only);
3. a local JSON config: `films --local-config PATH`, else `EDITARR_EVAL_FILMS_LOCAL`,
   else `<data root>/films.local.json` (inside the git-ignored data root);
4. for the ground truth only, the copy a previous `films` run left in
   `<data root>/films/ground_truth/`.

The local config is `{"ground_truth_dir": DIR, "films": {KEY: PATH}}`, where KEY is an
IMDb id or a `Title (Year)` folder name; relative paths resolve against the config file.
A `--film` entry beats the config for the same film whichever key form either uses, and
keys that name no film in `films.json` are rejected.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .paths import FILMS_ROOT, Layout

GROUND_TRUTH_ENV = "EDITARR_EVAL_GROUND_TRUTH_DIR"
LOCAL_CONFIG_ENV = "EDITARR_EVAL_FILMS_LOCAL"
LOCAL_CONFIG_NAME = "films.local.json"
GROUND_TRUTH_COPY = "ground_truth"


class FilmSourceError(ValueError):
    """The ground truth or a film file is not configured or does not exist."""


@dataclass
class FilmSources:
    """Resolved ground-truth directory and film files keyed by IMDb id or folder name.

    `cli_files` (from `--film`) always win over `config_files` (the local config),
    whichever key form each uses.
    """

    ground_truth_dir: Path
    cli_files: dict[str, Path] = field(default_factory=dict)
    config_files: dict[str, Path] = field(default_factory=dict)

    def check_keys(self, films: Iterable[tuple[str, str]]) -> None:
        """Reject keys that name none of the `(imdb_id, folder name)` films listed."""
        known = {key for film in films for key in film}
        for origin, files in (("--film", self.cli_files), (LOCAL_CONFIG_NAME, self.config_files)):
            unknown = sorted(set(files) - known)
            if unknown:
                raise FilmSourceError(
                    f"{origin} names films not in films.json: {', '.join(unknown)} "
                    "(use an IMDb id or 'Title (Year)')"
                )

    def file_for(self, imdb_id: str, folder_name: str) -> Path:
        """The film's absolute file path: `--film` before the local config, each looked up
        by IMDb id then `Title (Year)`.

        Resolved so the data-root symlink to it never dangles; the file must exist.
        """
        path = next(
            (
                files[key]
                for files in (self.cli_files, self.config_files)
                for key in (imdb_id, folder_name)
                if key in files
            ),
            None,
        )
        if path is None:
            raise FilmSourceError(
                f"{folder_name} ({imdb_id}): no file configured; pass --film {imdb_id}=PATH "
                f"or add it to {LOCAL_CONFIG_NAME} (see docs/model-development.md)"
            )
        path = path.resolve()
        if not path.is_file():
            raise FilmSourceError(f"{folder_name} ({imdb_id}): {path} is not a file")
        return path


def parse_film_options(values: Iterable[str]) -> dict[str, Path]:
    """Parse repeated `KEY=PATH` options (KEY: IMDb id or `Title (Year)`)."""
    files: dict[str, Path] = {}
    for value in values:
        key, separator, path = value.partition("=")
        if not separator or not key.strip() or not path.strip():
            raise FilmSourceError(f"--film expects KEY=PATH, got {value!r}")
        files[key.strip()] = Path(path.strip()).expanduser()
    return files


def local_config_path(layout: Layout, explicit: Path | None = None) -> Path:
    if explicit is not None:
        return explicit
    configured = os.environ.get(LOCAL_CONFIG_ENV, "").strip()
    return Path(configured).expanduser() if configured else layout.data_root / LOCAL_CONFIG_NAME


def _load_local(path: Path, required: bool) -> tuple[Path | None, dict[str, Path]]:
    if not path.is_file():
        if required:
            raise FilmSourceError(f"Local films config {path} does not exist")
        return None, {}
    payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    base = path.resolve().parent

    def resolve(value: str) -> Path:
        candidate = Path(value).expanduser()
        return candidate if candidate.is_absolute() else base / candidate

    ground_truth = payload.get("ground_truth_dir")
    files = {key: resolve(value) for key, value in (payload.get("films") or {}).items()}
    return (resolve(ground_truth) if ground_truth else None), files


def resolve_sources(
    layout: Layout,
    *,
    ground_truth_dir: Path | None = None,
    film_files: dict[str, Path] | None = None,
    local_config: Path | None = None,
) -> FilmSources:
    """Combine CLI values, environment and the local config by the documented precedence."""
    config_path = local_config_path(layout, local_config)
    local_ground_truth, config_files = _load_local(config_path, required=local_config is not None)
    copy = layout.data_root / FILMS_ROOT / GROUND_TRUTH_COPY
    candidates = (
        ground_truth_dir,
        Path(os.environ[GROUND_TRUTH_ENV]).expanduser()
        if os.environ.get(GROUND_TRUTH_ENV, "").strip()
        else None,
        local_ground_truth,
        copy if (copy / "movies.csv").is_file() else None,
    )
    chosen = next((candidate for candidate in candidates if candidate is not None), None)
    if chosen is None:
        raise FilmSourceError(
            "No ground-truth directory: pass --ground-truth-dir, set "
            f"{GROUND_TRUTH_ENV}, or set ground_truth_dir in {config_path}"
        )
    if not (chosen / "movies.csv").is_file():
        raise FilmSourceError(f"{chosen} has no movies.csv")
    return FilmSources(chosen, dict(film_files or {}), config_files)
