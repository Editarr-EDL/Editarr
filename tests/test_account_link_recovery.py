# ruff: noqa: F811
"""MU-35 failures and interruptions: old exports survive, and recovery is deterministic.

Hard exits run `tests.account_link_crash` in a subprocess against the test database;
recovery then runs through the startup sweep (`sweep_regenerations`), twice.
"""

from __future__ import annotations

import errno
import json
import os
import subprocess
import sys

import pytest
from sqlalchemy import select

from scanner.db import AccountLinkExportMoveRow, MediaItemExportRow
from scanner.persistence.repository import export_identity_moves as files
from scanner.persistence.repository.export_recovery import digest
from scanner.persistence.repository.export_regeneration import sweep_regenerations
from scanner.persistence.rows.db_export_regenerations import (
    ExportRegenerationRow,
    RegenerationFailure,
    RegenerationState,
)
from tests.account_link_support import (
    boundary,  # noqa: F401
    clock,  # noqa: F401
    confirm,
    identity,
    link_app,  # noqa: F401
    link_events,
    prove_and_link,
    publications,
    publish_both,
    request_proof,
    transitions,
)
from tests.jellyfin_user_boundary import IDS, PRIMARY
from tests.multi_user_access_support import ADMIN, USER
from tests.test_personal_exports_http import app, review_app  # noqa: F401
from tests.users_api_http_support import code

ROLLED_BACK = {
    "after_begin",
    "after_staging",
    "after_record",
    "after_place",
    "mid_write",
    "after_write",
}
STAGED = {"after_staging", "after_record", "after_place"}


def _new_paths(before):
    return [
        path.parent.parent / IDS["Drew"] / path.name.replace(export_id, IDS["Drew"])
        for _id, _format, export_id, path, _content, _revision in before
    ]


def _recover(app):
    for _ in range(2):
        with app.accounts.factory() as db:
            sweep_regenerations(db)


def _assert_unlinked(app, before):
    assert identity(app, USER) is None
    assert publications(app, USER) == before
    for *_, path, content, _revision in before:
        assert path.read_bytes() == content
    for path in _new_paths(before):
        assert not path.exists() and not path.parent.exists()


def _assert_linked(app, before):
    assert identity(app, USER) == (PRIMARY, IDS["Drew"], "verified")
    after = publications(app, USER)
    for old, new, new_path in zip(before, after, _new_paths(before), strict=True):
        assert (new[2], new[3]) == (IDS["Drew"], new_path)
        assert new_path.read_bytes() == old[4]
        assert not old[3].exists() and not old[3].parent.exists()


@pytest.mark.parametrize(
    "point",
    [
        "after_begin",
        "after_staging",
        "after_record",
        "after_place",
        "mid_write",
        "after_write",
        "after_switch",
        "mid_remove",
    ],
)
def test_a_hard_exit_converges_by_the_stored_state_alone(link_app, point):
    publish_both(link_app, USER, "crash")
    before = publications(link_app, USER)
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "tests.account_link_crash",
            str(link_app.accounts.path),
            str(USER),
            point,
        ],
        capture_output=True,
        check=False,
        env={**os.environ, "PYTHONPATH": os.getcwd()},
    )
    assert result.returncode == 97, result.stderr.decode()
    open_state = "writing" if point in ROLLED_BACK else "removing"
    assert [state for state, *_ in transitions(link_app, USER)] == [open_state]
    if point in ROLLED_BACK:
        for *_, path, content, _revision in before:
            assert path.read_bytes() == content
    staged = list(link_app.media.rglob("*.editarr-link.tmp"))
    assert len(staged) == (point in STAGED)
    _recover(link_app)
    assert list(link_app.media.rglob("*.tmp")) == []
    if point in ROLLED_BACK:
        _assert_unlinked(link_app, before)
        assert transitions(link_app, USER) == [("rolled_back", "interrupted", [None, None])]
    else:
        _assert_linked(link_app, before)
        assert transitions(link_app, USER)[0][0] == "completed"
    recovery = [details for _o, actor, _t, details in link_events(link_app) if actor is None]
    assert [details["step"] for details in recovery] == ["recovery"]
    linked = prove_and_link(link_app, ADMIN, USER, "Drew")
    assert linked.status_code == (409 if point not in ROLLED_BACK else 200), linked.text
    _assert_linked(link_app, before)


def test_a_failed_write_keeps_the_old_identity_and_exports(link_app, monkeypatch):
    publish_both(link_app, USER, "failing")
    before = publications(link_app, USER)
    real, calls = files.write_new, []

    def fail_second(move, new_id):
        calls.append(move.export_id)
        if len(calls) == 2:
            raise OSError(errno.ENOSPC, "No space left on device")
        return real(move, new_id)

    monkeypatch.setattr(files, "write_new", fail_second)
    refused = prove_and_link(link_app, ADMIN, USER, "Drew")
    assert code(refused) == (409, "write_failed")
    _assert_unlinked(link_app, before)
    assert transitions(link_app, USER) == [("rolled_back", "write_failed", [None, None])]
    assert link_events(link_app)[-1][0] == "denied"
    assert link_events(link_app)[-1][3]["reason"] == "write_failed"
    monkeypatch.setattr(files, "write_new", real)
    assert prove_and_link(link_app, ADMIN, USER, "Drew").status_code == 200
    _assert_linked(link_app, before)


