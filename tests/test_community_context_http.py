"""Publication management remains local and independently authorized while disconnected."""

import pytest

from scanner.db import CommunityOperationRow, CommunityPublicationRow, UserCapabilityOverrideRow
from scanner.persistence.repository.repository_community import identity_connection
from scanner.security.access.capability_ids import Capability as C
from tests.community_identity_users_support import SERVICE
from tests.multi_user_access_support import ADMIN, PEER_ADMIN, USER
from tests.test_community_activity_access import activities as activities
from tests.test_community_activity_http import community as community
from tests.test_community_activity_http import preview, queue
from tests.test_stream_fingerprints import generated_streams as generated_streams


@pytest.mark.parametrize("disconnect_during_send", [False, True])
def test_reconnected_publication_resumes_despite_historical_connection_error(
    community, disconnect_during_send
):
    app, service, homes, _scan, review = community
    _prepared, job = queue(app, USER, review)
    operation_id = job["operationId"]
    contributor_id = job["contributorId"]
    if disconnect_during_send:

        def disconnect():
            with app.accounts.factory() as db:
                identity_connection(db, homes[USER].credential_ref, SERVICE).contributor_id = None
                db.commit()

        service.on_check = disconnect
        raced = app.send(
            "POST", f"/api/community/publications/{operation_id}/retry", USER, {"confirmed": True}
        )
        assert raced.status_code == 409
        assert service.posts == []
        with app.accounts.factory() as db:
            assert db.get(CommunityPublicationRow, operation_id).error_code is None
            identity_connection(
                db, homes[USER].credential_ref, SERVICE
            ).contributor_id = contributor_id
            db.commit()
        service.on_check = None
    else:
        with app.accounts.factory() as db:
            db.get(CommunityPublicationRow, operation_id).error_code = "not_connected"
            db.commit()
    context = app.send("GET", f"/api/community/sharing/sources/review/{review}", USER)
    assert context.status_code == 200, context.text
    observed = context.json()["jobs"][0]
    assert (observed["state"], observed["errorCode"]) == ("pending", None)
    sent = app.send(
        "POST", f"/api/community/publications/{operation_id}/retry", USER, {"confirmed": True}
    )
    assert sent.status_code == 200 and sent.json()["state"] == "published"
    assert service.posts == [(contributor_id, operation_id)]


def test_disconnected_contributor_holds_only_their_visible_job(community):
    app, service, homes, _scan, review = community
    _prepared, managed = queue(app, USER, review)
    _prepared, own = queue(app, ADMIN, review)
    with app.accounts.factory() as db:
        identity_connection(db, homes[USER].credential_ref, SERVICE).contributor_id = None
        db.commit()
    context = app.send("GET", f"/api/community/sharing/sources/review/{review}", ADMIN)
    assert context.status_code == 200, context.text
    jobs = {job["operationId"]: job for job in context.json()["jobs"]}
    assert set(jobs) == {own["operationId"], managed["operationId"]}
    assert (jobs[managed["operationId"]]["state"], jobs[managed["operationId"]]["errorCode"]) == (
        "paused",
        "not_connected",
    )
    assert jobs[managed["operationId"]]["contributorId"] == managed["contributorId"]
    assert jobs[own["operationId"]]["state"] == "pending"
    assert service.posts == []
    refused = app.send(
        "POST",
        f"/api/community/publications/{managed['operationId']}/retry",
        ADMIN,
        {"confirmed": True},
    )
    assert refused.status_code == 409 and refused.json()["code"] == "not_connected"
    assert service.posts == []
    with app.accounts.factory() as db:
        assert db.get(CommunityOperationRow, managed["operationId"]).state == "pending"
    sent = app.send(
        "POST",
        f"/api/community/publications/{own['operationId']}/retry",
        ADMIN,
        {"confirmed": True},
    )
    assert sent.status_code == 200 and sent.json()["state"] == "published"
    assert service.posts == [(own["contributorId"], own["operationId"])]


