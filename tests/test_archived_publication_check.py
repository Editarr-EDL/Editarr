# ruff: noqa: F811
"""A publication of archived work that reached the server can still learn its outcome.

0025 and 0026 leave such a publication `submitted` with a cancel request. Only an
archive viewer who may also check that contributor's publications may check it,
through the contributor who sent it, and nobody may resend or change it.
"""

import pytest
from sqlalchemy import text

from scanner.db import CommunityOperationRow, UserCapabilityOverrideRow
from scanner.security.access.capability_ids import Capability as C
from tests.multi_user_access_support import ADMIN, USER
from tests.test_community_activity_access import activities as activities  # noqa: F401
from tests.test_community_activity_http import community, queue  # noqa: F401
from tests.test_stream_fingerprints import generated_streams as generated_streams  # noqa: F401


def _archive_sent(app, service, operation_id, source):
    with app.accounts.factory() as db:
        operation = db.get(CommunityOperationRow, operation_id)
        service.publications[operation_id] = {
            "version": 1,
            "state": "completed",
            "publicationState": "published",
            "result": {
                "version": 1,
                "operationId": operation_id,
                "contributionId": operation.contribution_id,
                "revisionId": operation.revision_id,
                "committedAt": 1700000030,
                "artifactDigest": operation.digest,
            },
        }
        db.execute(
            text("UPDATE community_operations SET state = 'submitted' WHERE operation_id = :id"),
            {"id": operation_id},
        )
        db.execute(
            text("UPDATE community_publications SET cancel_requested = 1 WHERE operation_id = :id"),
            {"id": operation_id},
        )
        db.execute(
            text(
                "UPDATE media_items SET archived_source = :source WHERE id = "
                "(SELECT media_item_id FROM saved_reviews WHERE id = "
                "(SELECT source_id FROM community_publications WHERE operation_id = :id))"
            ),
            {"id": operation_id, "source": source},
        )
        db.commit()


SOURCES = pytest.mark.parametrize("source", ["radarr", "manual_files"])


@SOURCES
def test_an_archive_viewer_learns_a_sent_archived_publication_was_published(community, source):
    app, service, _homes, _scan, review = community
    _prepared, job = queue(app, USER, review)
    operation_id = job["operationId"]
    _archive_sent(app, service, operation_id, source)
    path = f"/api/community/publications/{operation_id}"
    assert app.send("GET", path, ADMIN).json()["state"] == "cancel_pending"
    checked = app.send("POST", f"{path}/check", ADMIN)
    assert checked.status_code == 200, checked.text
    assert checked.json()["state"] == "published"
    assert service.posts == []
    with app.accounts.factory() as db:
        assert db.get(CommunityOperationRow, operation_id).state == "acknowledged"


@SOURCES
def test_nobody_resends_or_cancels_an_archived_publication_and_members_cannot_check_it(
    community, source
):
    app, service, _homes, _scan, review = community
    _prepared, job = queue(app, USER, review)
    operation_id = job["operationId"]
    _archive_sent(app, service, operation_id, source)
    path = f"/api/community/publications/{operation_id}"
    assert app.send("POST", f"{path}/check", USER).status_code == 404
    assert app.send("GET", path, USER).status_code == 404
    assert app.send("POST", f"{path}/retry", ADMIN, {"confirmed": True}).status_code == 404
    assert app.send("DELETE", path, ADMIN).status_code == 404
    assert service.posts == []
    with app.accounts.factory() as db:
        assert db.get(CommunityOperationRow, operation_id).state == "submitted"


def _deny(app, user, capability):
    with app.accounts.factory() as db:
        db.add(UserCapabilityOverrideRow(user_id=user, capability=capability.value, effect="deny"))
        db.commit()


@SOURCES
@pytest.mark.parametrize("denied", [None, C.COMMUNITY_PUBLICATIONS_CHECK_OTHER])
def test_checking_another_users_archived_publication_needs_check_other(
    community, denied, source
):
    app, service, _homes, _scan, review = community
    _prepared, job = queue(app, USER, review)
    operation_id = job["operationId"]
    _archive_sent(app, service, operation_id, source)
    if denied is not None:
        _deny(app, ADMIN, denied)
    path = f"/api/community/publications/{operation_id}"
    checked = app.send("POST", f"{path}/check", ADMIN)
    with app.accounts.factory() as db:
        state = db.get(CommunityOperationRow, operation_id).state
    if denied is None:
        assert checked.status_code == 200, checked.text
        assert state == "acknowledged"
    else:
        assert checked.status_code == 404, checked.text
        assert app.send("GET", path, ADMIN).status_code == 404
        assert state == "submitted"
    assert service.posts == []


@pytest.mark.parametrize(
    "denied",
    [C.COMMUNITY_PUBLICATIONS_CHECK_OTHER, C.COMMUNITY_PUBLICATIONS_CHECK_OWN],
)
@SOURCES
def test_an_archive_viewer_checks_their_own_archived_publication_with_check_own(
    community, denied, source
):
    app, service, _homes, _scan, review = community
    _prepared, job = queue(app, ADMIN, review)
    operation_id = job["operationId"]
    _archive_sent(app, service, operation_id, source)
    _deny(app, ADMIN, denied)
    checked = app.send("POST", f"/api/community/publications/{operation_id}/check", ADMIN)
    with app.accounts.factory() as db:
        state = db.get(CommunityOperationRow, operation_id).state
    if denied == C.COMMUNITY_PUBLICATIONS_CHECK_OTHER:
        assert checked.status_code == 200, checked.text
        assert state == "acknowledged"
    else:
        assert checked.status_code == 403, checked.text
        assert checked.json()["code"] == "capability_denied"
        assert state == "submitted"
