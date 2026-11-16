"""Hard exits around a replacement of returned owned bytes recover without manual repair."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scanner.db import MediaItemExportRow
from scanner.persistence.repository.export_recovery import (
    ExportRecoveryRequired,
    _journal,
    digest,
    recover_sidecar,
)
from tests.multi_user_access_support import ADMIN, USER
from tests.test_export_regeneration_boundaries import observe
from tests.test_export_regeneration_convergence import settle_due
from tests.test_export_regeneration_deletion import deletion_request, keyed_rows, unmounted
from tests.test_export_regeneration_failures import bump_policy
from tests.test_export_regeneration_ownership import relocate, sweep
from tests.test_personal_exports_http import app as app
from tests.test_personal_exports_http import publish, seeded_review
from tests.test_personal_exports_http import review_app as review_app

FORMATS = pytest.mark.parametrize("export_format", ["edl", "mcf"])
COMMITTED = pytest.mark.parametrize("committed", [False, True])
TARGETS = pytest.mark.parametrize("target", ["self", "item_default"])

# Each script replaces db.commit so the process exits after the durable file replacement,
# either before the transaction commits or immediately after it.
CRASH = """
import os, sys
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
engine = create_engine(sys.argv[1])
if sys.argv[2] == "before_replace":
    replace = os.replace
    def crash_before_replace(source, destination, *args, **kwargs):
        if str(source).endswith(".tmp"):
            os._exit(73)
        return replace(source, destination, *args, **kwargs)
    os.replace = crash_before_replace
def crashing(db):
    commit = db.commit
    def crash():
        if sys.argv[2] == "True":
            commit()
        os._exit(73)
    db.commit = crash
"""
REGENERATE = """
from scanner.persistence.repository.export_regeneration import claim_regeneration
from scanner.runtime.export_regeneration import settle_regeneration
with Session(engine) as db:
    job = claim_regeneration(db)
    assert job is not None
    crashing(db)
    settle_regeneration(db, job)
"""
PUBLISH = """
from scanner.models import ExportFormat
from scanner.persistence.repository.export_publication import publish_export
from scanner.persistence.repository.export_recovery import publication_lock
from scanner.security.access.vocabulary import Role
from tests.multi_user_access_support import build_access
review_id, export_format, target = sys.argv[3:]
with Session(engine) as db, publication_lock(db):
    crashing(db)
    publish_export(db, review_id, 0, ExportFormat(export_format),
                   access=build_access(Role.ADMIN), target=target)
