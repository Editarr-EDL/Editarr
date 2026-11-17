"""Exact preview approval is local, session-bound and separate from upload."""

import asyncio
import gzip
import hashlib
import json
from dataclasses import replace

import httpx
import pytest
from sqlalchemy.orm import Session

from scanner.db import (
    CommunityImportRow,
    CommunityOperationRow,
    CommunityPublicationRow,
    ReviewFilterRow,
    ScanRow,
)
from scanner.integrations.community.client import CommunityClient
from scanner.integrations.community.donation_sources import SourceRef
from scanner.integrations.community.donations import prepare_donation
from scanner.integrations.community.sharing import TERMS_VERSION, PreviewStore, SharingService
from scanner.integrations.community.transport import (
    CommunityFailure,
    CommunityTransport,
    ServiceEndpoint,
)
from scanner.persistence.repository.repository_community import (
    set_connection,
)
from scanner.security.community_identity import (
    load_or_create_installation_identity,
    save_identity_registration,
)
from tests.community_identity_support import client_home, only_connection, only_identity_ref
from tests.test_community_api import context as context
from tests.test_community_api import headers
from tests.test_community_donations import BOUNDS, options
from tests.test_community_donations import source_database as source_database


def service(engine, tmp_path):
    db = Session(engine)
    home = client_home(db, tmp_path)
    identity = load_or_create_installation_identity(home)
    save_identity_registration(
        home,
        contributor_id="contributor-1",
        service_url="https://community.example",
        last_known_key_state="active",
    )
    set_connection(
        db,
        credential_ref=only_identity_ref(db),
        service_url="https://community.example",
        contributor_id="contributor-1",
        installation_id=identity.installation_id,
        key_id=identity.key_id,
    )
    db.commit()
    transport = CommunityTransport(
        ServiceEndpoint("https://community.example", "community.example"),
        adapter=httpx.MockTransport(lambda _: pytest.fail("preview must not send")),
    )
    client = CommunityClient(db, client_home(db, tmp_path), transport)
    return SharingService(
        client, bounds=BOUNDS, source_limit=4, request_bytes=100_000, store=PreviewStore()
    )


def test_preview_is_public_local_and_does_not_enable_sharing(source_database, tmp_path):
    sharing = service(source_database, tmp_path)
    response = sharing.prepare(
        SourceRef("scan", "scan"), owner="session-a", name="Public name", media=options().media
    )
    assert response["preview"]["artifact"]["sharing_terms"] == TERMS_VERSION
    assert response["preview"]["artifact"]["name"] == "Public name"
    assert "PRIVATE FULL TRANSCRIPT" not in str(response)
    assert "/private/media.mp4" not in str(response)
    assert sharing.db.query(CommunityOperationRow).count() == 0
    assert only_connection(sharing.db, "https://community.example").sharing_enabled is False
    with pytest.raises(CommunityFailure, match="preview_unavailable"):
        sharing.store.get(response["previewId"], "other-session", sharing.scope)
    sharing.db.close()


def test_changed_source_invalidates_exact_preview_before_any_network(source_database, tmp_path):
    sharing = service(source_database, tmp_path)
    response = sharing.prepare(
        SourceRef("scan", "scan"), owner="session-a", name="Public name", media=options().media
    )
    with Session(source_database) as db:
        db.get(ScanRow, "scan").name = "Source edited"
        db.commit()
    with pytest.raises(CommunityFailure, match="preview_stale"):
        asyncio.run(sharing.approve(response["previewId"], owner="session-a", confirmed=True))
    sharing.db.close()


@pytest.mark.parametrize(
    "provider,entity,url,identifier",
    [
        ("tmdb", "movie", "https://www.themoviedb.org/movie/123-title", "123"),
        ("tvdb", "movie", "https://www.thetvdb.com/dereferrer/movie/123", "123"),
        ("tvdb", "episode", "https://thetvdb.com/series/psych/episodes/307497", "307497"),
    ],
)
def test_public_reference_urls_and_ids_agree(provider, entity, url, identifier):
    from scanner.http.community_sharing_api import PublicReference

    first = PublicReference(provider=provider, entity_type=entity, reference=url).identity()
    second = PublicReference(provider=provider, entity_type=entity, reference=identifier).identity()
    assert first == second


