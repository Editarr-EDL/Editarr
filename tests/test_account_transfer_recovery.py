"""A transfer survives write failures and hard exits on either side of its switch.

The child process runs the real `account_deletion_flow._transfer` with a real
password proof and exits with `os._exit` at the injected point. Recovery is the
next request's own `recover` or the MU-34 sweep; ownership evidence decides
which files it removes.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from scanner.persistence.repository import export_identity_moves
from scanner.persistence.rows.db_account_dispositions import (
    AccountDispositionRow,
    AccountTransferMoveRow,
)
from tests.multi_user_access_support import ADMIN, USER
from tests.test_account_deletion_http import account, code, delete, deletion_audits, preview
from tests.test_account_transfer_http import app as app
from tests.test_account_transfer_http import export_id, exports_app, review_app, rows
from tests.test_export_regeneration_ownership import sweep
from tests.test_personal_exports_http import publish, seeded_review

__all__ = ["exports_app", "review_app"]

TRANSFER_AND_EXIT = """
import os, sys
from datetime import UTC, datetime
from types import SimpleNamespace
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from scanner.db import AuthSessionRow
from scanner.http import account_deletion_flow as flow
from scanner.http.users_access import Caller
from scanner.persistence.repository import account_transfer_files
from scanner.persistence.repository.account_disposition import DispositionRequest
from scanner.persistence.repository.account_impact import Disposition
from scanner.security.access.api_shapes import Reauthentication
from scanner.security.access.fresh import PasswordCheck, verify_reauthentication
from scanner.security.access.identity import CredentialKind, UserId
from scanner.security.access.load import load_access
from scanner.security.access.targets import AccountRef
from scanner.security.access.vocabulary import Role
from tests.multi_user_access_support import ADMIN, MEMBER_PASSWORD
engine = create_engine(sys.argv[1])
user, destination = int(sys.argv[2]), int(sys.argv[3])
stage, confirmation = sys.argv[4], sys.argv[5]
def crash(*_args, **_kwargs):
    os._exit(75)
if stage == "written":
    flow._apply = crash
else:
    account_transfer_files.finish = crash
with Session(engine) as db:
    session = db.scalar(select(AuthSessionRow.id).where(
        AuthSessionRow.user_id == ADMIN, AuthSessionRow.revoked_at.is_(None)))
    access = load_access(db, UserId(ADMIN), CredentialKind.SESSION, session)
    def jellyfin(_db, _user_id, password):
        return PasswordCheck.ACCEPTED if password == MEMBER_PASSWORD else PasswordCheck.REFUSED
    proof = Reauthentication(password=MEMBER_PASSWORD)
    fresh = verify_reauthentication(db, access, proof, jellyfin)
    caller = Caller(db, access, "crash-test", datetime.now(UTC))
    ask = DispositionRequest(Disposition.TRANSFER, destination, fresh)
    body = SimpleNamespace(
        confirmation=confirmation, destination_id=destination, jellyfin_user="allow"
    )
    target = AccountRef(UserId(user), Role.USER)
    flow._transfer(caller, flow._review_access(caller), target, ask, body, {})
