"""Account deletion survives failed commits and hard exits without duplicate effects.

Each case confirms one preview, interrupts the single disposition transaction,
and then retries the same confirmation through HTTP. The impact record exists
exactly when the disposition committed.
"""

import subprocess
import sys
from pathlib import Path

import pytest

from scanner.db import AuditEventRow, MediaItemExportRow, SavedReviewRow, UserRow
from scanner.persistence.repository import account_disposition
from scanner.persistence.rows.db_account_dispositions import AccountDispositionRow
from tests.multi_user_access_support import ADMIN, USER
from tests.test_account_deletion_http import app as app
from tests.test_account_deletion_http import delete, exports_app, files_on_disk, preview
from tests.test_personal_exports_http import publish, review_app, seeded_review

__all__ = ["exports_app", "review_app"]

APPLY_AND_EXIT = """
import os, sys
from datetime import UTC, datetime
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from scanner.persistence.repository.account_consent import preview
from scanner.persistence.repository.account_disposition import (
    DispositionRequest, apply_disposition, disposition_gate,
)
from scanner.persistence.repository.account_impact import Disposition
from scanner.persistence.repository.export_recovery import publication_lock
from scanner.persistence.repository.review_access import ReviewAccess
from scanner.persistence.repository.review_disposition import FileChoice
from scanner.security.access.identity import CredentialKind, UserId
from scanner.security.access.load import load_access
from scanner.security.access.records import AuditEvent, AuditOutcome
from scanner.security.access.targets import AccountRef
from scanner.security.access.vocabulary import Role
from scanner.security.audit.actions import AuditAction
from scanner.security.audit.store import stage_event
from tests.multi_user_access_support import ADMIN as actor
engine = create_engine(sys.argv[1])
user, committed, removed = int(sys.argv[2]), sys.argv[3] == "True", int(sys.argv[4])
now = datetime.now(UTC)
# The authorization gate already ran in the parent; preview=True only skips the proof.
ask = DispositionRequest(Disposition.DELETE, preview=True)
with Session(engine) as db, publication_lock(db):
    access = load_access(db, UserId(actor), CredentialKind.SESSION)
    caller = ReviewAccess(db, access)
    target = AccountRef(UserId(user), Role.USER)
    with disposition_gate(caller, access, target, ask) as impact:
        consent = preview(caller, impact)
        assert consent["confirmation"] == sys.argv[5], "the subprocess applies the confirmed impact"
        commit = db.commit
        def crash():
            if committed:
                commit()
            os._exit(73)
        db.commit = crash
        choices = {
            file.row_id: FileChoice.REMOVE if file.row_id == removed else FileChoice.RETAIN
            for plan in impact.plans for file in plan.files
        }
        def audit(details):
            event = AuditEvent(
                now, access.principal.user_id, access.principal.credential,
                AuditAction.ACCOUNT_DELETE, AuditOutcome.SUCCEEDED,
                target_user=UserId(user), details=details,
            )
            stage_event(db, event)
        apply_disposition(db, access, impact, consent, choices, now=now, stage_audit=audit)
"""


def _published(app):
    review = seeded_review(app, USER, "interrupted")
    edl, mcf = (publish(app, review, format).json()["id"] for format in ("edl", "mcf"))
    with app.accounts.factory() as db:
        path = Path(db.get(MediaItemExportRow, mcf).destination_path)
    return review, edl, mcf, path


def _state(app, review):
    with app.accounts.factory() as db:
        user = db.get(UserRow, USER)
        return (
            user.status,
            user.username is not None,
            db.get(SavedReviewRow, review.id) is not None,
            db.get(AccountDispositionRow, USER) is not None,
            db.query(AuditEventRow)
            .filter(AuditEventRow.action == "account.delete", AuditEventRow.outcome == "succeeded")
            .count(),
        )


def test_a_failed_commit_restores_every_file_and_row_and_the_retry_applies_once(app, monkeypatch):
    review, edl, mcf, path = _published(app)
    before, state = files_on_disk(app), _state(app, review)
    body = preview(app, ADMIN, USER, "delete").json()
    choices = [(edl, "retain"), (mcf, "remove")]

    def fail(_value):
        assert not path.exists(), "the removal is staged before the commit fails"
        raise RuntimeError("injected failure before commit")

    with monkeypatch.context() as patch:
        patch.setattr(account_disposition, "canonical", fail)
        with pytest.raises(RuntimeError, match="injected failure"):
            delete(app, ADMIN, USER, "delete", body["confirmation"], choices)
    assert files_on_disk(app) == before and _state(app, review) == state
    assert preview(app, ADMIN, USER, "delete").json()["confirmation"] == body["confirmation"]
    applied = delete(app, ADMIN, USER, "delete", body["confirmation"], choices)
    assert applied.status_code == 200, applied.text
    replay = delete(app, ADMIN, USER, "delete", body["confirmation"], choices)
    assert replay.json() == applied.json() | {"replayed": True}
    assert _state(app, review) == ("disabled", False, False, True, 1)
    assert not path.exists() and files_on_disk(app)[edl][3] == before[edl][3]


@pytest.mark.parametrize("committed", [False, True])
def test_a_hard_exit_keeps_the_impact_record_exactly_when_the_disposition_committed(app, committed):
    review, edl, mcf, path = _published(app)
    before = files_on_disk(app)
    body = preview(app, ADMIN, USER, "delete").json()
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            APPLY_AND_EXIT,
            str(app.accounts.engine.url),
            str(USER),
            str(committed),
            str(mcf),
            body["confirmation"],
        ],
        capture_output=True,
        check=False,
        timeout=60,
    )
    assert result.returncode == 73, result.stderr.decode()
    assert not path.exists()
    if committed:
        assert _state(app, review) == ("disabled", False, False, True, 1)
    else:
        assert _state(app, review) == ("active", True, True, False, 0)
        with app.accounts.factory() as db:
            assert db.get(MediaItemExportRow, mcf) is not None
    response = delete(
        app, ADMIN, USER, "delete", body["confirmation"], [(edl, "retain"), (mcf, "remove")]
    )
    assert response.status_code == 200, response.text
    assert response.json()["replayed"] is committed
    assert _state(app, review) == ("disabled", False, False, True, 1)
    after = files_on_disk(app)
    assert mcf not in after and not path.exists()
    assert after[edl][3] == before[edl][3]
    with app.accounts.factory() as db:
        record = db.get(AccountDispositionRow, USER)
        assert record.confirmation == body["confirmation"] and record.actor_id == ADMIN