"""


def hard_exit(app, operation, committed, *arguments):
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            CRASH + operation,
            str(app.accounts.engine.url),
            str(committed),
            *arguments,
        ],
        capture_output=True,
        check=False,
        timeout=60,
    )
    assert result.returncode == 73, result.stderr.decode()


def leftovers(path):
    return sorted(other.name for other in path.parent.glob(f".{path.name}.*"))


def admin_delete(app, export_id):
    status = observe(app, export_id)[0]
    return app.send(
        "DELETE",
        f"/api/exports/{export_id}?revision={status['publication_revision']}"
        f"&publication_id={status['publication_id']}",
        app.accounts.unsafe_cookie(ADMIN),
    )


def deleted_and_returned(app, review, export_format, target):
    """Publish, DELETE while the path is absent, then return the owned bytes."""
    export_id = publish(app, review, export_format, user=ADMIN, target=target).json()["id"]
    _status, original, path = observe(app, export_id)
    path.unlink()
    assert admin_delete(app, export_id).status_code == 204
    path.write_bytes(original)
    return export_id, path


@FORMATS
@TARGETS
@pytest.mark.parametrize("returned", [False, True], ids=["serving", "deleted-returned"])
def test_before_rename_hard_exit_clears_temporary_on_recovery(app, export_format, target, returned):
    review = seeded_review(app, USER, f"before-rename-{export_format}")
    if returned:
        export_id, path = deleted_and_returned(app, review, export_format, target)
    else:
        export_id = publish(app, review, export_format, user=ADMIN, target=target).json()["id"]
        path = observe(app, export_id)[2]
    original = path.read_bytes()
    hard_exit(app, PUBLISH, "before_replace", review.id, export_format, target)
    assert _journal(path).exists() and path.read_bytes() == original
    assert list(path.parent.glob(f".{path.name}.*.tmp"))
    unrelated = path.parent / f".{path.name}.unrelated.tmp"
    unrelated.write_bytes(b"not part of this publication")

    sweep(app)
    assert leftovers(path) == [unrelated.name]
    assert unrelated.read_bytes() == b"not part of this publication"
    assert path.exists() is not returned
    if not returned:
        assert path.read_bytes() == original == observe(app, export_id)[1]
    republished = publish(app, review, export_format, user=ADMIN, target=target)
    assert republished.status_code == 200, republished.text
    assert path.read_bytes() == observe(app, republished.json()["id"])[1]
    removed = admin_delete(app, republished.json()["id"])
    assert removed.status_code == 204, removed.text
    assert not path.exists() and leftovers(path) == [unrelated.name]


@FORMATS
@COMMITTED
def test_regeneration_of_returned_withdrawn_bytes_recovers_after_hard_exit(
    app, export_format, committed
):
    review = seeded_review(app, USER, f"returned-crash-{export_format}")
    relocate(app, review, Path("share"))
    export_id = publish(app, review, export_format).json()["id"]
    _status, original, path = observe(app, export_id)
    bump_policy(app.accounts)
    with unmounted(app):
        assert settle_due(app, 0) == "failed"
    assert observe(app, export_id)[1] is None and path.read_bytes() == original
    sweep(app)
    hard_exit(app, REGENERATE, committed)
    assert _journal(path).exists()
    with app.accounts.factory() as db:
        assert (db.get(MediaItemExportRow, export_id).content_bytes is None) is not committed

    sweep(app)
    assert leftovers(path) == []
    for attempt in (1, 2):
        settle_due(app, attempt)
    status, retained, _path = observe(app, export_id)
    assert status["serving"] and path.read_bytes() == retained
    republished = publish(app, review, export_format, policy_owner_id=USER, policy_revision=1)
    assert republished.status_code == 200, republished.text
    deleted = deletion_request(app, export_id)()
    assert deleted.status_code == 204, deleted.text
    assert not path.exists() and leftovers(path) == []


@FORMATS
@TARGETS
@COMMITTED
def test_republish_over_a_returned_deleted_record_recovers_after_hard_exit(
    app, export_format, target, committed
):
    review = seeded_review(app, USER, f"deleted-crash-{export_format}")
    export_id, path = deleted_and_returned(app, review, export_format, target)
    hard_exit(app, PUBLISH, committed, review.id, export_format, target)
    assert _journal(path).exists()

    sweep(app)
    assert leftovers(path) == []
    assert path.exists() is committed
    republished = publish(app, review, export_format, user=ADMIN, target=target)
    assert republished.status_code == 200, republished.text
    assert republished.json()["id"] == export_id and keyed_rows(app, export_id) == [export_id]
    assert path.read_bytes() == observe(app, export_id)[1]
    deleted = admin_delete(app, export_id)
    assert deleted.status_code == 204, deleted.text
    assert not path.exists() and leftovers(path) == []


@FORMATS
@TARGETS
def test_republish_recovers_its_own_interrupted_replacement(app, export_format, target):
    review = seeded_review(app, USER, f"deleted-retry-{export_format}")
    export_id, path = deleted_and_returned(app, review, export_format, target)
    hard_exit(app, PUBLISH, False, review.id, export_format, target)

    republished = publish(app, review, export_format, user=ADMIN, target=target)
    assert republished.status_code == 200, republished.text
    assert republished.json()["id"] == export_id
    assert path.read_bytes() == observe(app, export_id)[1] and leftovers(path) == []


@FORMATS
@COMMITTED
def test_replacing_a_deleted_record_of_another_version_recovers_after_hard_exit(
    app, export_format, committed
):
    review = seeded_review(app, USER, f"other-version-crash-{export_format}")
    retired, path = deleted_and_returned(app, review, export_format, "item_default")
    with app.accounts.factory() as db:
        db.get(MediaItemExportRow, retired).media_source_id = "retired-version"
        db.commit()
    hard_exit(app, PUBLISH, committed, review.id, export_format, "item_default")
    assert _journal(path).exists()

    sweep(app)
    assert leftovers(path) == []
    republished = publish(app, review, export_format, user=ADMIN, target="item_default")
    assert republished.status_code == 200, republished.text
    with app.accounts.factory() as db:
        rows = db.query(MediaItemExportRow).filter_by(destination_path=str(path)).all()
        assert [row.id for row in rows] == [republished.json()["id"]]
        assert rows[0].media_source_id != "retired-version"
        assert path.read_bytes() == rows[0].content_bytes


OLD, NEW = b"owned bytes", b"replacement bytes"


def interrupted(tmp_path, actual, *, old=OLD, new=NEW):
    """Leave a journal as a hard exit would after replacing `old` with `new`."""
    destination = tmp_path / "Movie.edl"
    if actual is not None:
        destination.write_bytes(actual)
    backup = tmp_path / ".Movie.edl.interrupted.bak"
    backup.write_bytes(old)
    state = {"version": 1, "old": digest(old), "new": digest(new), "backup": backup.name}
    _journal(destination).write_text(json.dumps(state))
    return destination


@pytest.mark.parametrize("actual", [OLD, NEW, None])
def test_null_commit_reconciles_to_absence_when_the_old_digest_is_owned(tmp_path, actual):
    destination = interrupted(tmp_path, actual)
    recover_sidecar(destination, None, owned={digest(OLD)})
    assert not destination.exists() and list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("actual", "committed", "owned"),
    [
        (b"foreign bytes", None, {digest(OLD)}),
        (NEW, None, set()),
        (NEW, None, {digest(NEW)}),
        (NEW, None, {digest(b"other owned bytes")}),
        (NEW, b"other committed bytes", {digest(OLD)}),
    ],
    ids=["foreign", "no-evidence", "new-only", "other-evidence", "other-committed"],
)
def test_null_commit_admission_keeps_refusals(tmp_path, actual, committed, owned):
    destination = interrupted(tmp_path, actual)
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    with pytest.raises(ExportRecoveryRequired):
        recover_sidecar(destination, committed, owned=owned)
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == before


def test_null_commit_admission_refuses_a_symbolic_destination(tmp_path):
    destination = interrupted(tmp_path, None)
    elsewhere = tmp_path.parent / f"{tmp_path.name}-elsewhere"
    elsewhere.write_bytes(NEW)
    os.symlink(elsewhere, destination)
    with pytest.raises(ExportRecoveryRequired):
        recover_sidecar(destination, None, owned={digest(OLD)})
    assert destination.is_symlink() and elsewhere.read_bytes() == NEW
    assert _journal(destination).exists()


@pytest.mark.parametrize(
    "temporary_name",
    ["../outside.tmp", "/outside.tmp", ".Other.edl.pending.tmp", ".Movie.edl.pending.bak", "", 42],
)
def test_recovery_refuses_invalid_temporary_names_without_changes(tmp_path, temporary_name):
    destination = interrupted(tmp_path, OLD)
    journal = _journal(destination)
    state = json.loads(journal.read_bytes())
    state["temporary"] = temporary_name
    journal.write_text(json.dumps(state))
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    with pytest.raises(ExportRecoveryRequired):
        recover_sidecar(destination, OLD)
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == before


def test_recovery_refuses_a_symbolic_temporary_without_changes(tmp_path):
    destination = interrupted(tmp_path, NEW)
    journal = _journal(destination)
    pending = tmp_path / ".Movie.edl.pending.tmp"
    target = tmp_path / "untouched-target"
    target.write_bytes(b"not part of this publication")
    pending.symlink_to(target)
    state = json.loads(journal.read_bytes())
    state["temporary"] = pending.name
    journal.write_text(json.dumps(state))
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    with pytest.raises(ExportRecoveryRequired):
        recover_sidecar(destination, OLD)
    assert pending.is_symlink() and pending.readlink() == target
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == before
