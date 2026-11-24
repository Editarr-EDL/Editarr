"""Two contributors cross the real HTTP authorization and signed transport boundaries."""

import base64
import gzip
import hashlib
import json
import shutil
from dataclasses import asdict
from datetime import datetime, timezone

import httpx
import pytest

from scanner.db import (
    AuditEventRow,
    AuthSessionRow,
    CommunityOperationRow,
    ReviewFilterRow,
    ReviewSynchronizationRow,
    ScanFindingRow,
    ScanRow,
    UserCapabilityOverrideRow,
)
from scanner.formats.community.codec import ResourceBounds
from scanner.http import (
    community_activity_access,
    community_api,
    community_identity_api,
    community_sharing_api,
)
from scanner.integrations.community.sharing import previews
from scanner.integrations.community.transport import CommunityTransport, ServiceEndpoint
from scanner.modeling.community_normalization import SEEDED_ALIASES
from scanner.persistence.repository.repository_community import identity_connection
from scanner.security.access.capability_ids import Capability as C
from tests.community_identity_users_support import AUDIENCE, SERVICE, MockService
from tests.multi_user_access_http import serve_access_fixture
from tests.multi_user_access_support import ADMIN, PEER_USER, USER
from tests.test_community_activity_access import activities as activities
from tests.test_community_identity_users import _enroll
from tests.test_stream_fingerprints import generated_streams as generated_streams
from tests.users_api_http_support import UsersApp

BOUNDS = ResourceBounds(65536, 524288, 100, 4096, 24)
ALIASES = [
    {"aliasId": alias.alias_id, "label": alias.label, "spellings": list(alias.spellings)}
    for alias in SEEDED_ALIASES
]


class ActivityService(MockService):
    """Accept `POST /v1/submissions`, replaying the stored result for a repeated operation."""

    def __init__(self):
        super().__init__()
        self.posts = []
        self.submissions = {}

    async def __call__(self, request):
        if request.url.path == "/v1/aliases":
            self._signer(request)
            return httpx.Response(200, json={"version": 1, "aliases": ALIASES})
        if request.url.path == "/v1/submissions":
            installation = self._signer(request)
            contributor = self.contributors[installation]
            body = json.loads(request.content)
            artifact = gzip.decompress(base64.b64decode(body["artifact"]))
            assert json.loads(artifact)["author"]["contributorId"] == contributor
            operation = request.headers["idempotency-key"]
            assert operation == "submit-" + body["submissionId"]
            self.posts.append((contributor, operation))
            self.submissions.setdefault(
                operation,
                {
                    "version": 1,
                    "state": "completed",
                    "result": {
                        "version": 1,
                        "operationId": operation,
                        "submissionId": body["submissionId"],
                        "submissionDigest": hashlib.sha256(
                            base64.b64decode(body["artifact"])
                        ).hexdigest(),
                        "workId": "w_1",
                        "versionId": "v_1",
                        "versionStatus": "confirmed",
                        "disposition": "created_confirmed",
                        "tier": None,
                        "revision": 1,
                        "supersedes": None,
                        "outcome": {
                            "newFilters": 1,
                            "supports": 1,
                            "disputes": 0,
                            "coverageNegatives": 0,
                            "ignored": 0,
                            "unassigned": 0,
                        },
                        "aliasSuggestion": None,
                    },
                },
            )
            return httpx.Response(200, json=self.submissions[operation])
        return await super().__call__(request)