os._exit(1)
"""


def _published(app):
    review = seeded_review(app, USER, "moving")
    ids = [publish(app, review, fmt).json()["id"] for fmt in ("edl", "mcf")]
    before = rows(app)
    return ids, before, {key: before[key]["path"].read_bytes() for key in ids}


def _new_paths(app, before, ids):
    destination = export_id(app, ADMIN)
    old = before[ids[0]]["export_id"]
    return {key: Path(str(before[key]["path"]).replace(old, destination)) for key in ids}


def _crash(app, stage, confirmation):
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            TRANSFER_AND_EXIT,
            str(app.accounts.engine.url),
            str(USER),
            str(ADMIN),
            stage,
            confirmation,
        ],
        capture_output=True,
        check=False,
        timeout=60,
    )
    assert result.returncode == 75, result.stderr.decode()


def _moves(app):
    with app.accounts.factory() as db:
        return [
            (row.new_file_id is not None, row.outcome) for row in db.query(AccountTransferMoveRow)
        ]


def test_a_hard_exit_before_the_switch_discards_new_files_and_the_retry_applies_once(app):
    ids, before, content = _published(app)
    new = _new_paths(app, before, ids)
    body = preview(app, ADMIN, USER, "transfer", ADMIN).json()
    _crash(app, "written", body["confirmation"])
    assert all(new[key].read_bytes() == content[key] for key in ids)
    assert all(before[key]["path"].read_bytes() == content[key] for key in ids)
    assert _moves(app) == [(True, None), (True, None)]
    assert account(app, USER)[0] == "active" and rows(app) == before
    response = delete(app, ADMIN, USER, "transfer", body["confirmation"], destination=ADMIN)
    assert response.status_code == 200, response.text
    after = rows(app)
    for key in ids:
        assert after[key]["path"] == new[key] and new[key].read_bytes() == content[key]
        assert not before[key]["path"].exists()
    assert _moves(app) == [(True, "removed"), (True, "removed")]
    assert [row[0] for row in deletion_audits(app)] == ["succeeded"]


def test_a_hard_exit_after_the_switch_is_finished_by_the_sweep_and_replays(app):
    ids, before, content = _published(app)
    new = _new_paths(app, before, ids)
    body = preview(app, ADMIN, USER, "transfer", ADMIN).json()
    _crash(app, "switched", body["confirmation"])
    with app.accounts.factory() as db:
        assert db.get(AccountDispositionRow, USER).mode == "transfer"
    assert all(before[key]["path"].exists() and new[key].exists() for key in ids)
    assert _moves(app) == [(True, None), (True, None)]
    sweep(app)
    assert not any(before[key]["path"].exists() for key in ids)
    assert all(new[key].read_bytes() == content[key] for key in ids)
    assert _moves(app) == [(True, "removed"), (True, "removed")]
    replay = delete(app, ADMIN, USER, "transfer", body["confirmation"], destination=ADMIN)
    assert replay.status_code == 200 and replay.json()["replayed"] is True
    assert [row[0] for row in deletion_audits(app)] == ["succeeded"]


def test_recovery_never_removes_a_file_it_did_not_place(app):
    ids, before, content = _published(app)
    new = _new_paths(app, before, ids)
    body = preview(app, ADMIN, USER, "transfer", ADMIN).json()
    _crash(app, "written", body["confirmation"])
    foreign = new[ids[0]]
    foreign.unlink()
    foreign.write_bytes(b"someone else's file")
    sweep(app)
    assert foreign.read_bytes() == b"someone else's file" and not new[ids[1]].exists()
    assert _moves(app) == [] and rows(app) == before
    current = preview(app, ADMIN, USER, "transfer", ADMIN).json()
    assert current["blockers"] == [{"code": "export_file_collision", "count": 1}]


@pytest.mark.parametrize("failure", [PermissionError(13, "denied"), OSError(28, "full")])
def test_a_write_failure_discards_placed_files_and_changes_nothing(app, monkeypatch, failure):
    ids, before, content = _published(app)
    new = _new_paths(app, before, ids)
    body = preview(app, ADMIN, USER, "transfer", ADMIN).json()
    original, calls = export_identity_moves.place_new, []

    def second_fails(path):
        calls.append(path)
        if len(calls) == 2:
            raise failure
        original(path)

    with monkeypatch.context() as patch:
        patch.setattr(export_identity_moves, "place_new", second_fails)
        refused = delete(app, ADMIN, USER, "transfer", body["confirmation"], destination=ADMIN)
    assert code(refused) == (409, "export_write_failed")
    assert not any(os.path.lexists(path) for path in new.values())
    assert rows(app) == before and _moves(app) == [] and account(app, USER)[0] == "active"
    assert deletion_audits(app)[-1][3] == {"reason": "export_write_failed"}
    applied = delete(app, ADMIN, USER, "transfer", body["confirmation"], destination=ADMIN)
    assert applied.status_code == 200, applied.text
    assert all(new[key].read_bytes() == content[key] for key in ids)
