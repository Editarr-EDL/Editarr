"""Copy the static viewer into `_results/` next to the data it reads."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from .paths import Layout

log = logging.getLogger(__name__)
VIEWER_SOURCE = Path(__file__).with_name("viewer")


def install_viewer(layout: Layout, source: Path = VIEWER_SOURCE) -> list[str]:
    """Recursively copy `viewer/` into the results folder; warn and return [] if absent."""
    if not source.is_dir():
        log.warning("Viewer folder %s not found; skipping viewer install", source)
        return []
    layout.results.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        source,
        layout.results,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns(".*", "__pycache__"),
    )
    copied = sorted(
        path.relative_to(source).as_posix()
        for path in source.rglob("*")
        if path.is_file() and not any(
            part.startswith(".") or part == "__pycache__" for part in path.relative_to(source).parts
        )
    )
    log.info("Viewer installed: %s → open %s", ", ".join(copied), layout.results / "index.html")
    return copied
