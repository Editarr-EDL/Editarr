"""Contributor-private receipts and votes over authenticated local HTTP."""

import base64
import json
from dataclasses import asdict

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import TypeAdapter

from scanner.db import CommunityImportRow, CommunityOperationRow, ScanRow, UserCapabilityOverrideRow
from scanner.formats.community.artifact_v2 import validate_v2
from scanner.integrations.community.downloads import verify_download_bundle
from scanner.modeling.community_matching import POLICY_VERSION, MatchEvidence, classify
from scanner.modeling.model_community_lookup import LookupSummary
from scanner.persistence.repository.community_import_projection import LocalImportTarget
from scanner.persistence.repository.community_imports import import_verified_download
from scanner.persistence.repository.repository_community import identity_connection
from scanner.security.access.capability_ids import Capability as C
from scanner.security.community_download_receipts import (
    ReceiptFields,
    parse_receipt_keys,
    receipt_identity,
    receipt_signing_bytes,
)
from tests import test_community_activity_http as activity_http
from tests.community_identity_users_support import SERVICE
from tests.multi_user_access_support import ADMIN, PEER_USER, USER
from tests.retired_protocol_support import RETIRED_REVISION_PROTOCOL
from tests.scan_access_http import LIBRARY_A, LIBRARY_B
from tests.test_community_activity_access import activities as activities
from tests.test_community_activity_http import community as community
from tests.test_community_downloads import response
from tests.test_community_verified_import import BOUNDS, RECEIPT_VECTOR, download_input
from tests.test_stream_fingerprints import generated_streams as generated_streams


def bundle(contributor, kind="retimed"):
    value, keys, _ = download_input(kind)
    fields = value["receipt"]["receipt"]
    fields.update(service=SERVICE, contributorId=contributor)
    fields["receiptId"] = receipt_identity(
        SERVICE, contributor, value["revisionId"], value["artifactDigest"]
    )
    signer = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(RECEIPT_VECTOR["syntheticSeedHex"]))
    value["receipt"]["signature"] = base64.b64encode(
        signer.sign(receipt_signing_bytes(ReceiptFields.model_validate(fields)))
    ).decode()
    return value, keys


class FeedbackService(activity_http.ActivityService):
    def __init__(self):
        super().__init__()
        self.feedback = []
        self.current = {}
        self.on_keys = None
        self.on_artifact = None
        self.on_accepted = None

    async def __call__(self, request):
        path = request.url.path
        if path == "/v1/matches":
            contributor = self.contributors[self._signer(request)]
            value, keys = bundle(contributor)
            downloaded = verify_download_bundle(
                value,
                service=SERVICE,
                contribution_id="contribution-root",
                revision_id=value["revisionId"],
                contributor_id=contributor,
                trusted_keys=parse_receipt_keys(keys),
                bounds=BOUNDS,
            )
            evidence = validate_v2(
                downloaded.verified.artifact.artifact, source_limit=BOUNDS.events
            )
            target = TypeAdapter(MatchEvidence).validate_python(
                json.loads(request.content)["evidence"]
            )
            summary = downloaded.verified.artifact.summary
            candidate = {
                "contributionId": "contribution-root",
                "revisionId": value["revisionId"],
                "artifactDigest": value["artifactDigest"],
                "artifactSchema": "community-artifact-2",
                "releaseId": "release",
                "catalogCreatedAt": 1,
                "attribution": {"contributorId": "author", "displayName": "Public Author"},
                "summary": {
                    key: item for key, item in summary.items() if key in LookupSummary.model_fields
                },
                "evidence": asdict(evidence),
                "compatibility": asdict(classify(target, evidence)),
                "statistics": {"imports": 0, "likes": 0, "dislikes": 0},
                "ranking": None,
            }
            return response(
                {
                    "version": 1,
                    "policyVersion": POLICY_VERSION,
                    "candidates": [candidate],
                    "nextCursor": None,
                }
            )
        if path.endswith("/artifact"):
            contributor = self.contributors[self._signer(request)]
            value, _keys = bundle(contributor)
            if self.on_artifact is not None:
                action, self.on_artifact = self.on_artifact, None
                action()
            return response(value)
        if path == "/v1/receipt-keys":
            self._signer(request)
            if self.on_keys is not None:
                action, self.on_keys = self.on_keys, None
                action()
            return response({"version": 1, "service": SERVICE, "keys": bundle("reader")[1]})
        if path == "/v1/import-receipts" or path.endswith(("/vote", "/votes")):
            contributor = self.contributors[self._signer(request)]
            body = json.loads(request.content) if request.content else {}
            operation = request.headers.get("idempotency-key")
            if path == "/v1/import-receipts":
                receipt = body["receipt"]["receipt"]
                assert receipt["contributorId"] == contributor
                result = {
                    "version": 1,
                    "operationId": operation,
                    "revisionId": receipt["revisionId"],
                    "artifactDigest": receipt["artifactDigest"],
                    "receiptId": receipt["receiptId"],
                    "counted": True,
                }
            elif request.method == "GET":
                revision = path.split("/")[-2]
                result = {"version": 1, "revisionId": revision}
                result |= (
                    {"vote": self.current.get(contributor, "neutral"), "eligible": True}
                    if path.endswith("/vote")
                    else {"likes": 1, "dislikes": 1}
                )
                return httpx.Response(200, json=result)
            else:
                result = {
                    "version": 1,
                    "operationId": operation,
                    "revisionId": path.split("/")[-2],
                    "vote": body["vote"],
                }
                self.current[contributor] = body["vote"]
            self.feedback.append((path, contributor, operation, body))
            if self.on_accepted is not None:
                action, self.on_accepted = self.on_accepted, None
                action()
            return httpx.Response(200, json={"version": 1, "state": "completed", "result": result})
        return await super().__call__(request)


