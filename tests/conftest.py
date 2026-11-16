"""Shared pytest configuration for the Python suite."""

import shutil
import tempfile
from pathlib import Path

import pytest

from scanner.persistence.migrations import preflight, runner
from scanner.runtime.maintenance import DatabaseMaintenance

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
