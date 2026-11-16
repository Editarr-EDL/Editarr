"""Validation shared by the release wrapper and Hatch build hook."""

from __future__ import annotations

import ast
import json
import re
import subprocess
import tomllib
from pathlib import Path

_BOOTSTRAP_PATTERN = re.compile(
    r"<script\b(?=[^>]*\bid\s*=\s*['\"]editarr-bootstrap['\"])"
    r"(?=[^>]*\btype\s*=\s*['\"]application/json['\"])[^>]*>.*?</script>",
    re.IGNORECASE | re.DOTALL,
)
_REQUIRED_ROOT_FILES = {
    "apple-touch-icon.png",
    "editarr-app-icon.png",
    "favicon-16.png",
    "favicon-32.png",
    "favicon.ico",
    "index.html",
    "samwise-app-icon.png",
}


def validate_release_versions(root: Path) -> str:
    """Require the release authority, Python facade and frontend metadata to agree."""
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    release = project["project"]["version"]
    web = json.loads((root / "web/package.json").read_text(encoding="utf-8"))
    module = ast.parse((root / "scanner/__init__.py").read_text(encoding="utf-8"))
    python_version = next(
        (statement.value.value for statement in module.body
         if isinstance(statement, ast.Assign)
         and any(isinstance(target, ast.Name) and target.id == "__version__"
                 for target in statement.targets)
         and isinstance(statement.value, ast.Constant)
         and isinstance(statement.value.value, str)),
        None,
    )
    if release != web["version"] or release != python_version:
        raise RuntimeError("Release version drift: pyproject.toml, scanner and web must agree")
    return release


def source_identity(root: Path) -> tuple[str | None, bool | None]:
    """Return a known revision/dirty state, or unknown without a Git worktree."""
    try:
        top_level = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"], cwd=root, check=True,
            capture_output=True, text=True,
        ).stdout.strip()
        if Path(top_level).resolve() != root.resolve():
            return None, None
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"], cwd=root, check=True, capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None, None
    return (revision if len(revision) == 40 else None), bool(dirty)


def validate_frontend(frontend: Path) -> None:
    """Reject an absent, incomplete, or unsafe release-asset staging tree."""

    hint = "Run `uv run python scripts/build_release.py` from the source checkout."
    if not frontend.is_dir() or frontend.is_symlink():
        raise RuntimeError(f"Required production frontend is unavailable at {frontend}. {hint}")

    paths = list(frontend.rglob("*"))
    if any(path.is_symlink() for path in paths):
        raise RuntimeError(f"Production frontend staging contains a symlink. {hint}")
    if any(path.is_file() and path.name in {".env", ".env.local"} for path in paths):
        raise RuntimeError(f"Production frontend staging contains an environment file. {hint}")

    missing = sorted(name for name in _REQUIRED_ROOT_FILES if not (frontend / name).is_file())
    if missing:
        raise RuntimeError(
            f"Production frontend staging is incomplete; missing: {', '.join(missing)}. {hint}"
        )

    index_html = (frontend / "index.html").read_text(encoding="utf-8")
    if len(_BOOTSTRAP_PATTERN.findall(index_html)) != 1:
        raise RuntimeError(
            f"Production frontend entry HTML lacks one Editarr runtime bootstrap. {hint}"
        )
    assets = frontend / "assets"
    if not assets.is_dir() or not any(
        path.is_file() and path.suffix == ".js" for path in assets.iterdir()
    ):
        raise RuntimeError(f"Production frontend staging has no built JavaScript. {hint}")
    if not any(path.is_file() and path.suffix == ".css" for path in assets.iterdir()):
        raise RuntimeError(f"Production frontend staging has no built CSS. {hint}")