@pytest.fixture
def feedback_service(monkeypatch):
    monkeypatch.setattr(activity_http, "ActivityService", FeedbackService)


@pytest.fixture
def feedback(feedback_service, community):
    return community


def import_for(app, service, homes, scan, user, kind="retimed"):
    with app.accounts.factory() as db:
        contributor = identity_connection(db, homes[user].credential_ref, SERVICE).contributor_id
        value, keys = bundle(contributor, kind)
        downloaded = verify_download_bundle(
            value,
            service=SERVICE,
            contribution_id="contribution-root",
            revision_id=value["revisionId"],
            contributor_id=contributor,
            trusted_keys=parse_receipt_keys(keys),
            bounds=BOUNDS,
        )
        source = db.get(ScanRow, scan)
        target = LocalImportTarget(
            source.media_item_id,
            None,
            source.resolved_local_path,
            validate_v2(downloaded.verified.artifact.artifact, source_limit=BOUNDS.events),
        )
        result = import_verified_download(
            db,
            downloaded,
            target,
            bounds=BOUNDS,
            confirm_uncertain=True,
            credential_ref=homes[user].credential_ref,
        )
        return result.import_id, result.scan.id, value["revisionId"], contributor


@RETIRED_REVISION_PROTOCOL
def test_two_contributors_share_evidence_not_receipts_or_votes(feedback):
    app, service, homes, scan, _review = feedback
    first = import_for(app, service, homes, scan, USER)
    second = import_for(app, service, homes, scan, PEER_USER)
    assert first[:3] == second[:3] and first[3] != second[3]
    imported, shared_scan, revision, _ = first
    for user, vote in ((USER, "like"), (PEER_USER, "dislike")):
        acknowledged = app.send(
            "POST", f"/api/community/import-receipts/{imported}/retry", user, {"confirmed": True}
        )
        assert acknowledged.status_code == 200, acknowledged.text
        voted = app.send(
            "POST",
            f"/api/community/votes/{revision}",
            user,
            {"confirmed": True, "vote": vote, "operation_id": f"vote-user-{user}"},
        )
        assert voted.status_code == 200, voted.text
        own = app.send("GET", f"/api/community/votes/{revision}", user)
        assert own.status_code == 200 and own.json()["current"]["vote"] == vote, own.text
    before = list(service.feedback)
    foreign = app.send(
        "POST",
        f"/api/community/votes/{revision}/retry",
        PEER_USER,
        {"confirmed": True, "operation_id": f"vote-user-{USER}"},
    )
    assert foreign.status_code == 404, foreign.text
    assert service.feedback == before
    with app.accounts.factory() as db:
        assert db.query(CommunityImportRow).count() == 1
        receipts = (
            db.query(CommunityOperationRow).filter_by(kind="receipt", scan_id=shared_scan).all()
        )
        assert {
            (row.owner_id, row.actor_id, row.contributor_id, row.state) for row in receipts
        } == {
            (USER, USER, first[3], "acknowledged"),
            (PEER_USER, PEER_USER, second[3], "acknowledged"),
        }
        assert all(
            json.loads(row.receipt)["receipt"]["contributorId"] == row.contributor_id
            for row in receipts
        )


