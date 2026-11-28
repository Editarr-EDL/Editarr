"""Shared pytest configuration for the Python suite."""

import atexit
import os
import shutil
import tempfile
from pathlib import Path

REAL_HOME_ENV = "EDITARR_TESTS_REAL_HOME"


def _isolate_user_locations() -> Path:
    """Point every per-user Editarr location at a private scratch root.

    Runs before any `scanner` import, because settings resolve the data, log and cache
    directories from HOME (and the XDG/LOCALAPPDATA roots) at import time. Without this
    the suite, and the subprocesses it starts, would use and log into the installed
    app's directories. EDITARR_DATA_DIR and EDITARR_LOG_DIR are cleared rather than set,
    so the default-path logic under test resolves inside the scratch HOME. The real HOME
    is kept in EDITARR_TESTS_REAL_HOME for tools such as docker that need their config.
    """
    root = Path(tempfile.mkdtemp(prefix="editarr-tests-"))
    atexit.register(shutil.rmtree, root, ignore_errors=True)
    os.environ.setdefault(REAL_HOME_ENV, str(Path.home()))
    for name, child in (
        ("HOME", "home"),
        ("LOCALAPPDATA", "home/AppData/Local"),
        ("XDG_DATA_HOME", "xdg/data"),
        ("XDG_CONFIG_HOME", "xdg/config"),
        ("XDG_STATE_HOME", "xdg/state"),
        ("XDG_CACHE_HOME", "xdg/cache"),
    ):
        path = root / child
        path.mkdir(parents=True, exist_ok=True)
        os.environ[name] = str(path)
    for name in (
        "EDITARR_DATA_DIR", "EDITARR_LOG_DIR", "EDITARR_MODEL_CACHE",
        "EDITARR_DECISION_MODELS_DIR",
    ):
        os.environ.pop(name, None)
    return root


ISOLATED_ROOT = _isolate_user_locations()

import pytest  # noqa: E402

from scanner.persistence.migrations import preflight, runner  # noqa: E402
from scanner.runtime.maintenance import DatabaseMaintenance  # noqa: E402

_real_migrate_fresh_database = runner.migrate_fresh_database
_template: bytes | None = None
_use_real_migrations = False


def _template_bytes() -> bytes:
    """Migrate one head database per worker process and keep its bytes."""
    global _template
    if _template is None:
        directory = Path(tempfile.mkdtemp(prefix="editarr-template-"))
        try:
            _real_migrate_fresh_database(directory / "scanner.db", directory)
            _template = (directory / "scanner.db").read_bytes()
        finally:
            shutil.rmtree(directory, ignore_errors=True)
    return _template


def _migrate_fresh_from_template(
    database_path: Path, data_dir: Path, *, timeout: float = 10.0
) -> None:
    """Copy the head template under the same lock and refusals as the real entry point.

    Tests marked ``real_migrations`` and paths without a parent directory run the
    packaged migrations instead.
    """
    database_path = Path(database_path)
    if _use_real_migrations or not database_path.parent.is_dir():
        _real_migrate_fresh_database(database_path, data_dir, timeout=timeout)
        return
    template = _template_bytes()
    if database_path.exists() or database_path.is_symlink():
        raise FileExistsError("Fresh migration refuses an existing database")
    with DatabaseMaintenance(data_dir).exclusive(timeout=timeout):
        if database_path.exists() or database_path.is_symlink():
            raise FileExistsError("Fresh migration refuses an existing database")
        database_path.write_bytes(template)


# Test modules import this name after conftest loads, so they receive the fast path too.
runner.migrate_fresh_database = _migrate_fresh_from_template
preflight.migrate_fresh_database = _migrate_fresh_from_template


@pytest.fixture(autouse=True)
def _fresh_migration_mode(request):
    global _use_real_migrations
    _use_real_migrations = request.node.get_closest_marker("real_migrations") is not None
    yield
    _use_real_migrations = False
