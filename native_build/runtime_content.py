"""Exclude managed state and third-party development resources from native outputs."""

from __future__ import annotations

import shutil
from pathlib import Path

MANAGED_OUTPUT_NAMES = frozenset(
    {
        ".cache",
        ".pytest_cache",
        ".ruff_cache",
        ".uv-cache",
        ".venv",
        "data",
        "corpora",
        "credentials",
        "samwisenative",
    }
)
EXCLUDED_OUTPUT_PARTS = frozenset({"test", "tests", "corpora", "samwisenative"})
DEVELOPMENT_OUTPUT_PARTS = frozenset(
    {"coverage", "pytest", "pytest_asyncio", "pytest_cov", "pluggy", "iniconfig"}
)


def runtime_path_is_excluded(relative: Path) -> bool:
    parts = tuple(part.lower() for part in relative.parts)
    development_content = any(
        part in DEVELOPMENT_OUTPUT_PARTS
        or any(
            part.startswith(f"{name}-") and part.endswith(".dist-info")
            for name in DEVELOPMENT_OUTPUT_PARTS
        )
        for part in parts
    )
    return (
        bool(set(parts).intersection(EXCLUDED_OUTPUT_PARTS))
        or development_content
        or parts[:1] == ("data",)
        or relative.name.lower() in {".env", ".env.local"}
    )


def prune_excluded_runtime_content(runtime: Path) -> None:
    """Remove development tests/corpora introduced by broad third-party hooks."""

    for path in sorted(runtime.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        if runtime_path_is_excluded(path.relative_to(runtime)):
            if path.is_symlink():
                path.unlink()
            elif path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()


def assert_clean_runtime(runtime: Path) -> None:
    if not runtime.is_dir():
        raise RuntimeError(f"PyInstaller did not create expected runtime directory {runtime}")
    forbidden = [
        path.relative_to(runtime).as_posix()
        for path in runtime.rglob("*")
        if runtime_path_is_excluded(path.relative_to(runtime))
    ]
    if forbidden:
        raise RuntimeError(
            "native runtime captured managed/development state: " + ", ".join(forbidden)
        )