@RETIRED_REVISION_PROTOCOL
def test_receipt_hidden_and_absent_ids_match_without_network_or_writes(feedback):
    app, service, homes, scan, _review = feedback
    imported, _shared_scan, _revision, _contributor = import_for(app, service, homes, scan, USER)
    before = list(service.feedback)
    for identifier in (imported, "absent-import"):
        refused = app.send(
            "POST",
            f"/api/community/import-receipts/{identifier}/retry",
            PEER_USER,
            {"confirmed": True},
        )
        assert refused.status_code == 404 and refused.json()["code"] == "not_found", refused.text
    recovery = app.send("POST", "/api/community/import-receipts/recover", PEER_USER)
    assert recovery.status_code == 200 and recovery.json() == {"importIds": []}, recovery.text
    assert service.feedback == before
    managed = app.send(
        "POST",
        f"/api/community/import-receipts/{imported}/retry?user_id={USER}",
        ADMIN,
        {"confirmed": True},
    )
    assert managed.status_code == 200, managed.text
    assert service.feedback[-1][1] == _contributor


@pytest.mark.parametrize("caller", [ADMIN, USER])
@RETIRED_REVISION_PROTOCOL
def test_receipt_recovery_skips_out_of_scope_imports(feedback, activities, caller):
    app, service, homes, scan, _review = feedback
    hidden_scan, _ = activities.seed_scan(USER, "library-b", library=LIBRARY_B, status="complete")
    visible = import_for(app, service, homes, scan, USER)[0]
    hidden = import_for(app, service, homes, hidden_scan, USER, "manual")[0]
    with app.accounts.factory() as db:
        db.query(CommunityOperationRow).filter_by(kind="receipt").delete()
        db.commit()
    activities.limit_to_library(caller, LIBRARY_A)
    query = f"?user_id={USER}" if caller == ADMIN else ""
    recovered = app.send("POST", f"/api/community/import-receipts/recover{query}", caller)
    assert recovered.status_code == 200, recovered.text
    assert recovered.json() == {"importIds": [visible]}
    with app.accounts.factory() as db:
        rows = db.query(CommunityOperationRow).filter_by(kind="receipt").all()
        assert len(rows) == 1 and rows[0].owner_id == USER and rows[0].state == "pending"
    refused = app.send(
        "POST",
        f"/api/community/import-receipts/{hidden}/retry{query}",
        caller,
        {"confirmed": True},
    )
    assert refused.status_code == 404 and refused.json()["code"] == "not_found"
    assert service.feedback == []


@RETIRED_REVISION_PROTOCOL
def test_receipt_permission_revoked_after_key_read_holds_unsent_operation(feedback):
    app, service, homes, scan, _review = feedback
    imported, _shared_scan, _revision, _contributor = import_for(app, service, homes, scan, USER)

    def revoke():
        with app.accounts.factory() as db:
            db.add(
                UserCapabilityOverrideRow(
                    user_id=USER, capability=C.COMMUNITY_RECEIPTS_RECOVER_OWN.value, effect="deny"
                )
            )
            db.commit()

    service.on_keys = revoke
    result = app.send(
        "POST", f"/api/community/import-receipts/{imported}/retry", USER, {"confirmed": True}
    )
    assert result.status_code == 403, result.text
    assert service.feedback == []
    with app.accounts.factory() as db:
        assert db.query(CommunityOperationRow).filter_by(kind="receipt").one().state == "pending"


