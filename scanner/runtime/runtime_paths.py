"""Resolve Editarr's writable and immutable runtime locations.

This module deliberately imports only the standard library.  It is safe to use from
an executable bootstrap before configuration, model libraries, or web application
modules have been imported.  Resolution is pure: directory creation belongs to the
consumer that needs to write a particular location.
"""

from __future__ import annotations

import os
import stat
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Mapping

PathValue = Path | PureWindowsPath
USER_PRIVATE_DIRECTORY_MODE = 0o700
"""Permission mode owners should request for private runtime directories on POSIX."""


class RuntimePathConfigurationError(ValueError):
    """An operator supplied an invalid runtime-path override."""


@dataclass(frozen=True, slots=True)
class RuntimePaths:
    """All root locations used by one Editarr process.

    ``PureWindowsPath`` is returned only when resolving a Windows target from a
    non-Windows test host.  On the target host every field is a concrete ``Path``.
    """

    data_dir: PathValue
    config_dir: PathValue
    log_dir: PathValue
    cache_dir: PathValue
    temp_dir: PathValue
    resources_dir: PathValue

    @property
    def model_cache(self) -> PathValue:
        """Return the Editarr-owned root for downloadable model artifacts.

        Model packages and their library metadata are regenerable cache content.
        Keeping this as a derived root preserves the public resolver contract while
        giving model consumers one stable location independent of durable data.
        """

        return self.cache_dir / "models"


_active_runtime_paths: RuntimePaths | None = None


def _environment_value(environment: Mapping[str, str], name: str) -> str | None:
    """Read an environment value case-insensitively, matching Windows behavior."""

    normalized_name = name.casefold()
    for key, value in environment.items():
        if key.casefold() == normalized_name:
            return value
    return None


def _target_platform(platform: str | None) -> str:
    value = (sys.platform if platform is None else platform).casefold()
    if value in {"darwin", "macos"}:
        return "macos"
    if value in {"win32", "windows"}:
        return "windows"
    if value.startswith("linux"):
        return "linux"
    raise RuntimePathConfigurationError(f"Unsupported Editarr runtime platform: {platform!r}")


def _path_type(platform: str):
    return PureWindowsPath if platform == "windows" else Path


def _as_path(value: str | os.PathLike[str], *, platform: str) -> PathValue:
    return _path_type(platform)(value)


def _absolute_override(
    value: str | os.PathLike[str], *, source: str, platform: str
) -> PathValue:
    path = _as_path(value, platform=platform)
    if not path.is_absolute():
        raise RuntimePathConfigurationError(
            f"{source} must be an absolute path; received {str(value)!r}. "
            "Use an absolute path such as /path/to/editarr-data."
        )
    return path


def source_checkout_root() -> Path | None:
    """Return the source root when this module is running from this checkout.

    This inspects the installed module location, never the process working directory.
    It is an explicit bootstrap aid for development entry points, not native-path
    detection for installed builds.
    """

    candidate = Path(__file__).resolve().parents[2]
    if (candidate / "pyproject.toml").is_file() and (candidate / "scanner").is_dir():
        return candidate
    return None


def is_frozen_application() -> bool:
    """Return whether this process is executing from a frozen application.

    Freezers set ``sys.frozen`` before application code runs.  Keeping that check in
    this standard-library-only module gives the executable bootstrap and resource
    consumers one small adapter instead of scattering freezer details through the
    application.
    """

    return bool(getattr(sys, "frozen", False))


def resolve_resource_directory(
    *,
    frozen: bool | None = None,
    executable: str | os.PathLike[str] | None = None,
    bundle_directory: str | os.PathLike[str] | None = None,
) -> Path:
    """Locate immutable source or frozen resources without using writable roots.

    PyInstaller exposes its collected files through ``sys._MEIPASS``.  The
    executable-parent fallback keeps this adapter usable with other one-directory
    freezers. Neither
    branch creates directories or treats runtime data/cache as a resource location.
    """

    frozen_mode = is_frozen_application() if frozen is None else frozen
    if frozen_mode:
        bundle = bundle_directory
        if bundle is None:
            bundle = getattr(sys, "_MEIPASS", None)
        if bundle is not None:
            return Path(bundle)
        return Path(sys.executable if executable is None else executable).resolve().parent

    checkout = source_checkout_root()
    if checkout is not None:
        return checkout
    return Path(__file__).resolve().parents[2]