@pytest.fixture
def community(activities, monkeypatch, generated_streams):
    configuration = activities.accounts.configuration.model_copy(
        update={
            "community_service_url": SERVICE,
            "community_service_audience": AUDIENCE,
            "community_preview_bounds": asdict(BOUNDS),
            "community_preview_source_limit": 4,
            "community_publication_request_bytes": 100_000,
        }
    )
    service = ActivityService()
    homes = {
        user: _enroll(activities.accounts, service, user, character * 64)
        for user, character in ((USER, "a"), (ADMIN, "b"), (PEER_USER, "c"))
    }
    for module in (
        community_api,
        community_identity_api,
        community_sharing_api,
        community_activity_access,
    ):
        monkeypatch.setattr(module, "settings", configuration)
    monkeypatch.setattr(
        community_identity_api,
        "service_transport",
        lambda: CommunityTransport(
            ServiceEndpoint(SERVICE, AUDIENCE), adapter=httpx.MockTransport(service)
        ),
    )
    client = serve_access_fixture(monkeypatch, activities.accounts)
    client.app.include_router(community_api.router)
    scan, _ = activities.seed_scan(USER, "community", status="complete")
    review = activities.seed_review(USER, "private", scan)
    with activities.accounts.factory() as db:
        row = db.get(ScanRow, scan)
        shutil.copyfile(generated_streams[0], row.resolved_local_path)
        row.video_hash = hashlib.md5(generated_streams[0].read_bytes()).hexdigest()
        row.duration_seconds = 3
        finding = ScanFindingRow(
            scan_id=scan,
            source="visual",
            category="violence",
            subcategory="violence",
            start_seconds=1,
            end_seconds=2,
            selected=True,
            edit_action="skip",
        )
        db.add(finding)
        db.flush()
        db.add(
            ReviewFilterRow(
                id="synthetic",
                review_id=review,
                source_scan_id=scan,
                source_finding_id=finding.id,
                position=0,
                source="visual",
                category="violence",
                subcategory="violence",
                mcf_channel="video",
                start_seconds=1,
                end_seconds=2,
                selected=True,
                edit_action="skip",
            )
        )
        db.add(
            ReviewSynchronizationRow(
                review_id=review, start_seconds=0, end_seconds=3, end_mode="file_end"
            )
        )
        db.commit()
    yield UsersApp(client, activities.accounts), service, homes, scan, review
    client.close()
    previews.records.clear()


def preview(app, user, kind, identifier):
    return app.send(
        "POST",
        f"/api/community/sharing/sources/{kind}/{identifier}/preview",
        user,
        {
            "name": "Synthetic public filters",
            "identity_confirmed": True,
            "media": {"provider": "tmdb", "entity_type": "movie", "reference": "123"},
            "version_choice": {"choice": "alias", "alias_id": "theatrical"},
        },
    )


def queue(app, user, review):
    prepared = preview(app, user, "review", review)
    assert prepared.status_code == 200, prepared.text
    approved = app.send(
        "POST",
        f"/api/community/sharing/previews/{prepared.json()['previewId']}/approve",
        user,
        {"confirmed": True},
    )
    assert approved.status_code == 200, approved.text
    return prepared.json(), approved.json()


def test_admin_new_publication_and_retry_use_the_correct_contributors(community):
    app, service, homes, _scan, review = community
    own_preview, own = queue(app, USER, review)
    admin_preview, managed = queue(app, ADMIN, review)
    with app.accounts.factory() as db:
        own_contributor = identity_connection(
            db, homes[USER].credential_ref, SERVICE
        ).contributor_id
        admin_contributor = identity_connection(
            db, homes[ADMIN].credential_ref, SERVICE
        ).contributor_id
    assert own_contributor != admin_contributor
    assert own_preview["preview"]["artifact"]["author"]["contributorId"] == own_contributor
    assert admin_preview["preview"]["artifact"]["author"]["contributorId"] == admin_contributor
    assert (own["ownerId"], own["contributorId"]) == (USER, own_contributor)
    assert (managed["ownerId"], managed["contributorId"]) == (ADMIN, admin_contributor)
    context = app.send("GET", f"/api/community/sharing/sources/review/{review}", ADMIN)
    assert context.status_code == 200, context.text
    assert context.json()["sourceOwnerId"] == USER
    assert {(item["ownerId"], item["contributorId"]) for item in context.json()["jobs"]} == {
        (USER, own_contributor),
        (ADMIN, admin_contributor),
    }
    user_context = app.send("GET", f"/api/community/sharing/sources/review/{review}", USER)
    assert user_context.status_code == 200, user_context.text
    assert [item["operationId"] for item in user_context.json()["jobs"]] == [own["operationId"]]
    for job, contributor in ((own, own_contributor), (managed, admin_contributor)):
        result = app.send(
            "POST",
            f"/api/community/publications/{job['operationId']}/retry",
            ADMIN,
            {"confirmed": True},
        )
        assert result.status_code == 200 and result.json()["state"] == "published", result.text
        assert service.posts[-1] == (contributor, job["operationId"])
    before = list(service.posts)
    cancelled = app.send("DELETE", f"/api/community/publications/{own['operationId']}", ADMIN)
    assert cancelled.status_code == 200 and cancelled.json()["state"] == "published"
    assert service.posts == before
    with app.accounts.factory() as db:
        assert (
            db.query(AuditEventRow).filter_by(action="community.publication.retract").count() == 0
        )
        row = db.get(CommunityOperationRow, own["operationId"])
        assert (row.owner_id, row.actor_id, row.contributor_id) == (USER, USER, own_contributor)
        row = db.get(CommunityOperationRow, managed["operationId"])
        assert (row.owner_id, row.actor_id, row.contributor_id) == (ADMIN, ADMIN, admin_contributor)
        events = (
            db.query(AuditEventRow)
            .filter(AuditEventRow.action == "community.publication.publish")
            .all()
        )
        assert {
            (row.actor_id, row.target_user_id, json.loads(row.details_json)["kind"])
            for row in events
        } == {
            (USER, USER, "own_review"),
            (ADMIN, USER, "other_review"),
        }


