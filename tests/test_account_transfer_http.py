"""Transfer: reviews, policy, and personal files move to another account (MU-38).

Rulings wave13-mu38-transfer (2026-10-28) through the real HTTP routes. Bytes
are compared on disk at the old and new export IDs; community transport is the
only mocked boundary and records no call.
"""

import json
import sqlite3
import time
from pathlib import Path

import pytest

from scanner.db import (
    CommunityOperationRow,
    MediaItemExportRow,
    SavedReviewRow,
    ScanRow,
    UserFilterPreferenceRow,
    UserRow,
)
from scanner.http import review_deletion_api
from scanner.persistence.repository.export_regeneration import (
    claim_regeneration,
    policy_identity,
)
from scanner.persistence.rows.db_account_dispositions import (
    AccountDispositionRow,
    AccountTransferMoveRow,
)
from scanner.runtime.export_regeneration import settle_regeneration
from scanner.security.access.capability_ids import Capability as C
from scanner.security.access.overrides import OverrideEffect
from tests.multi_user_access_support import ADMIN, OWNER, PEER_ADMIN, PEER_USER, USER
from tests.scan_access_http import LIBRARY_B
from tests.test_account_deletion_http import (
    account,
    bulk,
    code,
    confirmed,
    delete,
    deletion_audits,
    deletion_routes,
    exports_app,
    preview,
    remote_publication,
)
from tests.test_personal_exports_http import publish, review_app, seeded_review

__all__ = ["exports_app", "review_app"]


@pytest.fixture
def app(exports_app, monkeypatch):
    served = deletion_routes(exports_app, monkeypatch)
    served.client.app.include_router(review_deletion_api.router)
    return served


def export_id(app, user):
    with app.accounts.factory() as db:
        return db.get(UserRow, user).local_export_id


def rows(app):
    with app.accounts.factory() as db:
        return {
            row.id: {
                "target": row.target_user_id,
                "export_id": row.target_export_id,
                "path": Path(row.destination_path),
                "revision": row.publication_revision,
                "uuid": json.loads(row.snapshot_json)["publication_id"],
                "review": row.review_id,
                "stamp": row.policy_owner_id,
                "intended": row.intended_policy_owner_id,
                "author": row.original_author_id,
                "owner": policy_identity(db, row)[0],
            }
            for row in db.query(MediaItemExportRow)
        }


def published_account(app):
    """USER's review with both formats, a retained file of a deleted review, and an
    ADMIN-targeted file of USER's review."""
    review = seeded_review(app, USER, "moving")
    edl, mcf = (publish(app, review, fmt).json()["id"] for fmt in ("edl", "mcf"))
    admin_copy = publish(app, review, "edl", user=ADMIN).json()["id"]
    gone = seeded_review(app, USER, "deleted-earlier")
    retained = publish(app, gone, "edl").json()["id"]
    body = app.send("GET", f"/api/reviews/{gone.id}/deletion", app.accounts.cookie(USER)).json()
    response = app.send(
        "POST",
        f"/api/reviews/{gone.id}/deletion",
        app.accounts.unsafe_cookie(USER),
        {
            "revision": body["revision"],
            "confirmation": body["confirmation"],
            "files": [{"export_id": retained, "choice": "retain"}],
        },
    )
    assert response.status_code == 200, response.text
    return review, {"edl": edl, "mcf": mcf, "admin": admin_copy, "retained": retained}