def test_episode_context_is_explicit_and_imdb_is_not_supported():
    from pydantic import ValidationError

    from scanner.http.community_sharing_api import PublicReference

    identity = PublicReference(
        provider="tmdb",
        entity_type="episode",
        reference="https://www.themoviedb.org/tv/12/season/0/episode/2",
    ).identity()
    assert identity.series_identifiers[0].value == "12"
    assert identity.episode_numbers[0].season == 0
    assert identity.episode_numbers[0].number == 2
    with pytest.raises(ValidationError):
        PublicReference(provider="imdb", entity_type="movie", reference="tt123")
    with pytest.raises(ValueError, match="Conflicting episode series"):
        PublicReference(
            provider="tmdb",
            entity_type="episode",
            series_reference="13",
            reference="https://www.themoviedb.org/tv/12/season/0/episode/2",
        ).identity()


def test_approval_queues_exact_bytes_and_recovers_without_resending(source_database, tmp_path):
    from tests.test_community_client import profile

    sharing = service(source_database, tmp_path)
    response = sharing.prepare(
        SourceRef("scan", "scan"), owner="session-a", name="Public name", media=options().media
    )
    identity = load_or_create_installation_identity(sharing.client.home)
    calls = []

    def handler(request):
        calls.append(request)
        assert request.method == "GET" and request.url.path == "/v1/identity"
        return httpx.Response(200, json=profile(identity) | {"contributorId": "contributor-1"})

    sharing.client.transport.adapter = httpx.MockTransport(handler)
    job = asyncio.run(sharing.approve(response["previewId"], owner="session-a", confirmed=True))
    assert job["state"] == "pending"
    assert len(calls) == 1
    row = sharing.db.get(CommunityPublicationRow, job["operationId"])
    assert json.loads(gzip.decompress(row.artifact_bytes)) == response["preview"]["artifact"]
    assert json.loads(row.manifest_bytes) == response["preview"]["manifest"]
    connection = only_connection(sharing.db, "https://community.example")
    assert connection.consent_version == TERMS_VERSION and connection.sharing_enabled
    sharing.db.rollback()
    with Session(source_database) as db:
        db.get(ScanRow, "scan").name = "Changed after queue"
        db.commit()
    assert (
        asyncio.run(sharing.approve(response["previewId"], owner="session-a", confirmed=True))
        == job
    )
    assert len(calls) == 1
    assert sharing.context(SourceRef("scan", "scan"))["jobs"] == [job]
    sharing.db.close()


def test_preview_expiry_scope_discard_and_capacity(source_database, tmp_path):
    sharing = service(source_database, tmp_path)
    now = [10.0]
    sharing.store.clock = lambda: now[0]
    response = sharing.prepare(
        SourceRef("scan", "scan"), owner="session-a", name="Public name", media=options().media
    )
    with pytest.raises(CommunityFailure, match="preview_unavailable"):
        sharing.store.get(response["previewId"], "session-a", "different-service")
    now[0] = 910.0
    with pytest.raises(CommunityFailure, match="preview_expired"):
        sharing.store.get(response["previewId"], "session-a", sharing.scope)
    fresh = sharing.prepare(
        SourceRef("scan", "scan"), owner="session-a", name="Public name", media=options().media
    )
    sharing.store.discard(fresh["previewId"], "session-a", sharing.scope)
    with pytest.raises(CommunityFailure, match="preview_expired"):
        sharing.store.get(fresh["previewId"], "session-a", sharing.scope)
    for _ in range(16):
        sharing.prepare(
            SourceRef("scan", "scan"), owner="session-a", name="Public name", media=options().media
        )
    with pytest.raises(CommunityFailure, match="preview_capacity"):
        sharing.prepare(
            SourceRef("scan", "scan"), owner="session-a", name="Public name", media=options().media
        )
    sharing.db.close()


