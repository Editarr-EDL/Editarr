"""Signed one-target lookup privacy, failure and mutation contracts."""

import asyncio
import json
from dataclasses import asdict, replace
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from scanner.db import Base
from scanner.integrations.community import lookup
from scanner.integrations.community.client import CommunityClient
from scanner.integrations.community.lookup_targets import CapturedTarget
from scanner.integrations.community.transport import (
    CommunityFailure,
    CommunityTransport,
    ServiceEndpoint,
)
from scanner.modeling.community_matching import BANDS, POLICY_VERSION, MatchEvidence, classify
from scanner.modeling.community_ranking import rank_revision
from scanner.modeling.media_identity import MediaIdentity, ProviderIdentifier
from scanner.modeling.model_community_lookup import LookupRequest
from scanner.pipeline.audio_tracks import _file_state
from scanner.security.community_identity import (
    load_or_create_installation_identity,
    save_identity_registration,
)
from tests.community_identity_support import client_home
from tests.test_community_api import context as context
from tests.test_community_api import headers

REQUEST = {"target": {"source": "jellyfin", "item_id": "movie-1", "media_source_id": "source-1"}}


@pytest.fixture
def captured(tmp_path, monkeypatch):
    path = tmp_path / "PRIVATE-PATH-CANARY.mp4"
    path.write_bytes(b"synthetic temporary media")
    media = MediaIdentity("movie", (ProviderIdentifier("tmdb", "movie", "123", "jellyfin.movie"),))
    value = CapturedTarget(path, _file_state(path), MatchEvidence(media), "a" * 64, ())
    monkeypatch.setattr(lookup, "capture_target", lambda *_args, **_kwargs: value)
    return value


def availability():
    return {
        "version": 1,
        "policyVersion": POLICY_VERSION,
        "items": [
            {
                "id": "target",
                "status": "ok",
                "candidateCount": 0,
                "truncated": False,
                "channels": {channel: dict.fromkeys(BANDS, 0) for channel in ("audio", "video")},
            }
        ],
    }


def page(evidence=None):
    candidates = []
    if evidence:
        candidates = [
            {
                "contributionId": "root-1",
                "revisionId": "revision-1",
                "artifactDigest": "b" * 64,
                "artifactSchema": "community-artifact-1",
                "releaseId": "release-1",
                "catalogCreatedAt": 1,
                "attribution": {"contributorId": "author-1", "displayName": "Public Author"},
                "summary": {
                    "schema_version": "community-summary-1",
                    "registry_version": "mcf-1.1.0",
                    "submission_id": "revision-1",
                    "kind": "scan",
                    "work_ids": [],
                    "release": None,
                    "duration_seconds": 120,
                    "release_version": None,
                    "scanner_behavior_version": None,
                    "total": 1,
                    "sources": {"visual": 1},
                    "channels": {"video": 1},
                    "taxonomy": {},
                    "coverage": [],
                },
                "evidence": asdict(evidence),
                "compatibility": asdict(classify(evidence, evidence)),
                "statistics": {"imports": 0, "likes": 0, "dislikes": 0},
                "ranking": None,
            }
        ]
    return {
        "version": 1,
        "policyVersion": POLICY_VERSION,
        "candidates": candidates,
        "nextCursor": None,
    }


def run(tmp_path, handler, body=None, should_cancel=None):
    async def delay(_seconds):
        pass

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        home = client_home(db, tmp_path)
        load_or_create_installation_identity(home)
        save_identity_registration(
            home,
            contributor_id="requester",
            service_url="https://community.test",
            last_known_key_state="active",
        )
        client = CommunityClient(
            db,
            client_home(db, tmp_path),
            CommunityTransport(
                ServiceEndpoint("https://community.test", "community.test"),
                adapter=httpx.MockTransport(handler),
                delay=delay,
            ),
        )
        return asyncio.run(
            lookup.CommunityLookup(client).run(
                LookupRequest.model_validate(body or REQUEST), should_cancel=should_cancel
            )
        )


def test_signed_exact_public_evidence_and_unselected_candidates(tmp_path, captured):
    sent = []

    def handler(request):
        sent.append(request)
        return httpx.Response(
            200,
            json=availability() if request.url.path.endswith("batch") else page(captured.evidence),
        )

    result = run(tmp_path, handler)
    assert result["page"]["candidates"][0]["compatibility"]["selected"] is False
    assert result["targetKey"] == captured.key
    assert len(sent) == 2
    for request in sent:
        assert request.headers["signature"]
        assert request.headers["idempotency-key"]
        assert "PRIVATE-PATH-CANARY" not in request.content.decode()
        assert "stream_index" not in request.content.decode()
        assert "file_version" not in request.content.decode()
    assert json.loads(sent[0].content)["items"][0]["id"] == "target"


