"""MU-45: legacy activity without an account is never a restore target, before or after upgrade.

Fixture mu03-activity-fixture-2 without its administrator stands for an installation
whose data predates accounts. Restore through the library and the offline CLI must
refuse it by name and leave every byte in the data directory unchanged; the upgraded
data still waits for owner setup.
"""

import hashlib

import pytest

from scanner.backup.restore_target import HAS_WORK, RestoreRefused, require_empty_installation
from scanner.persistence.migrations import adoption, preflight
from tests.test_activity_migration import _built
from tests.test_backup_activation import _fixture
from tests.test_backup_cli import _run


def _tree(root):
    return {
        str(entry.relative_to(root)): hashlib.sha256(entry.read_bytes()).hexdigest()
        for entry in sorted(root.rglob("*"))
        if entry.is_file()
    }


@pytest.mark.parametrize("upgraded", [False, True])
@pytest.mark.parametrize(
    "revision", [None, "0009_readable_api_key", "0010_user_accounts", "0011_activity_ownership"]
)
def test_accountless_activity_refuses_restore_and_still_waits_for_owner_setup(
    tmp_path, monkeypatch, revision, upgraded
):
    path, _sidecars = _built(tmp_path, revision, administrator=False)
    data = path.parent
    if upgraded:
        assert adoption.upgrade_existing_database(path, data).is_dir()
    (tmp_path / "backup").mkdir()
    archive, _target = _fixture(tmp_path / "backup")
    before = _tree(data)

    with pytest.raises(RestoreRefused) as refused:
        require_empty_installation(data, path)
    assert refused.value.reason == HAS_WORK
    result = _run(data, "restore", str(archive), "--confirm")
    assert (result.returncode, HAS_WORK in result.stderr) == (1, True), result.stderr
    assert _tree(data) == before

    if upgraded:
        monkeypatch.setattr(preflight, "database_location", lambda: (path, data))
        with pytest.raises(RuntimeError, match="Owner setup is required"):
            preflight.require_owner_setup()