@pytest.mark.parametrize(
    "terms,code",
    [
        (TERMS_VERSION, None),
        ("other-permission", "source_permission_incompatible"),
        (None, "source_permission_unknown"),
    ],
)
def test_original_permissions_and_attribution_are_not_relicensed(
    source_database, tmp_path, terms, code
):
    original = prepare_donation(
        source_database,
        SourceRef("scan", "scan"),
        replace(options(), sharing_terms=terms),
        bounds=BOUNDS,
        source_limit=4,
    )
    with Session(source_database) as db:
        db.get(ScanRow, "scan").origin = "imported"
        db.add(
            CommunityImportRow(
                id="import",
                scan_id="scan",
                service_url="https://community.example",
                contribution_id="original",
                revision_id="submission-1",
                digest=hashlib.sha256(original.artifact_bytes).hexdigest(),
                author_id="contributor-1",
                artifact_type="scan",
                artifact_bytes=original.artifact_bytes if terms else None,
            )
        )
        db.commit()
    sharing = service(source_database, tmp_path)
    if code:
        with pytest.raises(CommunityFailure, match=code):
            sharing.prepare(
                SourceRef("scan", "scan"),
                owner="session-a",
                name="Public reuse",
                media=options().media,
            )
    else:
        response = sharing.prepare(
            SourceRef("scan", "scan"), owner="session-a", name="Public reuse", media=options().media
        )
        assert response["preview"]["artifact"]["sources"][0]["author_id"] == "contributor-1"
        assert (
            response["preview"]["artifact"]["source_references"][0]["submission_id"]
            == "submission-1"
        )
        assert sharing.context(SourceRef("scan", "scan"))["audioSources"][0]["track"] is None
    sharing.db.close()


def test_mixed_preview_uses_actual_source_preserves_video_and_review(source_database, tmp_path):
    from tests.test_community_donations import audio_selection, seed_mixed_review

    seed_mixed_review(source_database)
    with Session(source_database) as db:
        db.get(ScanRow, "scan").requested_audio_selection_json = audio_selection(
            8, "PRIVATE ALTERNATE TRACK", "de"
        )
        db.get(ReviewFilterRow, ("review", "f1")).mcf_comment = "Public review note"
        db.commit()
        before = [
            (row.id, row.selected, row.mcf_comment) for row in db.query(ReviewFilterRow).all()
        ]
    sharing = service(source_database, tmp_path)
    context = sharing.context(SourceRef("review", "review"))
    assert "PRIVATE ALTERNATE TRACK" not in str(context)
    assert len(context["audioSources"]) == 2
    response = sharing.prepare(
        SourceRef("review", "review"),
        owner="session-a",
        name="Derived",
        media=options().media,
        audio_source_scan_id="scan",
    )
    assert response["preview"]["artifact"]["analyzed_audio"]["language"] == "en"
    assert any(item["channel"] == "video" for item in response["preview"]["artifact"]["findings"])
    assert response["preview"]["exclusions"][0]["reason"] == "other_audio_track"
    assert response["preview"]["artifact"]["findings"][0]["note"] == "Public review note"
    with Session(source_database) as db:
        assert [
            (row.id, row.selected, row.mcf_comment) for row in db.query(ReviewFilterRow).all()
        ] == before
    sharing.db.close()


