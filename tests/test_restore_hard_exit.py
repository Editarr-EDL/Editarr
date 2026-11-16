"""A hard exit at each restore activation boundary rolls back to an empty installation.

Worker admission stays closed from the crash until recovery, and again after a
completed restore until its reconciliation (MU-45).
"""

import subprocess
import sys

import pytest

from scanner.backup.activation import GENERATION, HOLD, JOURNAL, RECOVERY, recover_restore
from scanner.persistence.migrations import preflight
from scanner.persistence.migrations.preflight import inspect_schema, require_supported_schema
from tests.test_backup_activation import _ACTIVATE, _empty_again, _fixture, _restore


@pytest.mark.parametrize("database", [True, False])
@pytest.mark.parametrize("boundary", ["journal", "replace", "hold", "committed"])
def test_hard_exit_rollback_leaves_an_empty_installation(tmp_path, monkeypatch, boundary, database):
    archive, target = _fixture(tmp_path)
    monkeypatch.setattr(preflight, "database_location", lambda: (target / "scanner.db", target))
    if not database:
        (target / "scanner.db").unlink()
    script = (
        "import os, sys; from pathlib import Path; "
        "from scanner.backup import activation as a; "
        "from scanner.backup.restore import preview_restore; "
        "boundary = sys.argv[3]; "
        "original_record = a._record; original_apply = a._apply; "
        "def_record = lambda path, data: (original_record(path, data), os._exit(41)) "
        "if boundary == 'journal' and path.name == a.JOURNAL else "
        "(original_record(path, data), os._exit(43)) "
        "if boundary == 'hold' and path.name == a.HOLD else original_record(path, data); "
        "def_apply = lambda *args: (original_apply(*args), os._exit(42)) "
        "if boundary == 'replace' else original_apply(*args); "
        "original_rename = Path.rename; "
        "def_rename = lambda path, target: os._exit(44) if boundary == 'committed' "
        "and path.name == a.GENERATION else original_rename(path, target); "
        "a._record = def_record; a._apply = def_apply; Path.rename = def_rename; " + _ACTIVATE
    )
    result = subprocess.run(
        [sys.executable, "-c", script, str(archive), str(target), boundary], check=False
    )
    assert (
        result.returncode == {"journal": 41, "replace": 42, "hold": 43, "committed": 44}[boundary]
    )
    with pytest.raises(RuntimeError, match="Restore activation|reconciliation"):
        require_supported_schema()
    if boundary == "committed":
        assert not (target / JOURNAL).exists()
        assert (target / HOLD).exists()
        assert recover_restore(target / "scanner.db", target) is False
        with pytest.raises(RuntimeError, match="reconciliation before worker admission"):
            require_supported_schema()
        assert not (target / GENERATION).exists()
        assert (target / RECOVERY / "after" / "database.sqlite3").is_file()
        assert (target / "secrets/community/identity.json").exists()
        return
    assert (target / JOURNAL).exists()
    with pytest.raises(RuntimeError, match="Restore activation"):
        inspect_schema(target / "scanner.db", target)
    assert recover_restore(target / "scanner.db", target)
    _empty_again(target, database=database)
    if database:
        assert require_supported_schema() == "0031_explicit_filter_modes"
    _restore(archive, target)
    with pytest.raises(RuntimeError, match="reconciliation before worker admission"):
        require_supported_schema()