def test_an_old_file_that_cannot_be_removed_yet_is_retried_by_recovery(link_app, monkeypatch):
    publish_both(link_app, USER, "stuck")
    before = publications(link_app, USER)
    real = files.remove_old

    def unavailable(path, expected):
        raise OSError(errno.EIO, "Input/output error")

    monkeypatch.setattr(files, "remove_old", unavailable)
    linked = prove_and_link(link_app, ADMIN, USER, "Drew")
    assert linked.status_code == 200, linked.text
    assert linked.json()["state"] == "removing"
    assert transitions(link_app, USER) == [("removing", None, [None, None])]
    assert identity(link_app, USER)[1] == IDS["Drew"]
    for *_, path, content, _revision in before:
        assert path.read_bytes() == content
    _recover(link_app)
    assert transitions(link_app, USER) == [("removing", None, [None, None])]
    monkeypatch.setattr(files, "remove_old", real)
    _recover(link_app)
    assert transitions(link_app, USER) == [("completed", None, ["removed", "removed"])]
    _assert_linked(link_app, before)


def test_unfinished_regeneration_blocks_linking(link_app):
    publish_both(link_app, USER, "regenerating")
    export_id = publications(link_app, USER)[0][0]
    with link_app.accounts.factory() as db:
        db.add(
            ExportRegenerationRow(
                export_id=export_id,
                state=RegenerationState.QUEUED,
                due_at=0.0,
                attempts=0,
                policy_revision=0,
            )
        )
        db.commit()
    proven = request_proof(link_app, ADMIN, USER, "Drew")
    assert proven.json()["preview"]["blockers"] == ["export_regeneration_pending"]
    refused = confirm(link_app, ADMIN, USER, proven.json()["proof"], proven.json()["confirmation"])
    assert code(refused) == (409, "link_blocked")
    assert identity(link_app, USER) is None


def test_a_withdrawn_publication_moves_its_record_and_its_evidence_stays_at_the_old_path(
    link_app,
):
    publish_both(link_app, USER, "withdrawn")
    before = publications(link_app, USER)
    withdrawn = before[0]
    with link_app.accounts.factory() as db:
        row = db.get(MediaItemExportRow, withdrawn[0])
        snapshot = json.loads(row.snapshot_json) | {"owned_sha256": digest(row.content_bytes)}
        row.snapshot_json = json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
        row.content_bytes = None
        row.regeneration = ExportRegenerationRow(
            state=RegenerationState.WITHDRAWN,
            attempts=1,
            policy_revision=0,
            failure_reason=RegenerationFailure.REGENERATION_FAILED,
        )
        db.commit()
    withdrawn[3].unlink()
    proven = request_proof(link_app, ADMIN, USER, "Drew")
    assert [move["action"] for move in proven.json()["preview"]["exports"]] == ["record", "move"]
    linked = confirm(link_app, ADMIN, USER, proven.json()["proof"], proven.json()["confirmation"])
    assert linked.status_code == 200, linked.text
    after = publications(link_app, USER)
    assert after[0][2] == IDS["Drew"] and after[0][4] is None
    assert not after[0][3].exists()
    with link_app.accounts.factory() as db:
        row = db.get(MediaItemExportRow, withdrawn[0])
        assert "owned_sha256" not in json.loads(row.snapshot_json)
        assert json.loads(row.snapshot_json)["target_export_id"] == IDS["Drew"]
        move = db.scalars(select(AccountLinkExportMoveRow).filter_by(export_id=withdrawn[0])).one()
        assert (move.old_path, move.old_sha256) == (str(withdrawn[3]), digest(withdrawn[4]))
    assert transitions(link_app, USER) == [("completed", None, ["absent", "removed"])]


def test_bytes_editarr_did_not_write_stay_at_the_old_path(link_app):
    publish_both(link_app, USER, "foreign")
    before = publications(link_app, USER)
    before[0][3].write_bytes(b"edited by hand\n")
    linked = prove_and_link(link_app, ADMIN, USER, "Drew")
    assert linked.status_code == 200, linked.text
    assert transitions(link_app, USER) == [("completed", None, ["kept", "removed"])]
    assert before[0][3].read_bytes() == b"edited by hand\n"
    assert not before[1][3].exists()
    assert [row[3].read_bytes() for row in publications(link_app, USER)] == [
        before[0][4],
        before[1][4],
    ]


@pytest.mark.parametrize("committed", [False, True])
def test_an_uncertain_switch_commit_resolves_from_the_stored_state(
    link_app, monkeypatch, committed
):
    import sqlite3

    from sqlalchemy.exc import OperationalError
    from sqlalchemy.orm import Session

    from scanner.db import AccountLinkTransitionRow

    publish_both(link_app, USER, "uncertain-commit")
    before = publications(link_app, USER)
    real = Session.commit
    injected = []

    def uncertain(db):
        removing = db.query(AccountLinkTransitionRow.id).filter_by(state="removing").first()
        if not injected and removing is not None:
            injected.append(True)
            if committed:
                real(db)
            raise OperationalError("COMMIT", {}, sqlite3.OperationalError("injected I/O failure"))
        return real(db)

    monkeypatch.setattr(Session, "commit", uncertain)
    if committed:
        assert prove_and_link(link_app, ADMIN, USER, "Drew").status_code == 200
    else:
        with pytest.raises(OperationalError):
            prove_and_link(link_app, ADMIN, USER, "Drew")
    assert injected
    monkeypatch.setattr(Session, "commit", real)
    _recover(link_app)
    if committed:
        _assert_linked(link_app, before)
    else:
        _assert_unlinked(link_app, before)