@pytest.mark.parametrize("kind", ["receipt", "vote"])
@RETIRED_REVISION_PROTOCOL
def test_accepted_feedback_is_recorded_after_revocation(feedback, kind):
    app, service, homes, scan, _review = feedback
    imported, _shared_scan, revision, _contributor = import_for(app, service, homes, scan, USER)
    receipt_path = f"/api/community/import-receipts/{imported}/retry"
    if kind == "vote":
        assert app.send("POST", receipt_path, USER, {"confirmed": True}).status_code == 200
    capability = (
        C.COMMUNITY_RECEIPTS_RECOVER_OWN if kind == "receipt" else C.COMMUNITY_FEEDBACK_SUBMIT
    )

    def revoke():
        with app.accounts.factory() as db:
            db.add(
                UserCapabilityOverrideRow(user_id=USER, capability=capability.value, effect="deny")
            )
            db.commit()

    service.on_accepted = revoke
    path = receipt_path if kind == "receipt" else f"/api/community/votes/{revision}"
    body = {"confirmed": True}
    if kind == "vote":
        body |= {"vote": "like", "operation_id": "vote-accepted"}
    result = app.send("POST", path, USER, body)
    with app.accounts.factory() as db:
        operation = db.query(CommunityOperationRow).filter_by(kind=kind).one()
        assert operation.state == "acknowledged"
        db.query(UserCapabilityOverrideRow).filter_by(
            user_id=USER, capability=capability.value
        ).delete()
        db.commit()
    assert result.status_code == 200, result.text
    assert service.on_accepted is None
    accepted = [item for item in service.feedback if item[0].endswith("/vote")]
    if kind == "vote":
        assert len(accepted) == 1
        next_vote = app.send(
            "POST",
            path,
            USER,
            {"confirmed": True, "vote": "dislike", "operation_id": "vote-next"},
        )
        assert next_vote.status_code == 200, next_vote.text
        assert service.current[_contributor] == "dislike"
    else:
        assert len(service.feedback) == 1


def test_download_revoked_after_verified_artifact_writes_no_import(feedback, monkeypatch):
    from types import SimpleNamespace

    from scanner.db import MediaItemRow
    from scanner.http import community_downloads_api
    from scanner.integrations.community import lookup_targets
    from scanner.integrations.community.lookup_targets import capture_target
    from scanner.modeling.media_identity import MediaIdentity
    from scanner.modeling.model_community_lookup import LookupRequest
    from scanner.models import JellyfinSettingsUpdate
    from scanner.repository import update_jellyfin_settings
    from tests.scan_access_http import JELLYFIN

    app, service, _homes, scan, _review = feedback
    configuration = activity_http.community_activity_access.settings.model_copy(
        update={
            "community_download_bounds": asdict(BOUNDS),
            "community_download_response_bytes": 1_000_000,
        }
    )
    monkeypatch.setattr(community_downloads_api, "settings", configuration)
    with app.accounts.factory() as db:
        source = db.get(ScanRow, scan)
        media = db.get(MediaItemRow, source.media_item_id)
        update_jellyfin_settings(db, JellyfinSettingsUpdate(server_url=JELLYFIN, api_key="key-1"))
        item = SimpleNamespace(
            path=source.resolved_local_path,
            library_id=media.library_id,
            media_identity=MediaIdentity("movie", ()),
            versions=[],
        )

        class Provider:
            def __init__(self, *_args):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                pass

            def item(self, _identifier):
                return item

        monkeypatch.setattr(lookup_targets, "JellyfinClient", Provider)
        target = {"source": "jellyfin", "item_id": media.jellyfin_id}
        captured = capture_target(db, LookupRequest.model_validate({"target": target}))
        before = (
            db.query(ScanRow).count(),
            db.query(CommunityImportRow).count(),
            db.query(CommunityOperationRow).count(),
        )

    def revoke():
        with app.accounts.factory() as db:
            db.add(
                UserCapabilityOverrideRow(
                    user_id=USER, capability=C.COMMUNITY_DOWNLOAD.value, effect="deny"
                )
            )
            db.commit()

    service.on_artifact = revoke
    value, _keys = bundle("reader")
    result = app.send(
        "POST",
        "/api/community/downloads",
        USER,
        {
            "lookup": {"target": target, "target_key": captured.key},
            "choice": {
                "contributionId": "contribution-root",
                "revisionId": value["revisionId"],
                "artifactDigest": value["artifactDigest"],
                "channels": ["video"],
            },
            "confirmed": True,
        },
    )
    assert result.status_code == 403, result.text
    assert service.on_artifact is None and service.feedback == []
    with app.accounts.factory() as db:
        assert (
            db.query(ScanRow).count(),
            db.query(CommunityImportRow).count(),
            db.query(CommunityOperationRow).count(),
        ) == before
