"""The suite never uses the installed app's per-user Editarr locations."""

from __future__ import annotations

import os
from pathlib import Path

from scanner.runtime.runtime_paths import get_runtime_paths
from scanner.settings.config import settings
from tests.conftest import CLEARED_VARIABLES, ISOLATED_LOCATIONS, REAL_HOME_ENV


def _root() -> Path:
    return Path(os.environ["HOME"]).resolve().parent


def test_user_location_variables_point_into_the_scratch_root():
    root = _root()
    for name, child in ISOLATED_LOCATIONS.items():
        assert Path(os.environ[name]).resolve() == (root / child).resolve(), name
    for name in CLEARED_VARIABLES:
        assert name not in os.environ, name
    assert not Path(os.environ[REAL_HOME_ENV]).resolve().is_relative_to(root)


def _editarr_dirs(home: Path) -> list[Path]:
    """The installed app's per-user folders under a real home, on every platform."""
    return [
        home / "Library" / "Application Support" / "Editarr",
        home / "Library" / "Caches" / "Editarr",
        home / "Library" / "Logs" / "Editarr",
        home / "AppData" / "Local" / "Editarr",
        home / ".local" / "share" / "editarr",
        home / ".local" / "state" / "editarr",
        home / ".config" / "editarr",
        home / ".cache" / "editarr",
    ]


def isolation_problem(path: Path, root: Path, real_home: Path) -> str | None:
    """Why `path` escapes the scratch root, or None. The root itself may sit beneath the
    real home (Windows keeps %TEMP% under the profile), so only containment counts."""
    path = path.resolve()
    if path.is_relative_to(root):
        return None
    if any(path.is_relative_to(app) for app in _editarr_dirs(real_home)):
        return "inside the installed app's folders"
    return "outside the scratch root"


def test_isolation_allows_a_scratch_root_beneath_the_real_home(tmp_path):
    home = tmp_path / "Users" / "ada"
    root = home / "AppData" / "Local" / "Temp" / "editarr-tests-x"
    inside = root / "home" / "AppData" / "Local" / "Editarr" / "Logs"
    assert isolation_problem(inside, root, home) is None
    real_logs = home / "AppData" / "Local" / "Editarr" / "Logs"
    assert isolation_problem(real_logs, root, home) == "inside the installed app's folders"
    assert isolation_problem(tmp_path / "elsewhere", root, home) == "outside the scratch root"


def test_settings_resolve_inside_the_scratch_root():
    root = _root()
    real_home = Path(os.environ[REAL_HOME_ENV]).resolve()
    paths = get_runtime_paths()
    for path in (
        settings.data_dir, settings.log_dir, settings.cache_dir, settings.model_cache,
        paths.data_dir, paths.log_dir, paths.cache_dir, paths.config_dir,
    ):
        assert isolation_problem(Path(path), root, real_home) is None, path
