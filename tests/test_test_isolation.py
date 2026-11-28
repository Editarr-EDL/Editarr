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


def test_settings_resolve_inside_the_scratch_root():
    root = _root()
    real_home = Path(os.environ[REAL_HOME_ENV]).resolve()
    paths = get_runtime_paths()
    for path in (
        settings.data_dir, settings.log_dir, settings.cache_dir, settings.model_cache,
        paths.data_dir, paths.log_dir, paths.cache_dir, paths.config_dir,
    ):
        resolved = Path(path).resolve()
        assert resolved.is_relative_to(root), path
        assert not resolved.is_relative_to(real_home), path