def test_shared_ranking_vectors_and_signed_lookup_projection(tmp_path, captured):
    contract = json.loads(
        (Path(__file__).parent / "fixtures/community/ranking-v1.json").read_text()
    )
    for example in contract["cases"]:
        ranking = rank_revision(example["likes"], example["dislikes"])
        assert ranking["policyVersion"] == contract["policyVersion"]
        assert ranking["score"] == example["score"]
        assert ranking["uploaderBoost"] == 0

    def handler(request):
        if request.url.path.endswith("batch"):
            return httpx.Response(200, json=availability())
        value = page(captured.evidence)
        value["candidates"][0]["ranking"] = rank_revision(0, 0)
        return httpx.Response(200, json=value)

    result = run(tmp_path, handler)
    assert result["page"]["candidates"][0]["ranking"] == rank_revision(0, 0)


@pytest.mark.parametrize("alter", ["policy", "score", "count", "boost", "boolean", "extra"])
def test_corrupt_ranking_fails_closed(tmp_path, captured, alter):
    def handler(request):
        if request.url.path.endswith("batch"):
            return httpx.Response(200, json=availability())
        value = page(captured.evidence)
        ranking = rank_revision(0, 0)
        if alter == "policy":
            ranking["policyVersion"] = "future-ranking"
        elif alter == "score":
            ranking["score"] = 0.99
        elif alter == "count":
            ranking["voteCount"] = 1
        elif alter == "boost":
            ranking["uploaderBoost"] = 0.1
        elif alter == "boolean":
            ranking["uploaderBoost"] = False
        else:
            ranking["private_path"] = "PRIVATE"
        value["candidates"][0]["ranking"] = ranking
        return httpx.Response(200, json=value)

    result = run(tmp_path, handler)
    assert result["page"] is None and result["pageError"]["code"] == "invalid_response"


@pytest.mark.parametrize("likes,dislikes", [(-1, 0), (0, True), (0.5, 0), ((1 << 53) - 1, 0)])
def test_ranking_rejects_invalid_counts(likes, dislikes):
    with pytest.raises(ValueError, match="invalid_ranking_counts"):
        rank_revision(likes, dislikes)


@pytest.mark.parametrize(
    "code,status",
    [
        ("service_unavailable", 503),
        ("membership_unavailable", 503),
        ("membership_ineligible", 403),
        ("lookup_limit", 413),
    ],
)
def test_failures_are_not_empty_matches(tmp_path, captured, code, status):
    result = run(
        tmp_path,
        lambda _request: httpx.Response(
            status, json={"error": {"code": code, "secret": "PRIVATE"}}
        ),
    )
    assert result["availability"] is None and result["page"] is None
    assert result["availabilityError"]["code"] == code
    assert "PRIVATE" not in json.dumps(result)


def test_offline_and_partial_page_failure(tmp_path, captured):
    def offline(request):
        raise httpx.ReadError("PRIVATE", request=request)

    result = run(tmp_path, offline)
    assert result["availabilityError"]["code"] == "network_unavailable"

    def partial(request):
        return (
            httpx.Response(200, json=availability())
            if request.url.path.endswith("batch")
            else httpx.Response(413, json={"error": {"code": "lookup_limit"}})
        )

    result = run(tmp_path, partial)
    assert result["availability"]["items"][0]["candidateCount"] == 0
    assert result["page"] is None and result["pageError"]["code"] == "lookup_limit"


def test_missing_identity_skips_matches_and_keeps_null_counts(tmp_path, captured):
    sent = []

    def handler(request):
        sent.append(request)
        value = availability()
        value["items"][0].update(status="missing_identity", candidateCount=None, channels=None)
        return httpx.Response(200, json=value)

    result = run(tmp_path, handler)
    assert len(sent) == 1
    assert result["availability"]["items"][0]["candidateCount"] is None
    assert result["pageError"]["code"] == "missing_identity"


def test_changed_file_rejects_late_network_response(tmp_path, captured):
    def handler(_request):
        captured.path.write_bytes(b"changed")
        return httpx.Response(200, json=availability())

    with pytest.raises(CommunityFailure, match="file_changed"):
        run(tmp_path, handler)


