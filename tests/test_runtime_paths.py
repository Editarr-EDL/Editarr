"""Focused, host-independent runtime-path resolution coverage."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path, PureWindowsPath

import pytest

from scanner.runtime.runtime_paths import (
    USER_PRIVATE_DIRECTORY_MODE,
    RuntimePathConfigurationError,
    resolve_resource_directory,
    resolve_runtime_paths,
    source_checkout_root,
)


def test_moved_runtime_module_still_finds_checkout_from_any_working_directory(
    monkeypatch, tmp_path
):
    checkout = Path(__file__).resolve().parents[1]
    monkeypatch.chdir(tmp_path)

    assert source_checkout_root() == checkout
    assert resolve_resource_directory(frozen=False) == checkout
    assert resolve_runtime_paths(development=True, environment={}).data_dir == checkout / "data"


def test_macos_defaults_match_the_native_path_contract():
    paths = resolve_runtime_paths(
        platform="darwin",
        environment={},
        home_dir=Path("/Users/Ada Example"),
        temporary_dir=Path("/tmp/editarr test"),
        resources_dir=Path("/Applications/Editarr.app/Contents/Resources"),
    )

    assert paths.data_dir == Path("/Users/Ada Example/Library/Application Support/Editarr")
    assert paths.config_dir == paths.data_dir
    assert paths.log_dir == Path("/Users/Ada Example/Library/Logs/Editarr")
    assert paths.cache_dir == Path("/Users/Ada Example/Library/Caches/Editarr")
    assert paths.model_cache == Path("/Users/Ada Example/Library/Caches/Editarr/models")
    assert paths.temp_dir == Path("/tmp/editarr test")
    assert paths.resources_dir == Path("/Applications/Editarr.app/Contents/Resources")


def test_windows_defaults_are_resolvable_on_a_non_windows_host():
    paths = resolve_runtime_paths(
        platform="win32",
        environment={"LOCALAPPDATA": r"C:\\Users\\Ada User\\AppData\\Local"},
        home_dir=PureWindowsPath(r"C:\\Users\\Ada User"),
        temporary_dir=PureWindowsPath(r"C:\\Temp Space"),
        resources_dir=PureWindowsPath(r"C:\\Program Files\\Editarr\\resources"),
    )

    assert paths.data_dir == PureWindowsPath(r"C:\\Users\\Ada User\\AppData\\Local\\Editarr\\Data")
    assert paths.config_dir == PureWindowsPath(
        r"C:\\Users\\Ada User\\AppData\\Local\\Editarr\\Config"
    )
    assert paths.log_dir == PureWindowsPath(r"C:\\Users\\Ada User\\AppData\\Local\\Editarr\\Logs")
    assert paths.cache_dir == PureWindowsPath(
        r"C:\\Users\\Ada User\\AppData\\Local\\Editarr\\Cache"
    )
    assert paths.model_cache == PureWindowsPath(
        r"C:\\Users\\Ada User\\AppData\\Local\\Editarr\\Cache\\models"
    )
    assert paths.temp_dir == PureWindowsPath(r"C:\\Temp Space")


def test_windows_long_absolute_override_preserves_windows_path_syntax():
    long_dir = PureWindowsPath(r"C:\\Users\\Ada User\\AppData\\Local") / ("nested-" * 40)
    paths = resolve_runtime_paths(platform="windows", data_dir=long_dir, environment={})

    assert len(str(long_dir)) > 260
    assert paths.data_dir == long_dir


def test_linux_xdg_defaults_handle_absent_and_empty_values():
    paths = resolve_runtime_paths(
        platform="linux",
        environment={
            "XDG_DATA_HOME": "",
            "XDG_CONFIG_HOME": "   ",
            "XDG_STATE_HOME": "",
            "XDG_CACHE_HOME": "",
            "XDG_RUNTIME_DIR": "",
        },
        home_dir=Path("/home/Ada User"),
        temporary_dir=Path("/tmp/editarr"),
        resources_dir=Path("/opt/editarr/resources"),
    )

    assert paths.data_dir == Path("/home/Ada User/.local/share/editarr")
    assert paths.config_dir == Path("/home/Ada User/.config/editarr")
    assert paths.log_dir == Path("/home/Ada User/.local/state/editarr")
    assert paths.cache_dir == Path("/home/Ada User/.cache/editarr")
    assert paths.model_cache == Path("/home/Ada User/.cache/editarr/models")
    assert paths.temp_dir == Path("/tmp/editarr")


def test_linux_xdg_roots_and_runtime_directory_are_used_when_present():
    paths = resolve_runtime_paths(
        platform="linux",
        environment={
            "XDG_DATA_HOME": "/var/data root",
            "XDG_CONFIG_HOME": "/var/config root",
            "XDG_STATE_HOME": "/var/state root",
            "XDG_CACHE_HOME": "/var/cache root",
            "XDG_RUNTIME_DIR": "/run/user/501",
        },
        home_dir=Path("/home/unused"),
        temporary_dir=Path("/tmp/editarr"),
    )

    assert paths.data_dir == Path("/var/data root/editarr")
    assert paths.config_dir == Path("/var/config root/editarr")
    assert paths.log_dir == Path("/var/state root/editarr")
    assert paths.cache_dir == Path("/var/cache root/editarr")
    assert paths.temp_dir == Path("/run/user/501")


def test_data_override_precedence_is_leaf_specific_and_unicode_safe():
    paths = resolve_runtime_paths(
        platform="linux",
        data_dir="/portable/Éditarr data",
        environment={
            "EDITARR_DATA_DIR": "/environment/data",
            "XDG_CACHE_HOME": "/cache-root",
        },
        home_dir=Path("/home/Ada"),
        temporary_dir=Path("/tmp"),
    )

    assert paths.data_dir == Path("/portable/Éditarr data")
    assert paths.cache_dir == Path("/cache-root/editarr")
    assert paths.log_dir == Path("/home/Ada/.local/state/editarr")


def test_log_override_moves_only_logs():
    paths = resolve_runtime_paths(
        platform="macos",
        environment={"EDITARR_LOG_DIR": "/scratch/run logs"},
        home_dir=Path("/Users/Ada"),
        temporary_dir=Path("/tmp"),
    )

    assert paths.log_dir == Path("/scratch/run logs")
    assert paths.data_dir == Path("/Users/Ada/Library/Application Support/Editarr")
    assert paths.cache_dir == Path("/Users/Ada/Library/Caches/Editarr")


@pytest.mark.parametrize(
    ("kwargs", "expected_source"),
    [
        ({"data_dir": "relative/data"}, "--data-dir"),
        ({"environment": {"EDITARR_DATA_DIR": "relative/data"}}, "EDITARR_DATA_DIR"),
        ({"environment": {"EDITARR_LOG_DIR": "relative/logs"}}, "EDITARR_LOG_DIR"),
    ],
)
def test_relative_explicit_path_overrides_are_actionable(kwargs, expected_source):
    with pytest.raises(RuntimePathConfigurationError, match=expected_source):
        resolve_runtime_paths(platform="linux", **kwargs)


def test_resolution_creates_no_directories_and_does_not_use_the_current_directory(
    monkeypatch, tmp_path
):
    arbitrary_cwd = tmp_path / "read only cwd"
    arbitrary_cwd.mkdir()
    arbitrary_cwd.chmod(0o500)
    target = tmp_path / "future data"
    monkeypatch.chdir(arbitrary_cwd)

    try:
        paths = resolve_runtime_paths(
            platform="linux",
            data_dir=target,
            environment={},
            home_dir=tmp_path / "home",
            temporary_dir=tmp_path / "temporary",
            resources_dir=tmp_path / "immutable resources",
        )
    finally:
        arbitrary_cwd.chmod(0o700)

    assert paths.data_dir == target
    assert not target.exists()
    assert not (arbitrary_cwd / "data").exists()
    assert not (tmp_path / "home").exists()
    assert USER_PRIVATE_DIRECTORY_MODE == 0o700


def test_resolver_import_has_no_filesystem_side_effect(tmp_path):
    home_dir = tmp_path / "new home"
    environment = os.environ | {"HOME": str(home_dir)}
    result = subprocess.run(
        [sys.executable, "-c", "import scanner.runtime.runtime_paths"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert not home_dir.exists()


def test_cli_bootstrap_resolves_data_before_settings_from_an_arbitrary_working_directory(
    tmp_path,
):
    data_dir = tmp_path / "data with spaces"
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import scanner.cli as cli; print(cli.settings.data_dir)",
            "--data-dir",
            str(data_dir),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(data_dir)
    assert not data_dir.exists()