def test_unsent_publication_cancel_records_local_retraction(community):
    app, service, _homes, _scan, review = community
    _prepared, job = queue(app, USER, review)
    result = app.send("DELETE", f"/api/community/publications/{job['operationId']}", USER)
    assert result.status_code == 200 and result.json()["state"] == "cancelled"
    assert service.posts == []
    with app.accounts.factory() as db:
        event = db.query(AuditEventRow).filter_by(action="community.publication.retract").one()
        assert event.resource == job["operationId"] and event.outcome == "succeeded"


def test_publication_management_remains_visible_without_new_publication_permission(community):
    app, _service, _homes, _scan, review = community
    _prepared, job = queue(app, USER, review)
    with app.accounts.factory() as db:
        for capability in (C.COMMUNITY_PUBLISH_OWN_REVIEW, C.COMMUNITY_PUBLISH_SCAN_EVIDENCE):
            db.add(
                UserCapabilityOverrideRow(user_id=USER, capability=capability.value, effect="deny")
            )
        db.commit()
    context = app.send("GET", f"/api/community/sharing/sources/review/{review}", USER)
    assert context.status_code == 200, context.text
    assert [item["operationId"] for item in context.json()["jobs"]] == [job["operationId"]]
    refused = preview(app, USER, "review", review)
    assert refused.status_code == 403


@pytest.mark.parametrize("kind", ["scan", "review"])
@pytest.mark.parametrize("exists", [True, False])
def test_context_requires_a_publication_grant_before_source_lookup(community, kind, exists):
    app, service, _homes, scan, review = community
    with app.accounts.factory() as db:
        for capability in (
            C.COMMUNITY_PUBLISH_OWN_REVIEW,
            C.COMMUNITY_PUBLISH_OTHER_REVIEW,
            C.COMMUNITY_PUBLISH_SCAN_EVIDENCE,
            C.COMMUNITY_PUBLICATIONS_CHECK_OWN,
            C.COMMUNITY_PUBLICATIONS_CHECK_OTHER,
        ):
            db.add(
                UserCapabilityOverrideRow(user_id=USER, capability=capability.value, effect="deny")
            )
        db.commit()
    identifier = (scan if kind == "scan" else review) if exists else "absent-source"
    result = app.send("GET", f"/api/community/sharing/sources/{kind}/{identifier}", USER)
    assert result.status_code == 403, result.text
    assert result.json()["code"] == "capability_denied"
    assert service.posts == []


def test_private_ids_and_previews_do_not_cross_accounts(community):
    app, service, _homes, scan, review = community
    own_preview, job = queue(app, USER, review)
    for identifier in (job["operationId"], "publish-absent"):
        hidden = app.send("GET", f"/api/community/publications/{identifier}", PEER_USER)
        assert hidden.status_code == 404 and hidden.json()["code"] == "not_found"
    for token in (own_preview["previewId"], "absent-preview"):
        foreign = app.send(
            "POST", f"/api/community/sharing/previews/{token}/approve", ADMIN, {"confirmed": True}
        )
        assert foreign.status_code == 404 and foreign.json()["code"] == "not_found"
    with app.accounts.factory() as db:
        assert db.query(CommunityOperationRow).count() == 1
    hidden = preview(app, PEER_USER, "review", review)
    assert hidden.status_code == 404
    shared = preview(app, PEER_USER, "scan", scan)
    assert shared.status_code == 200, shared.text
    assert service.posts == []


def test_check_only_context_conceals_scan_without_findings_access(community):
    app, service, _homes, scan, _review = community
    with app.accounts.factory() as db:
        for capability in (C.COMMUNITY_PUBLISH_SCAN_EVIDENCE, C.SCANS_FINDINGS_READ):
            db.add(
                UserCapabilityOverrideRow(user_id=USER, capability=capability.value, effect="deny")
            )
        db.commit()
    responses = [
        app.send("GET", f"/api/community/sharing/sources/scan/{identifier}", USER)
        for identifier in (scan, "absent-scan")
    ]
    assert [response.status_code for response in responses] == [404, 404]
    assert responses[0].json() == responses[1].json()
    assert service.posts == []