@pytest.mark.parametrize(
    "alter", ["policy", "extra", "selected", "numeric_selected", "negative_count", "compatibility"]
)
def test_corrupt_catalog_response_fails_closed(tmp_path, captured, alter):
    def handler(request):
        if request.url.path.endswith("batch"):
            return httpx.Response(200, json=availability())
        value = page(captured.evidence)
        if alter == "policy":
            value["policyVersion"] = "future-policy"
        elif alter == "extra":
            value["candidates"][0]["summary"]["private_path"] = "PRIVATE"
        elif alter == "selected":
            value["candidates"][0]["compatibility"]["selected"] = True
        elif alter == "numeric_selected":
            value["candidates"][0]["compatibility"]["selected"] = 0
        elif alter == "negative_count":
            value["candidates"][0]["summary"]["channels"]["video"] = -1
        else:
            value["candidates"][0]["compatibility"]["video"]["band"] = "exact_file"
        return httpx.Response(200, json=value)

    if alter == "policy":
        with pytest.raises(CommunityFailure, match="unsupported_policy"):
            run(tmp_path, handler)
    else:
        result = run(tmp_path, handler)
        assert result["page"] is None and result["pageError"]["code"] == "invalid_response"


def test_invalid_capture_state_blocks_network(tmp_path, captured, monkeypatch):
    monkeypatch.setattr(
        lookup, "capture_target", lambda *_args, **_kwargs: replace(captured, state=(0,))
    )
    with pytest.raises(CommunityFailure, match="file_changed"):
        run(tmp_path, lambda _request: pytest.fail("must not send"))


def test_cancelled_capture_blocks_network(tmp_path, captured):
    with pytest.raises(CommunityFailure, match="lookup_cancelled"):
        run(tmp_path, lambda _request: pytest.fail("must not send"), should_cancel=lambda: True)


def test_cancelled_after_availability_blocks_matches(tmp_path, captured):
    from threading import Event

    cancelled = Event()

    def handler(request):
        assert request.url.path.endswith("batch")
        cancelled.set()
        return httpx.Response(200, json=availability())

    with pytest.raises(CommunityFailure, match="lookup_cancelled"):
        run(tmp_path, handler, should_cancel=cancelled.is_set)


def test_request_disconnect_cancels_lookup_and_sets_capture_signal(monkeypatch):
    from scanner.http import community_lookup_api

    observed = []

    class Lookup:
        def __init__(self, _client):
            pass

        async def run(self, _body, *, should_cancel):
            observed.append(should_cancel)
            await asyncio.Event().wait()

    class Request:
        async def is_disconnected(self):
            return True

    monkeypatch.setattr(community_lookup_api, "target_client", lambda *_args: None)
    monkeypatch.setattr(community_lookup_api, "CommunityLookup", Lookup)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            community_lookup_api.lookup(
                LookupRequest.model_validate(REQUEST), Request(), None, None
            )
        )
    assert observed[0]() is True


def test_outer_request_cancellation_signals_capture_before_cleanup(monkeypatch):
    from scanner.http import community_lookup_api

    async def scenario():
        started = asyncio.Event()
        cleaned = []

        class Lookup:
            def __init__(self, _client):
                pass

            async def run(self, _body, *, should_cancel):
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    assert should_cancel()
                    cleaned.append(True)

        class Request:
            async def is_disconnected(self):
                return False

        monkeypatch.setattr(community_lookup_api, "target_client", lambda *_args: None)
        monkeypatch.setattr(community_lookup_api, "CommunityLookup", Lookup)
        pending = asyncio.create_task(
            community_lookup_api.lookup(
                LookupRequest.model_validate(REQUEST), Request(), None, None
            )
        )
        await started.wait()
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert cleaned == [True]

    asyncio.run(scenario())


def test_lookup_route_requires_real_admin_and_csrf(context):
    client, issued = context
    url = "/editarr/api/community/lookup"
    assert client.post(url, json=REQUEST).status_code == 401
    valid = headers(issued)
    assert (
        client.post(
            url,
            json=REQUEST,
            headers={key: value for key, value in valid.items() if key != "X-CSRF-Token"},
        ).status_code
        == 403
    )
    assert (
        client.post(
            url, json=REQUEST, headers=valid | {"Origin": "https://attacker.test"}
        ).status_code
        == 403
    )
    result = client.post(url, json=REQUEST, headers=valid)
    assert result.json()["code"] == "not_configured"
    assert result.headers["cache-control"] == "no-store"


def test_local_request_rejects_caller_evidence_and_inventory(context):
    client, issued = context
    for field in ("evidence", "tracks", "fingerprints"):
        result = client.post(
            "/editarr/api/community/lookup", json=REQUEST | {field: []}, headers=headers(issued)
        )
        assert result.status_code == 422