def test_transfer_moves_reviews_policy_and_files_to_the_destination(app):
    review, ids = published_account(app)
    remote_publication(app, USER)
    with app.accounts.factory() as db:
        scan = db.get(SavedReviewRow, review.id).memberships[0].scan_id
        db.get_one(UserFilterPreferenceRow, PEER_USER).policy_revision = 3
        db.commit()
    before = rows(app)
    content = {key: value["path"].read_bytes() for key, value in before.items()}
    body = preview(app, ADMIN, USER, "transfer", PEER_USER).json()
    assert body["destination_id"] == PEER_USER and body["blockers"] == []
    assert body["reviews"] == {"count": 1, "hidden": 0, "ids": [review.id]}
    assert body["transfer"] == {"files_moved": 3, "policy_files": 1}
    response = delete(app, ADMIN, USER, "transfer", body["confirmation"], destination=PEER_USER)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["reviews_transferred"] == [review.id] and result["files_moved"] == 3
    assert (result["destination_id"], result["personal_files_kept"]) == (PEER_USER, 0)
    after = rows(app)
    destination_id = export_id(app, PEER_USER)
    for key in ("edl", "mcf", "retained"):
        old, new = before[ids[key]], after[ids[key]]
        assert (new["target"], new["export_id"]) == (PEER_USER, destination_id)
        assert new["path"] != old["path"] and destination_id in str(new["path"])
        assert new["path"].read_bytes() == content[ids[key]] and not old["path"].exists()
        assert (new["uuid"], new["revision"]) == (old["uuid"], old["revision"] + 1)
        assert (new["owner"], new["author"]) == (PEER_USER, USER)
    assert after[ids["retained"]]["intended"] == PEER_USER
    admin = after[ids["admin"]]
    assert (admin["target"], admin["path"], admin["owner"]) == (
        ADMIN,
        before[ids["admin"]]["path"],
        PEER_USER,
    )
    with app.accounts.factory() as db:
        moved = db.get(SavedReviewRow, review.id)
        assert (moved.owner_id, moved.original_author_id) == (PEER_USER, USER)
        assert db.get(ScanRow, scan).initiator_id == USER
        assert db.get(CommunityOperationRow, f"published-{USER}").state == "acknowledged"
        record = db.get(AccountDispositionRow, USER)
        assert (record.mode, record.destination_id) == ("transfer", PEER_USER)
        assert json.loads(record.impact_json) == body
        assert {row.outcome for row in db.query(AccountTransferMoveRow)} == {"removed"}
    assert account(app, USER) == ("disabled", "local", False, None)
    assert app.remote_calls == []
    audit = deletion_audits(app)[-1]
    assert audit[0] == "succeeded"
    assert {
        key: audit[3][key] for key in ("mode", "destination_id", "files_moved", "policy_files")
    } == {
        "mode": "transfer",
        "destination_id": PEER_USER,
        "files_moved": 3,
        "policy_files": 1,
    }
    regenerated = set()
    for _attempt in range(4):
        with app.accounts.factory() as db:
            job = claim_regeneration(db, now=time.time() + 3600)
            if job is None:
                break
            assert (job.policy_owner_id, job.policy_revision) == (PEER_USER, 3)
            assert settle_regeneration(db, job) == "succeeded"
            regenerated.add(job.export_id)
    assert regenerated == set(ids.values())
    with app.accounts.factory() as db:
        for key in ids.values():
            row = db.get(MediaItemExportRow, key)
            assert (row.policy_owner_id, row.policy_revision) == (PEER_USER, 3)
            assert Path(row.destination_path).read_bytes() == row.content_bytes
    replay = delete(app, ADMIN, USER, "transfer", body["confirmation"], destination=PEER_USER)
    assert replay.json() == result | {"replayed": True}


def test_destinations_must_be_active_visible_accounts_other_than_the_target(app):
    confirmed(app, ADMIN, PEER_USER, "retain")
    assert (
        app.send(
            "PUT",
            f"/api/users/{PEER_ADMIN}/status",
            app.accounts.unsafe_cookie(OWNER),
            {"status": "disabled"},
        ).status_code
        == 200
    )
    for destination in (USER, PEER_USER, PEER_ADMIN, 999):
        assert code(preview(app, ADMIN, USER, "transfer", destination)) == (
            409,
            "transfer_destination_unavailable",
        )
    body = preview(app, ADMIN, USER, "transfer", OWNER).json()
    stale = delete(app, ADMIN, USER, "transfer", body["confirmation"], destination=999)
    assert code(stale) == (409, "transfer_destination_unavailable")
    assert deletion_audits(app)[-1][3] == {"reason": "transfer_destination_unavailable"}
    assert preview(app, ADMIN, USER, "transfer", ADMIN).status_code == 200
    assert code(preview(app, ADMIN, USER, "transfer")) == (422, "transfer_destination_required")
    missing = app.send(
        "POST",
        f"/api/users/{USER}/deletion",
        app.accounts.unsafe_cookie(ADMIN),
        {"mode": "transfer", "confirmation": body["confirmation"]},
    )
    assert missing.status_code == 422
    stray = delete(
        app, ADMIN, USER, "transfer", body["confirmation"], [(1, "remove")], destination=OWNER
    )
    assert code(stray) == (422, "account_deletion_choice_required")
    _body, result = confirmed(app, ADMIN, USER, "transfer", destination=OWNER)
    assert result["destination_id"] == OWNER and account(app, USER)[0] == "disabled"


def test_media_the_destination_cannot_access_blocks_the_whole_transfer(app):
    review, ids = published_account(app)
    before = rows(app)
    app.limit_to_library(PEER_USER, LIBRARY_B)
    body = preview(app, ADMIN, USER, "transfer", PEER_USER).json()
    assert body["blockers"] == [{"code": "transfer_media_inaccessible", "count": 1}]
    refused = delete(app, ADMIN, USER, "transfer", body["confirmation"], destination=PEER_USER)
    assert code(refused) == (409, "account_deletion_blocked")
    assert rows(app) == before and account(app, USER)[0] == "active"
    with app.accounts.factory() as db:
        assert db.get(SavedReviewRow, review.id).owner_id == USER
        assert db.query(AccountTransferMoveRow).count() == 0
    owner = preview(app, ADMIN, USER, "transfer", OWNER).json()
    assert owner["blockers"] == []