@pytest.mark.parametrize("change", ["permission", "disconnect", "session"])
def test_recheck_before_sending_prevents_a_queued_send(community, change):
    app, service, homes, _scan, review = community
    _prepared, job = queue(app, USER, review)

    def revoke():
        with app.accounts.factory() as db:
            if change == "permission":
                db.add(
                    UserCapabilityOverrideRow(
                        user_id=USER,
                        capability=C.COMMUNITY_PUBLICATIONS_RETRY_OWN.value,
                        effect="deny",
                    )
                )
            elif change == "session":
                db.query(AuthSessionRow).filter_by(user_id=USER).update(
                    {"revoked_at": datetime.now(timezone.utc).replace(tzinfo=None)}
                )
            else:
                identity_connection(db, homes[USER].credential_ref, SERVICE).contributor_id = None
            db.commit()

    revoke()
    result = app.send(
        "POST", f"/api/community/publications/{job['operationId']}/retry", USER, {"confirmed": True}
    )
    expected = {"permission": 403, "disconnect": 409, "session": 401}[change]
    assert result.status_code == expected, result.text
    if change == "permission":
        assert result.json()["code"] == "capability_denied"
    assert service.posts == []
    with app.accounts.factory() as db:
        row = db.get(CommunityOperationRow, job["operationId"])
        assert row.state == "pending"


def test_transport_retry_rechecks_authority_before_second_send(community, monkeypatch):
    app, service, _homes, _scan, review = community
    _prepared, job = queue(app, USER, review)
    attempts = []
    original = type(service).__call__

    async def flaky(self, request):
        if request.url.path == "/v1/submissions":
            attempts.append(request.headers["idempotency-key"])
            if len(attempts) == 1:
                with app.accounts.factory() as db:
                    db.add(
                        UserCapabilityOverrideRow(
                            user_id=USER,
                            capability=C.COMMUNITY_PUBLICATIONS_RETRY_OWN.value,
                            effect="deny",
                        )
                    )
                    db.commit()
                return httpx.Response(503, headers={"retry-after": "0"}, json={})
        return await original(self, request)

    monkeypatch.setattr(type(service), "__call__", flaky)
    result = app.send(
        "POST",
        f"/api/community/publications/{job['operationId']}/retry",
        USER,
        {"confirmed": True},
    )
    assert len(attempts) == 1 and attempts[0].startswith("submit-sub_")
    assert result.status_code == 403 and result.json()["code"] == "capability_denied"
    assert service.posts == []
    with app.accounts.factory() as db:
        assert db.get(CommunityOperationRow, job["operationId"]).state == "submitted"


def test_lost_preview_permission_is_rechecked_before_queueing(community):
    app, service, _homes, _scan, review = community
    prepared = preview(app, USER, "review", review)
    assert prepared.status_code == 200, prepared.text
    with app.accounts.factory() as db:
        db.add(
            UserCapabilityOverrideRow(
                user_id=USER, capability=C.COMMUNITY_PUBLISH_OWN_REVIEW.value, effect="deny"
            )
        )
        db.commit()
    refused = app.send(
        "POST",
        f"/api/community/sharing/previews/{prepared.json()['previewId']}/approve",
        USER,
        {"confirmed": True},
    )
    assert refused.status_code == 403, refused.text
    assert refused.json()["code"] == "capability_denied"
    with app.accounts.factory() as db:
        assert db.query(CommunityOperationRow).count() == 0
    assert service.posts == []


def test_changed_source_invalidates_preview_without_queueing(community):
    app, service, _homes, _scan, review = community
    prepared = preview(app, USER, "review", review)
    assert prepared.status_code == 200, prepared.text
    with app.accounts.factory() as db:
        db.query(ReviewFilterRow).filter_by(review_id=review).one().label = "Changed after preview"
        db.commit()
    result = app.send(
        "POST",
        f"/api/community/sharing/previews/{prepared.json()['previewId']}/approve",
        USER,
        {"confirmed": True},
    )
    assert result.status_code == 409 and result.json()["code"] == "preview_stale", result.text
    assert service.posts == []
    with app.accounts.factory() as db:
        assert db.query(CommunityOperationRow).count() == 0
