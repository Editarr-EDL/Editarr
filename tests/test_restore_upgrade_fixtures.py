"""A backup taken at any schema revision below head is refused without changing the target.

Each fixture is a populated legacy database wrapped into a real archive.
"""

import pytest

from scanner.backup.archive import export_backup
from scanner.backup.restore import preview_restore
from scanner.backup.restore_target import RestoreRefused
from tests.activity_migration_support import SUPPORTED, build_activity_database
from tests.test_activity_migration import _archive_with_database
from tests.test_backup_activation import _fixture
from tests.test_backup_restore import _active_hashes


@pytest.mark.parametrize("revision", SUPPORTED)
def test_every_older_revision_backup_is_refused_before_any_change(tmp_path, revision):
    archive, target = _fixture(tmp_path)
    legacy = tmp_path / "legacy" / "scanner.db"
    legacy.parent.mkdir()
    build_activity_database(legacy, tmp_path / "media", revision)
    export_backup(tmp_path / "source" / "scanner.db", tmp_path / "source", archive)
    rewritten = tmp_path / "legacy.backup"
    _archive_with_database(archive, legacy, revision or "unversioned-v1", rewritten)
    before = _active_hashes(target)

    with pytest.raises(RestoreRefused) as refused:
        with preview_restore(rewritten, target):
            pass

    assert refused.value.reason == "restore_backup_schema_unsupported"
    assert f"schema {revision or 'unversioned-v1'}." in str(refused.value)
    assert _active_hashes(target) == before