def test_reviews_the_actor_cannot_view_block_a_transfer_to_any_destination(app):
    """Ruling wave13-mu38-transfer-visibility: the delete-mode blocker, with a count."""
    review = seeded_review(app, USER, "private-review")
    app.accounts.set_override(ADMIN, C.REVIEWS_VIEW_OTHER, OverrideEffect.DENY)
    for destination in (ADMIN, OWNER):
        body = preview(app, ADMIN, USER, "transfer", destination).json()
        assert body["reviews"] == {"count": 1, "hidden": 1, "ids": []}
        assert body["blockers"] == [{"code": "reviews_outside_access", "count": 1}]
        refused = delete(
            app, ADMIN, USER, "transfer", body["confirmation"], destination=destination
        )
        assert code(refused) == (409, "account_deletion_blocked")
    assert deletion_audits(app)[-1][3] == {"reason": "account_deletion_blocked"}
    with app.accounts.factory() as db:
        assert db.get(SavedReviewRow, review.id).owner_id == USER
    hidden = app.send("GET", f"/api/reviews/{review.id}", app.accounts.cookie(ADMIN))
    assert hidden.status_code == 404 and account(app, USER)[0] == "active"


def test_a_destination_publication_at_the_same_path_blocks_with_a_count(app):
    review = seeded_review(app, USER, "both-published")
    publish(app, review, "edl")
    assert publish(app, review, "edl", user=ADMIN, target="self").status_code == 200
    body = preview(app, ADMIN, USER, "transfer", ADMIN).json()
    assert body["blockers"] == [{"code": "export_filename_collision", "count": 1}]
    assert code(delete(app, ADMIN, USER, "transfer", body["confirmation"], destination=ADMIN)) == (
        409,
        "account_deletion_blocked",
    )


def test_stale_transfer_previews_refuse_and_bulk_transfers_report_each_target(app):
    seeded_review(app, USER, "first")
    first = preview(app, ADMIN, USER, "transfer", ADMIN).json()
    seeded_review(app, USER, "second")
    assert code(delete(app, ADMIN, USER, "transfer", first["confirmation"], destination=ADMIN)) == (
        409,
        "stale_account_deletion",
    )
    current = preview(app, ADMIN, USER, "transfer", ADMIN).json()
    peer = preview(app, ADMIN, PEER_USER, "transfer", ADMIN).json()
    response = bulk(
        app,
        ADMIN,
        [
            {
                "user_id": USER,
                "mode": "transfer",
                "destination_id": ADMIN,
                "confirmation": current["confirmation"],
            },
            {
                "user_id": PEER_USER,
                "mode": "transfer",
                "destination_id": USER,
                "confirmation": peer["confirmation"],
            },
        ],
    )
    outcomes = [
        (item["user_id"], item["outcome"], item["code"]) for item in response.json()["results"]
    ]
    assert outcomes == [
        (USER, "applied", None),
        (PEER_USER, "refused", "transfer_destination_unavailable"),
    ]
    with app.accounts.factory() as db:
        assert {row.owner_id for row in db.query(SavedReviewRow)} == {ADMIN}


def test_a_busy_database_after_the_switch_reports_the_committed_transfer(app, monkeypatch):
    from sqlalchemy.exc import OperationalError

    from scanner.persistence.repository import account_transfer_files

    _review, ids = published_account(app)
    before = rows(app)
    finish = account_transfer_files.finish
    locked = sqlite3.OperationalError("database is locked")
    locked.sqlite_errorcode = 5

    def busy(_db, _user_id):
        raise OperationalError("UPDATE", {}, locked)

    monkeypatch.setattr(account_transfer_files, "finish", busy)
    body = preview(app, ADMIN, USER, "transfer", PEER_USER).json()
    response = delete(app, ADMIN, USER, "transfer", body["confirmation"], destination=PEER_USER)
    assert response.status_code == 200, response.text
    assert response.json()["files_moved"] == 3
    assert account(app, USER)[0] == "disabled"
    assert before[ids["edl"]]["path"].exists()
    assert deletion_audits(app)[-1][0] == "succeeded"

    monkeypatch.setattr(account_transfer_files, "finish", finish)
    with app.accounts.factory() as db:
        account_transfer_files.recover(db, user_id=USER)
        assert {row.outcome for row in db.query(AccountTransferMoveRow)} == {"removed"}
    assert not before[ids["edl"]]["path"].exists()
    replay = delete(app, ADMIN, USER, "transfer", body["confirmation"], destination=PEER_USER)
    assert replay.json() == response.json() | {"replayed": True}