@pytest.mark.parametrize("actor", [USER, ADMIN])
def test_held_unsent_publication_can_be_cancelled_locally(community, actor):
    app, service, homes, _scan, review = community
    _prepared, job = queue(app, USER, review)
    operation_id = job["operationId"]
    with app.accounts.factory() as db:
        identity_connection(db, homes[USER].credential_ref, SERVICE).contributor_id = None
        db.commit()
    requests = []
    service.on_check = lambda: requests.append("check")
    cancelled = app.send("DELETE", f"/api/community/publications/{operation_id}", actor)
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["state"] == "cancelled"
    assert cancelled.json()["cancelRequested"] is True
    assert cancelled.json()["contributorId"] == job["contributorId"]
    assert requests == [] and service.posts == []
    with app.accounts.factory() as db:
        assert db.get(CommunityOperationRow, operation_id).state == "pending"
        assert db.get(CommunityPublicationRow, operation_id).cancel_requested is True


def test_held_publication_cancel_still_requires_retract_permission(community):
    app, service, homes, _scan, review = community
    _prepared, job = queue(app, USER, review)
    with app.accounts.factory() as db:
        identity_connection(db, homes[USER].credential_ref, SERVICE).contributor_id = None
        db.add(
            UserCapabilityOverrideRow(
                user_id=ADMIN,
                capability=C.COMMUNITY_PUBLICATIONS_RETRACT_OTHER.value,
                effect="deny",
            )
        )
        db.commit()
    refused = app.send("DELETE", f"/api/community/publications/{job['operationId']}", ADMIN)
    assert refused.status_code == 404 and refused.json()["code"] == "not_found"
    assert service.posts == []
    with app.accounts.factory() as db:
        assert db.get(CommunityPublicationRow, job["operationId"]).cancel_requested is False


@pytest.mark.parametrize("manager", [ADMIN, PEER_ADMIN])
def test_manager_connection_is_not_required_for_existing_publications(community, manager):
    app, service, homes, _scan, review = community
    _prepared, managed = queue(app, USER, review)
    if manager == ADMIN:
        with app.accounts.factory() as db:
            identity_connection(db, homes[ADMIN].credential_ref, SERVICE).contributor_id = None
            db.commit()
    context = app.send("GET", f"/api/community/sharing/sources/review/{review}", manager)
    assert context.status_code == 200, context.text
    assert context.json()["accessVerified"] is False
    assert context.json()["accessError"] == "not_connected"
    assert [job["operationId"] for job in context.json()["jobs"]] == [managed["operationId"]]
    assert service.posts == []
    sent = app.send(
        "POST",
        f"/api/community/publications/{managed['operationId']}/retry",
        manager,
        {"confirmed": True},
    )
    assert sent.status_code == 200 and sent.json()["state"] == "published"
    assert service.posts == [(managed["contributorId"], managed["operationId"])]
    refused = preview(app, manager, "review", review)
    assert refused.status_code == 409 and refused.json()["code"] == "not_connected"


def test_viewer_identity_file_failure_preserves_other_contributors_context(community):
    app, service, homes, _scan, review = community
    _prepared, managed = queue(app, USER, review)
    (homes[ADMIN].root / "identity.json").unlink()
    context = app.send("GET", f"/api/community/sharing/sources/review/{review}", ADMIN)
    assert context.status_code == 200, context.text
    assert context.json()["accessVerified"] is False
    assert context.json()["accessError"] == "identity_incomplete"
    assert [job["operationId"] for job in context.json()["jobs"]] == [managed["operationId"]]
    assert context.json()["jobs"][0]["state"] == "pending"
    assert service.posts == []
    sent = app.send(
        "POST",
        f"/api/community/publications/{managed['operationId']}/retry",
        ADMIN,
        {"confirmed": True},
    )
    assert sent.status_code == 200 and sent.json()["state"] == "published"
    assert service.posts == [(managed["contributorId"], managed["operationId"])]