def test_privileged_http_preview_approval_and_upload_are_separate(
    context, source_database, tmp_path, monkeypatch
):
    import base64

    from scanner.http import community_api, community_sharing_api
    from tests.test_community_client import profile

    client, issued = context
    sharing = service(source_database, tmp_path)
    identity = load_or_create_installation_identity(sharing.client.home)
    calls = []

    def handler(request):
        calls.append(request)
        assert request.headers.get("signature")
        if request.url.path == "/v1/identity":
            return httpx.Response(200, json=profile(identity) | {"contributorId": "contributor-1"})
        if request.method == "GET":
            return httpx.Response(404, json={"error": {"code": "operation_not_found"}})
        body = json.loads(request.content)
        assert request.url.path == "/v1/contributions"
        return httpx.Response(
            200,
            json={
                "version": 1,
                "state": "completed",
                "publicationState": "published",
                "result": {
                    "version": 1,
                    "operationId": request.headers["idempotency-key"],
                    "contributionId": body["contributionId"],
                    "revisionId": body["submissionId"],
                    "artifactDigest": hashlib.sha256(
                        base64.b64decode(body["artifact"])
                    ).hexdigest(),
                    "committedAt": 123,
                },
            },
        )

    sharing.client.transport.adapter = httpx.MockTransport(handler)
    monkeypatch.setattr(
        community_sharing_api, "sharing_service", lambda _db, _access, **_options: sharing
    )
    monkeypatch.setattr(community_sharing_api, "settings", community_api.settings)
    monkeypatch.setattr(community_api, "publication_outbox", lambda *_args: sharing.outbox)
    valid = headers(issued)
    source = "/editarr/api/community/sharing/sources/scan/scan"
    body = {
        "name": "Public HTTP donation",
        "identity_confirmed": True,
        "media": {
            "provider": "tmdb",
            "entity_type": "movie",
            "reference": "https://www.themoviedb.org/movie/123",
        },
    }
    response = client.post(source + "/preview", headers=valid, json=body)
    assert response.status_code == 200 and response.headers["Cache-Control"] == "no-store"
    preview = response.json()
    assert len(calls) == 0 and sharing.db.query(CommunityOperationRow).count() == 0
    sharing.db.rollback()
    approval = "/editarr/api/community/sharing/previews/" + preview["previewId"] + "/approve"
    assert (
        client.post(approval, headers=valid, json={"confirmed": False}).json()["code"]
        == "confirmation_required"
    )
    assert client.post(approval, headers=valid, json={"confirmed": "true"}).status_code == 422
    queued = client.post(approval, headers=valid, json={"confirmed": True})
    assert queued.status_code == 200 and queued.json()["state"] == "pending"
    assert all(request.method == "GET" for request in calls)
    job = queued.json()
    assert client.post(approval, headers=valid, json={"confirmed": True}).json() == job
    published = client.post(
        "/editarr/api/community/publications/" + job["operationId"] + "/retry",
        headers=valid,
        json={"confirmed": True},
    )
    assert published.status_code == 200 and published.json()["state"] == "published"
    uploaded = json.loads(next(request.content for request in calls if request.method == "POST"))
    assert (
        json.loads(gzip.decompress(base64.b64decode(uploaded["artifact"])))
        == preview["preview"]["artifact"]
    )
    assert json.loads(base64.b64decode(uploaded["manifest"])) == preview["preview"]["manifest"]
    for canary in (
        "PRIVATE FULL TRANSCRIPT",
        "/private/media.mp4",
        "private_bytes",
        "consent_token",
    ):
        assert canary not in response.text + queued.text + published.text + str(uploaded)
    sharing.db.close()


def test_current_access_failure_does_not_queue_or_enable_sharing(source_database, tmp_path):
    sharing = service(source_database, tmp_path)
    response = sharing.prepare(
        SourceRef("scan", "scan"), owner="session-a", name="Public", media=options().media
    )
    sharing.client.transport.adapter = httpx.MockTransport(
        lambda _: httpx.Response(403, json={"error": {"code": "membership_ineligible"}})
    )
    with pytest.raises(CommunityFailure, match="membership_ineligible"):
        asyncio.run(sharing.approve(response["previewId"], owner="session-a", confirmed=True))
    assert sharing.db.query(CommunityOperationRow).count() == 0
    assert only_connection(sharing.db, "https://community.example").sharing_enabled is False
    sharing.db.close()
