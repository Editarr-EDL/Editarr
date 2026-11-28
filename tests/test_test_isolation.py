"""The suite never uses the installed app's per-user Editarr locations."""

from __future__ import annotations

import os
import pwd
from pathlib import Path

from scanner.runtime.runtime_paths import get_runtime_paths
from scanner.settings.config import settings


def test_settings_resolve_inside_the_isolated_test_root():
    real_home = Path(pwd.getpwuid(os.getuid()).pw_dir).resolve()
    root = Path(os.environ["HOME"]).resolve().parent
    paths = get_runtime_paths()
    for path in (
        settings.data_dir, settings.log_dir, settings.cache_dir, settings.model_cache,
        paths.data_dir, paths.log_dir, paths.cache_dir, paths.config_dir,
    ):
        resolved = Path(path).resolve()
        assert resolved.is_relative_to(root), path
        assert not resolved.is_relative_to(real_home / "Library"), path
