"""Feedback follows verified local copies without changing contributor ownership."""

from contextlib import contextmanager

import pytest

from scanner.db import (
    CommunityImportTargetRow,
    CommunityOperationRow,
    MediaAccessScopeRow,
    MediaAccessScopeSourceRow,
    ScanRow,
    UserRow,
)
from scanner.persistence.repository import review_access
from scanner.security.access.capability_ids import Capability as C
from scanner.security.access.overrides import OverrideEffect
from tests.multi_user_access_support import ADMIN, PEER_USER, USER
from tests.retired_protocol_support import RETIRED_REVISION_PROTOCOL
from tests.scan_access_http import LIBRARY_A, LIBRARY_B
from tests.test_community_activity_access import activities as activities
from tests.test_community_activity_feedback import (
    feedback as feedback,
)
from tests.test_community_activity_feedback import (
    feedback_service as feedback_service,
)
from tests.test_community_activity_feedback import (
    import_for,
)
from tests.test_community_activity_http import community as community
from tests.test_stream_fingerprints import generated_streams as generated_streams

pytestmark = RETIRED_REVISION_PROTOCOL


def test_feedback_uses_each_contributors_accessible_verified_copy(feedback, activities):
    app, service, homes, scan, _review = feedback
    other_scan, _ = activities.seed_scan(
        PEER_USER, "recipient-copy", library=LIBRARY_B, status="complete"
    )
    first = import_for(app, service, homes, scan, USER)
    second = import_for(app, service, homes, other_scan, PEER_USER)
    assert first[:3] == second[:3] and first[3] != second[3]
    imported, _shared_scan, revision, _contributor = first
    activities.limit_to_library(USER, LIBRARY_A)
    activities.limit_to_library(PEER_USER, LIBRARY_B)
    activities.limit_to_library(ADMIN, "c" * 32)
    receipt_path = f"/api/community/import-receipts/{imported}/retry"
    vote_path = f"/api/community/votes/{revision}"
    for caller, vote in ((USER, "like"), (PEER_USER, "dislike")):
        result = app.send("POST", receipt_path, caller, {"confirmed": True})
        assert result.status_code == 200, result.text
        result = app.send(
            "POST",
            vote_path,
            caller,
            {"confirmed": True, "vote": vote, "operation_id": f"vote-copy-{caller}"},
        )
        assert result.status_code == 200, result.text
        result = app.send("GET", vote_path, caller)
        assert result.status_code == 200 and result.json()["current"]["vote"] == vote, result.text
        result = app.send(
            "POST",
            vote_path + "/retry",
            caller,
            {"confirmed": True, "operation_id": f"vote-copy-{caller}"},
        )
        assert result.status_code == 200, result.text
    before = list(service.feedback)
    for method, path, body in (
        ("POST", receipt_path + f"?user_id={USER}", {"confirmed": True}),
        ("GET", vote_path, None),
        ("POST", vote_path, {"confirmed": True, "vote": "like", "operation_id": "vote-no-copy"}),
        ("POST", vote_path + "/retry", {"confirmed": True, "operation_id": f"vote-copy-{USER}"}),
    ):
        refused = app.send(method, path, ADMIN, body)
        assert refused.status_code == 404 and refused.json()["code"] == "not_found", refused.text
    assert service.feedback == before
    with app.accounts.factory() as db:
        rows = db.query(CommunityOperationRow).filter_by(kind="receipt").all()
        assert {(row.owner_id, row.contributor_id, row.state) for row in rows} == {
            (USER, first[3], "acknowledged"),
            (PEER_USER, second[3], "acknowledged"),
        }


@pytest.mark.parametrize("kind", ["receipt", "vote"])
@pytest.mark.parametrize("change", ["association", "media_scope", "findings"])
def test_feedback_rechecks_verified_copy_under_write_lock(
    feedback, activities, monkeypatch, kind, change
):
    app, service, homes, scan, _review = feedback
    other_scan, _ = activities.seed_scan(
        PEER_USER, "racing-copy", library=LIBRARY_B, status="complete"
    )
    imported, _shared_scan, revision, _ = import_for(app, service, homes, scan, USER)
    import_for(app, service, homes, other_scan, PEER_USER)
    receipt_path = f"/api/community/import-receipts/{imported}/retry"
    if kind == "vote":
        assert app.send("POST", receipt_path, PEER_USER, {"confirmed": True}).status_code == 200
    with app.accounts.factory() as db:
        media_id = db.get(ScanRow, other_scan).media_item_id
    activities.limit_to_library(PEER_USER, LIBRARY_B)
    original = review_access.write_lock
    raced = []

    @contextmanager
    def racing_lock(db):
        if not raced:
            raced.append(change)
            if change == "association":
                with app.accounts.factory() as racing:
                    racing.query(CommunityImportTargetRow).filter_by(
                        import_id=imported, media_item_id=media_id
                    ).delete()
                    racing.commit()
            elif change == "media_scope":
                with app.accounts.factory() as racing:
                    scope = racing.query(MediaAccessScopeRow).filter_by(user_id=PEER_USER).one()
                    racing.query(MediaAccessScopeSourceRow).filter_by(scope_id=scope.id).delete()
                    racing.get(UserRow, PEER_USER).permission_revision += 1
                    racing.commit()
            else:
                app.accounts.set_override(PEER_USER, C.SCANS_FINDINGS_READ, OverrideEffect.DENY)
        with original(db):
            yield

    monkeypatch.setattr(review_access, "write_lock", racing_lock)
    before = list(service.feedback)
    path = receipt_path if kind == "receipt" else f"/api/community/votes/{revision}"
    body = {"confirmed": True}
    if kind == "vote":
        body |= {"vote": "like", "operation_id": "vote-racing-copy"}
    result = app.send("POST", path, PEER_USER, body)
    assert raced == [change]
    assert result.status_code == 404 and result.json()["code"] == "not_found", result.text
    assert service.feedback == before


@pytest.mark.parametrize("kind", ["receipt", "vote"])
def test_unverified_copy_does_not_authorize_feedback(feedback, activities, kind):
    app, service, homes, scan, _review = feedback
    other_scan, _ = activities.seed_scan(
        PEER_USER, "unverified-copy", library=LIBRARY_B, status="complete"
    )
    imported, _shared_scan, revision, _ = import_for(app, service, homes, scan, USER)
    import_for(app, service, homes, other_scan, PEER_USER)
    receipt_path = f"/api/community/import-receipts/{imported}/retry"
    if kind == "vote":
        assert app.send("POST", receipt_path, PEER_USER, {"confirmed": True}).status_code == 200
    with app.accounts.factory() as db:
        media_id = db.get(ScanRow, other_scan).media_item_id
        db.query(CommunityImportTargetRow).filter_by(
            import_id=imported, media_item_id=media_id
        ).delete()
        db.commit()
    activities.limit_to_library(PEER_USER, LIBRARY_B)
    before = list(service.feedback)
    path = receipt_path if kind == "receipt" else f"/api/community/votes/{revision}"
    result = app.send(
        "POST",
        path,
        PEER_USER,
        {
            "confirmed": True,
            **({"vote": "like", "operation_id": "vote-unverified"} if kind == "vote" else {}),
        },
    )
    assert result.status_code == 404 and result.json()["code"] == "not_found", result.text
    assert service.feedback == before
