"""An unfinished transfer never costs a later retain or delete its retained bytes.

Move rows are bound to the confirmed transfer, and every removal follows the shared
removal rule. Every mode first converges an account's unfinished transfer under the
publication lock; moves it cannot settle block with a count. Review round 2 P1-1 and
its probes, through the real routes and real child-process exits.
"""

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime

import pytest
from sqlalchemy.exc import OperationalError

from scanner.db import MediaItemExportRow
from scanner.persistence.repository import account_transfer_files, export_identity_moves
from scanner.persistence.repository.export_recovery import publication_lock
from scanner.persistence.rows.db_account_dispositions import (
    AccountDispositionRow,
    AccountTransferMoveRow,
)
from scanner.persistence.write_lock import write_lock
from tests.multi_user_access_support import ADMIN, USER
from tests.test_account_deletion_http import (
    account,
    code,
    confirmed,
    delete,
    deletion_audits,
    preview,
)
from tests.test_account_transfer_http import app as app
from tests.test_account_transfer_http import exports_app, review_app, rows
from tests.test_account_transfer_recovery import _crash, _new_paths, _published
from tests.test_export_regeneration_boundaries import observe
from tests.test_export_regeneration_convergence import settle_due
from tests.test_export_regeneration_ownership import sweep
from tests.test_personal_exports_http import publish, seeded_review

__all__ = ["exports_app", "review_app"]


def _moves(app):
    with app.accounts.factory() as db:
        return [(row.user_id, row.outcome) for row in db.query(AccountTransferMoveRow)]


def _kept(app, ids, before, content):
    after = rows(app)
    for key in ids:
        assert after[key]["path"] == before[key]["path"]
        assert before[key]["path"].read_bytes() == content[key]


def _crashed_transfer(app):
    ids, before, content = _published(app)
    new = _new_paths(app, before, ids)
    body = preview(app, ADMIN, USER, "transfer", ADMIN).json()
    _crash(app, "written", body["confirmation"])
    assert all(new[key].exists() for key in ids) and len(_moves(app)) == 2
    return ids, before, content, new


def test_retain_after_a_hard_exit_keeps_the_bytes_and_discards_the_new_files(app):
    ids, before, content, new = _crashed_transfer(app)
    confirmed(app, ADMIN, USER, "retain")
    assert _moves(app) == [] and not any(os.path.lexists(path) for path in new.values())
    sweep(app)
    _kept(app, ids, before, content)
    assert account(app, USER)[0] == "disabled"


def test_delete_after_a_hard_exit_keeps_every_file_chosen_to_retain(app):
    ids, before, content, _new = _crashed_transfer(app)
    current = preview(app, ADMIN, USER, "delete").json()
    listed = [item["publication"]["id"] for item in current["files"]]
    response = delete(
        app, ADMIN, USER, "delete", current["confirmation"], [(key, "retain") for key in listed]
    )
    assert response.status_code == 200, response.text
    sweep(app)
    assert _moves(app) == []
    assert all(before[key]["path"].read_bytes() == content[key] for key in ids)


def test_regeneration_after_retain_keeps_serving_the_retained_bytes(app):
    ids, before, content, _new = _crashed_transfer(app)
    confirmed(app, ADMIN, USER, "retain")
    sweep(app)
    for hours in range(1, 7):
        settle_due(app, hours)
    for key in ids:
        assert observe(app, key)[0]["serving"] is True
        assert before[key]["path"].read_bytes() == content[key]