def _native_defaults(
    *,
    platform: str,
    environment: Mapping[str, str],
    home_dir: str | os.PathLike[str] | None,
    temporary_dir: str | os.PathLike[str] | None,
) -> tuple[PathValue, PathValue, PathValue, PathValue, PathValue]:
    """Resolve platform-specific private data, cache, log, and temporary roots."""
    path_type = _path_type(platform)
    home = path_type(home_dir) if home_dir is not None else path_type(Path.home())
    temporary = (
        path_type(temporary_dir)
        if temporary_dir is not None
        else path_type(tempfile.gettempdir())
    )

    if platform == "macos":
        library = home / "Library"
        return (
            library / "Application Support" / "Editarr",
            library / "Application Support" / "Editarr",
            library / "Logs" / "Editarr",
            library / "Caches" / "Editarr",
            temporary,
        )

    if platform == "windows":
        local_app_data_value = _environment_value(environment, "LOCALAPPDATA")
        local_app_data = (
            path_type(local_app_data_value)
            if local_app_data_value and local_app_data_value.strip()
            else home / "AppData" / "Local"
        )
        return (
            local_app_data / "Editarr" / "Data",
            local_app_data / "Editarr" / "Config",
            local_app_data / "Editarr" / "Logs",
            local_app_data / "Editarr" / "Cache",
            temporary,
        )

    def xdg_root(name: str, fallback: PathValue) -> PathValue:
        value = _environment_value(environment, name)
        if value is None or not value.strip():
            return fallback
        candidate = path_type(value)
        if not candidate.is_absolute():
            raise RuntimePathConfigurationError(f"{name} must be an absolute path when set")
        return candidate

    data_root = xdg_root("XDG_DATA_HOME", home / ".local" / "share")
    config_root = xdg_root("XDG_CONFIG_HOME", home / ".config")
    state_root = xdg_root("XDG_STATE_HOME", home / ".local" / "state")
    cache_root = xdg_root("XDG_CACHE_HOME", home / ".cache")
    runtime_value = _environment_value(environment, "XDG_RUNTIME_DIR")
    runtime_root = (
        path_type(runtime_value)
        if runtime_value is not None
        and runtime_value.strip()
        and path_type(runtime_value).is_absolute()
        else temporary
    )
    return (
        data_root / "editarr",
        config_root / "editarr",
        state_root / "editarr",
        cache_root / "editarr",
        runtime_root,
    )


def resolve_runtime_paths(
    *,
    data_dir: str | os.PathLike[str] | None = None,
    environment: Mapping[str, str] | None = None,
    platform: str | None = None,
    development: bool = False,
    checkout_root: str | os.PathLike[str] | None = None,
    resources_dir: str | os.PathLike[str] | None = None,
    frozen: bool | None = None,
    executable: str | os.PathLike[str] | None = None,
    bundle_directory: str | os.PathLike[str] | None = None,
    home_dir: str | os.PathLike[str] | None = None,
    temporary_dir: str | os.PathLike[str] | None = None,
) -> RuntimePaths:
    """Resolve paths with CLI, environment, then development/native precedence.

    ``data_dir`` represents the public ``--data-dir`` value.  Its override affects
    only durable state.  An absolute ``EDITARR_LOG_DIR`` moves only logs, so an
    isolated run keeps them beside its scratch data.  All parameters provide
    dependency injection for tests; this function performs no filesystem writes
    or existence checks.
    """

    target = _target_platform(platform)
    env = os.environ if environment is None else environment
    native_data, config_dir, log_dir, cache_dir, temp_dir = _native_defaults(
        platform=target,
        environment=env,
        home_dir=home_dir,
        temporary_dir=temporary_dir,
    )
    path_type = _path_type(target)

    if development:
        root = path_type(checkout_root) if checkout_root is not None else source_checkout_root()
        if root is None:
            raise RuntimePathConfigurationError(
                "Development runtime paths require an explicit source checkout root"
            )
        default_data = root / "data"
    else:
        default_data = native_data

    environment_data = _environment_value(env, "EDITARR_DATA_DIR")
    if data_dir is not None:
        resolved_data = _absolute_override(data_dir, source="--data-dir", platform=target)
    elif environment_data is not None and environment_data.strip():
        resolved_data = _absolute_override(
            environment_data, source="EDITARR_DATA_DIR", platform=target
        )
    else:
        resolved_data = default_data

    environment_logs = _environment_value(env, "EDITARR_LOG_DIR")
    if environment_logs is not None and environment_logs.strip():
        log_dir = _absolute_override(environment_logs, source="EDITARR_LOG_DIR", platform=target)

    if resources_dir is not None:
        resolved_resources = _as_path(resources_dir, platform=target)
    elif development:
        resolved_resources = path_type(checkout_root) if checkout_root is not None else root
    else:
        resolved_resources = path_type(
            resolve_resource_directory(
                frozen=frozen,
                executable=executable,
                bundle_directory=bundle_directory,
            )
        )

    return RuntimePaths(
        data_dir=resolved_data,
        config_dir=config_dir,
        log_dir=log_dir,
        cache_dir=cache_dir,
        temp_dir=temp_dir,
        resources_dir=resolved_resources,
    )


