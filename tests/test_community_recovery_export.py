"""MU-06: an upgrade recovery export publishes one consistent, durable database and identity.

Covers competing exporters, key changes after the upgrade, and retries after an
unconfirmed directory sync or a hard exit. Nothing here contacts a community service.
"""

import json
import sqlite3
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from scanner.persistence.migrations import adoption
from scanner.security import community_identity_homes as homes
from scanner.security.community_key_recovery import activate_recovery, bind_recovery
from scanner.security.community_key_rotation import activate_rotation, prepare_rotation
from scanner.security.private_identity_files import DIRECTORY, IdentityUnavailable
from tests.community_identity_migration_support import PERSONAL_FILTERS, identity_files
from tests.community_identity_migration_support import no_network as no_network
from tests.test_community_identity_migration import _built, _recovery_dir, _resolve
from tests.test_community_recovery import outcome as recovery_outcome
from tests.test_community_rotation import outcome as rotation_outcome

_SYNC = homes._sync_directory


def _upgraded(tmp_path):
    path, files = _built(tmp_path, PERSONAL_FILTERS)
    adoption.upgrade_existing_database(path, path.parent)
    home, identity = _resolve(path)
    return path, home, identity, files


def _is_staging(directory):
    return directory.parent.name == "secrets" and directory.name.startswith(".community-recovery")


_COMPETITOR = """
import sys, traceback
from pathlib import Path
from scanner.persistence.migrations import adoption
from scanner.security import community_identity_homes as homes

real = homes._sync_directory

def sync(directory):
    real(directory)
    if directory.parent.name == "secrets" and directory.name.startswith(".community-recovery"):
        Path(sys.argv[3]).touch()

homes._sync_directory = sync
source = Path(sys.argv[1])
try:
    adoption.export_upgrade_recovery(source, source.parent, Path(sys.argv[2]))
except Exception as error:
    print(type(error).__name__)
    sys.exit(7)
"""


def test_a_competing_export_process_cannot_pair_its_identity_with_another_database(
    tmp_path, monkeypatch
):
    first, _home, first_identity, _files = _upgraded(tmp_path / "first")
    second, _home, _identity, _files = _upgraded(tmp_path / "second")
    recovery = _recovery_dir(tmp_path)
    marker = tmp_path / "second-staged"
    first_staged = Event()
    real_sync = homes._sync_directory

    def wait_for_competitor(directory):
        real_sync(directory)
        if _is_staging(directory):
            first_staged.set()
            deadline = time.monotonic() + 8
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(0.05)

    monkeypatch.setattr(homes, "_sync_directory", wait_for_competitor)
    with ThreadPoolExecutor(max_workers=1) as executor:
        exported = executor.submit(
            adoption.export_upgrade_recovery, first, first.parent, recovery / "scanner.db"
        )
        assert first_staged.wait(20)
        competitor = subprocess.run(
            [
                sys.executable,
                "-c",
                _COMPETITOR,
                str(second),
                str(recovery / "scanner.db"),
                str(marker),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        exported = exported.result(timeout=60)

    published = json.loads((recovery / DIRECTORY / "identity.json").read_text())
    with sqlite3.connect(exported) as database:
        recorded = database.execute("SELECT installation_id FROM community_connections").fetchone()
    assert recorded == (first_identity.installation_id,)
    assert published["installation_id"] == first_identity.installation_id
    assert competitor.returncode == 7, competitor.stderr
    assert competitor.stdout.strip() == "FileExistsError"
    assert not marker.exists(), "the competitor staged while the destination was held"
    assert sorted(entry.name for entry in (recovery / "secrets").iterdir()) == [
        "community",
        "community-identities",
    ]


@pytest.mark.parametrize(
    ("change", "refused"),
    [("completed-recovery", False), ("pending-rotation", True), ("completed-rotation", True)],
)
def test_a_key_change_after_the_upgrade_exports_only_a_loadable_pair(tmp_path, change, refused):
    path, home, identity, _files = _upgraded(tmp_path)
    bind_recovery(home, "b" * 64)
    current = activate_recovery(home, recovery_outcome(path.parent)["result"])
    if change != "completed-recovery":
        current = prepare_rotation(home, "community.test")
        if change == "completed-rotation":
            current = activate_rotation(
                home, current.rotation["operationId"], rotation_outcome(current)
            )
    with sqlite3.connect(path) as database:
        database.execute("UPDATE community_connections SET key_id = ?", (current.key_id,))
    active = identity_files(home.root)
    recovery = _recovery_dir(tmp_path)

    if refused:
        with pytest.raises(ValueError, match="community identity"):
            adoption.export_upgrade_recovery(path, path.parent, recovery / "scanner.db")
        assert list(recovery.iterdir()) == []
    else:
        recovered = adoption.export_upgrade_recovery(path, path.parent, recovery / "scanner.db")
        adoption.upgrade_existing_database(recovered, recovery)
        restored = _resolve(recovered)[1]
        assert (restored.installation_id, restored.key_id) == (
            identity.installation_id,
            current.key_id,
        )
    assert identity_files(home.root) == active
    assert _resolve(path)[1].key_id == current.key_id


def _recording_syncs(monkeypatch, recovery, fail=False):
    calls = []

    def sync(directory):
        calls.append(directory.relative_to(recovery).as_posix())
        if fail and directory == recovery / "secrets":
            raise OSError("injected directory sync failure")
        _SYNC(directory)

    monkeypatch.setattr(homes, "_sync_directory", sync)
    return calls


def test_a_retry_after_a_failed_directory_sync_confirms_durability_first(tmp_path, monkeypatch):
    path, _home, _identity, files = _upgraded(tmp_path)
    recovery = _recovery_dir(tmp_path)
    failing = _recording_syncs(monkeypatch, recovery, fail=True)

    for _attempt in range(2):
        with pytest.raises(IdentityUnavailable):
            adoption.export_upgrade_recovery(path, path.parent, recovery / "scanner.db")
        assert identity_files(recovery / DIRECTORY) == files
        assert not (recovery / "scanner.db").exists()
    assert failing.count("secrets") == 2

    retried = _recording_syncs(monkeypatch, recovery)
    adoption.export_upgrade_recovery(path, path.parent, recovery / "scanner.db")
    assert retried[-2:] == [DIRECTORY.as_posix(), "secrets"]
    assert (recovery / "scanner.db").is_file()


def test_a_retry_after_a_hard_exit_past_publication_confirms_durability(tmp_path, monkeypatch):
    path, _home, _identity, files = _upgraded(tmp_path)
    recovery = _recovery_dir(tmp_path)
    script = (
        "import os, sys; from pathlib import Path; "
        "from scanner.persistence.migrations import adoption; "
        "real = os.rename; os.rename = lambda *a: (real(*a), os._exit(96)); "
        "source = Path(sys.argv[1]); "
        "adoption.export_upgrade_recovery(source, source.parent, Path(sys.argv[2]))"
    )
    result = subprocess.run(
        [sys.executable, "-c", script, str(path), str(recovery / "scanner.db")], check=False
    )
    assert result.returncode == 96
    calls = _recording_syncs(monkeypatch, recovery)

    adoption.export_upgrade_recovery(path, path.parent, recovery / "scanner.db")

    assert calls[-2:] == [DIRECTORY.as_posix(), "secrets"]
    assert identity_files(recovery / DIRECTORY) == files