def test_a_rollback_that_keeps_failing_blocks_every_mode_until_it_settles(app, monkeypatch):
    ids, before, content = _published(app)
    body = preview(app, ADMIN, USER, "transfer", ADMIN).json()
    original, calls = export_identity_moves.place_new, []

    def second_fails(path):
        calls.append(path)
        if len(calls) == 2:
            raise OSError(28, "full")
        original(path)

    def denied(*_args, **_kwargs):
        raise PermissionError(13, "denied")

    with monkeypatch.context() as patch:
        patch.setattr(export_identity_moves, "place_new", second_fails)
        patch.setattr(export_identity_moves, "discard_new", denied)
        refused = delete(app, ADMIN, USER, "transfer", body["confirmation"], destination=ADMIN)
        assert code(refused) == (409, "export_write_failed")
        assert _moves(app) == [(USER, None), (USER, None)]
        blocked = {}
        for mode in ("retain", "delete", "transfer"):
            current = preview(app, ADMIN, USER, mode, ADMIN if mode == "transfer" else None)
            blocked[mode] = current.json()
            assert {"code": "account_transfer_unsettled", "count": 2} in blocked[mode]["blockers"]
        retain = delete(app, ADMIN, USER, "retain", blocked["retain"]["confirmation"])
        assert code(retain) == (409, "account_deletion_blocked")
        sweep(app)
        assert account(app, USER)[0] == "active" and len(_moves(app)) == 2
    _kept(app, ids, before, content)
    assert deletion_audits(app)[-1][3] == {"reason": "account_deletion_blocked"}
    current = preview(app, ADMIN, USER, "retain").json()
    assert current["blockers"] == [] and _moves(app) == []
    assert delete(app, ADMIN, USER, "retain", current["confirmation"]).status_code == 200
    sweep(app)
    _kept(app, ids, before, content)


def test_moves_meeting_a_disposition_that_is_not_their_transfer_are_rolled_back(app):
    """A settings restore can bring back a retain record while move rows are open."""
    ids, before, content, new = _crashed_transfer(app)
    with app.accounts.factory() as db:
        db.add(
            AccountDispositionRow(
                user_id=USER,
                mode="retain",
                confirmation="e" * 64,
                impact_json="{}",
                result_json="{}",
                created_at=datetime(2026, 10, 28),
            )
        )
        db.commit()
    sweep(app)
    assert _moves(app) == [] and not any(os.path.lexists(path) for path in new.values())
    _kept(app, ids, before, content)


def test_finish_keeps_an_old_path_that_a_publication_still_records(app):
    ids, before, content = _published(app)
    body = preview(app, ADMIN, USER, "transfer", ADMIN).json()
    _crash(app, "switched", body["confirmation"])
    with app.accounts.factory() as db:
        db.get(MediaItemExportRow, ids[0]).destination_path = str(before[ids[0]]["path"])
        db.commit()
    sweep(app)
    assert before[ids[0]]["path"].read_bytes() == content[ids[0]]
    assert not before[ids[1]]["path"].exists()
    assert sorted(outcome for _user, outcome in _moves(app)) == ["kept", "removed"]


def test_a_busy_database_while_writing_rolls_back_and_is_audited(app, monkeypatch):
    ids, before, content = _published(app)
    new = _new_paths(app, before, ids)
    body = preview(app, ADMIN, USER, "transfer", ADMIN).json()
    locked = sqlite3.OperationalError("database is locked")
    locked.sqlite_errorcode = 5
    calls = []

    @contextmanager
    def busy_on_second(db):
        calls.append(db)
        if len(calls) == 2:
            raise OperationalError("UPDATE", {}, locked)
        with write_lock(db):
            yield

    with monkeypatch.context() as patch:
        patch.setattr(account_transfer_files, "write_lock", busy_on_second)
        refused = delete(app, ADMIN, USER, "transfer", body["confirmation"], destination=ADMIN)
    assert code(refused) == (503, "database_busy")
    assert not any(os.path.lexists(path) for path in new.values())
    assert _moves(app) == [] and account(app, USER)[0] == "active"
    _kept(app, ids, before, content)
    assert deletion_audits(app)[-1][3] == {"reason": "database_busy"}
    applied = delete(app, ADMIN, USER, "transfer", body["confirmation"], destination=ADMIN)
    assert applied.status_code == 200, applied.text