def initialize_runtime_paths(**kwargs: object) -> RuntimePaths:
    """Initialize the process runtime paths once, before configuration imports."""

    global _active_runtime_paths
    if _active_runtime_paths is None:
        _active_runtime_paths = resolve_runtime_paths(**kwargs)
    return _active_runtime_paths


def get_runtime_paths() -> RuntimePaths:
    """Return the initialized paths, using native defaults for direct library imports."""

    return initialize_runtime_paths()


def ensure_user_private_directory(path: Path) -> Path:
    """Create one runtime directory with owner-only permissions where supported.

    Callers own the choice of which runtime root to create.  This helper deliberately
    performs no path resolution, so importing or resolving ``RuntimePaths`` remains
    side-effect free.  Windows ACL handling is left to the platform's per-user
    AppData roots; POSIX permission bits are tightened when available.
    """

    path.mkdir(mode=USER_PRIVATE_DIRECTORY_MODE, parents=True, exist_ok=True)
    try:
        if stat.S_IMODE(path.stat().st_mode) & 0o077:
            path.chmod(USER_PRIVATE_DIRECTORY_MODE)
    except OSError:
        # Some mounted filesystems do not implement POSIX permission bits.  Secret
        # consumers perform their stricter verification separately.
        pass
    return path


def initialize_runtime_paths_from_argv(
    argv: list[str] | tuple[str, ...], *, development: bool, checkout_root: Path | None
) -> RuntimePaths:
    """Perform the minimal early ``--data-dir`` parse required before Settings exists."""

    data_dir: str | None = None
    for index, argument in enumerate(argv):
        if argument == "--data-dir":
            if index + 1 >= len(argv) or argv[index + 1].startswith("-"):
                # Let argparse report the public CLI syntax error after bootstrap.
                break
            data_dir = argv[index + 1]
            break
        if argument.startswith("--data-dir="):
            data_dir = argument.split("=", maxsplit=1)[1]
            break
    return initialize_runtime_paths(
        data_dir=data_dir,
        development=development,
        checkout_root=checkout_root,
        resources_dir=checkout_root if development else None,
    )


def bootstrap_entrypoint_runtime_paths(
    argv: list[str] | tuple[str, ...] | None = None,
) -> RuntimePaths:
    """Initialize source-development or native paths for a Python entry point."""

    frozen = is_frozen_application()
    checkout_root = None if frozen else source_checkout_root()
    return initialize_runtime_paths_from_argv(
        tuple(sys.argv[1:] if argv is None else argv),
        development=not frozen and checkout_root is not None,
        checkout_root=checkout_root,
    )


def _reset_runtime_paths_for_testing() -> None:
    """Clear process state for isolated unit tests; not a production API."""

    global _active_runtime_paths
    _active_runtime_paths = None