def _recover(app):
    with app.accounts.factory() as db, publication_lock(db):
        account_transfer_files.recover(db)


@pytest.mark.parametrize("stage", ["written", "switched"])
def test_recovery_keeps_a_path_another_publication_records(app, stage):
    ids, before, content = _published(app)
    new = _new_paths(app, before, ids)
    body = preview(app, ADMIN, USER, "transfer", ADMIN).json()
    _crash(app, stage, body["confirmation"])
    other = seeded_review(app, ADMIN, "unrelated")
    other_id = publish(app, other, "edl", user=ADMIN).json()["id"]
    claimed = new[ids[0]] if stage == "written" else before[ids[0]]["path"]
    with app.accounts.factory() as db:
        db.get(MediaItemExportRow, other_id).destination_path = str(claimed)
        db.commit()
    _recover(app)
    assert claimed.read_bytes() == content[ids[0]]
    expected = [] if stage == "written" else [(USER, "kept"), (USER, "removed")]
    assert _moves(app) == expected


def test_a_switched_move_keeps_its_old_file_until_the_new_path_serves(app):
    ids, before, content = _published(app)
    new = _new_paths(app, before, ids)
    body = preview(app, ADMIN, USER, "transfer", ADMIN).json()
    _crash(app, "switched", body["confirmation"])
    new[ids[0]].write_bytes(b"not the published bytes")
    _recover(app)
    assert before[ids[0]]["path"].read_bytes() == content[ids[0]]
    assert not before[ids[1]]["path"].exists()
    assert _moves(app) == [(USER, "kept"), (USER, "removed")]
    new[ids[0]].write_bytes(content[ids[0]])
    _recover(app)
    assert not before[ids[0]]["path"].exists()
    assert _moves(app) == [(USER, "removed"), (USER, "removed")]


def test_a_move_whose_publication_records_another_path_waits_for_its_bytes_there(app):
    ids, before, content = _published(app)
    new = _new_paths(app, before, ids)
    body = preview(app, ADMIN, USER, "transfer", ADMIN).json()
    _crash(app, "switched", body["confirmation"])
    elsewhere = new[ids[0]].with_name("elsewhere" + new[ids[0]].suffix)
    with app.accounts.factory() as db:
        db.get(MediaItemExportRow, ids[0]).destination_path = str(elsewhere)
        db.commit()
    _recover(app)
    assert before[ids[0]]["path"].read_bytes() == content[ids[0]]
    assert new[ids[0]].read_bytes() == content[ids[0]]
    assert _moves(app) == [(USER, "kept"), (USER, "removed")]
    new[ids[0]].replace(elsewhere)
    _recover(app)
    assert not before[ids[0]]["path"].exists() and not new[ids[0]].exists()
    assert elsewhere.read_bytes() == content[ids[0]]
    assert _moves(app) == [(USER, "removed"), (USER, "removed")]


def test_a_refused_transfer_with_a_hand_edited_sidecar_leaves_no_block(app, monkeypatch):
    """R6-A: a transfer whose new files could not be written changes nothing, even when the
    user had edited a sidecar, so nothing is left to block the account."""
    ids, before, content = _published(app)
    edited = before[ids[0]]["path"]
    edited.write_bytes(content[ids[0]] + b"\n* user note\n")
    body = preview(app, ADMIN, USER, "transfer", ADMIN).json()

    def no_space(*_args, **_kwargs):
        raise OSError(28, "No space left on device")

    with monkeypatch.context() as patch:
        patch.setattr(account_transfer_files, "write", no_space)
        refused = delete(app, ADMIN, USER, "transfer", body["confirmation"], destination=ADMIN)
    assert code(refused) == (409, "export_write_failed")
    sweep(app)
    assert _moves(app) == []
    assert preview(app, ADMIN, USER, "retain").json()["blockers"] == []
    assert edited.read_bytes() == content[ids[0]] + b"\n* user note\n"
