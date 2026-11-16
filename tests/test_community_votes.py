"""Exact vote retry and private-current-state checks on disposable imported evidence."""

import asyncio
import json

import httpx
import pytest
from sqlalchemy.orm import Session

from scanner.db import CommunityOperationRow
from scanner.integrations.community.client import CommunityClient
from scanner.integrations.community.transport import CommunityFailure
from scanner.integrations.community.votes import VoteOutbox
from scanner.persistence.repository.repository_community import set_connection
from scanner.security.community_identity import save_identity_registration
from tests.community_identity_support import client_home
from tests.test_community_downloads import response
from tests.test_community_import_receipts import committed, outbox
from tests.test_community_verified_import import import_database as import_database


def sender(db, tmp_path, handle):
    imported = committed(db, tmp_path)
    operation = db.query(CommunityOperationRow).one()
    operation.state = "acknowledged"
    revision_id = operation.revision_id
    db.commit()
    return VoteOutbox(outbox(db, tmp_path, handle).client), imported, revision_id


@pytest.mark.parametrize("vote", ["like", "dislike", "neutral"])
def test_exact_vote_retry_and_fresh_read(import_database, tmp_path, vote):
    calls = []
    current = "neutral"

    def handle(request):
        nonlocal current
        if request.method == "GET":
            revision_id = request.url.path.split("/")[-2]
            value = {"version": 1, "revisionId": revision_id}
            return response(
                value
                | (
                    {"vote": current, "eligible": True}
                    if request.url.path.endswith("/vote")
                    else {"likes": 0, "dislikes": 1}
                )
            )
        calls.append(request)
        if len(calls) <= 3:
            current = "dislike"
            raise httpx.ReadError("lost response")
        return response(
            {
                "version": 1,
                "state": "completed",
                "result": {
                    "version": 1,
                    "operationId": request.headers["idempotency-key"],
                    "revisionId": request.url.path.split("/")[-2],
                    "vote": json.loads(request.content)["vote"],
                },
            }
        )

    with Session(import_database) as db:
        votes, _, revision_id = sender(db, tmp_path, handle)
        votes.prepare(revision_id, vote, "vote-one")
        with pytest.raises(CommunityFailure):
            asyncio.run(votes.run(revision_id, "vote-one"))
        state = asyncio.run(votes.read(revision_id))
        assert state["pending"]["vote"] == vote
        assert state["current"]["vote"] == "dislike"
        with pytest.raises(CommunityFailure, match="vote_pending"):
            votes.prepare(revision_id, "neutral", "vote-two")
        asyncio.run(votes.run(revision_id, "vote-one"))
        assert asyncio.run(votes.read(revision_id))["current"]["vote"] == "dislike"
        asyncio.run(votes.run(revision_id, "vote-one"))
        assert len(calls) == 4
        assert len({call.content for call in calls}) == 1
        assert len({call.headers["signature-input"] for call in calls}) == 4
        assert len({call.headers["idempotency-key"] for call in calls}) == 1


def test_offline_read_recovers_pending_without_cached_own_vote(import_database, tmp_path):
    with Session(import_database) as db:
        votes, _, revision_id = sender(
            db,
            tmp_path,
            lambda request: httpx.Response(503, json={"error": {"code": "service_unavailable"}}),
        )
        votes.prepare(revision_id, "neutral", "vote-offline")
        state = asyncio.run(votes.read(revision_id))
        assert state["current"] is None and state["totals"] is None
        assert state["pending"]["operationId"] == "vote-offline"
        assert state["error"]["code"] == "service_unavailable"


def test_disconnected_vote_stays_pending_and_reconnect_sends_the_original_intent(
    import_database, tmp_path
):
    calls = []

    def handle(request):
        calls.append(request)
        return response(
            {
                "version": 1,
                "state": "completed",
                "result": {
                    "version": 1,
                    "operationId": "vote-held",
                    "revisionId": revision_id,
                    "vote": "neutral",
                },
            }
        )

    with Session(import_database) as db:
        votes, _, revision_id = sender(db, tmp_path, handle)
        home = client_home(db, tmp_path)
        identity = save_identity_registration(
            home,
            contributor_id="reader",
            service_url=votes.client.transport.endpoint.url,
            last_known_key_state="active",
        )
        set_connection(
            db,
            credential_ref=home.credential_ref,
            service_url=identity.service_url,
            contributor_id=identity.contributor_id,
            installation_id=identity.installation_id,
            key_id=identity.key_id,
        )
        db.query(CommunityOperationRow).update({"installation_id": identity.installation_id})
        db.commit()
        votes.client = CommunityClient(db, home, votes.client.transport)
        votes.prepare(revision_id, "neutral", "vote-held")
        identity = votes.client._identity()
        db.rollback()
        votes.client.disconnect()
        db.rollback()
        with pytest.raises(CommunityFailure, match="not_connected"):
            asyncio.run(votes.run(revision_id, "vote-held"))
        operation = db.get(CommunityOperationRow, "vote-held")
        assert operation.state == "pending" and calls == []
        saved = operation.receipt
        db.rollback()
        assert votes.client.start()["connected"] is True
        assert votes.client._identity().contributor_id == identity.contributor_id
        db.rollback()
        assert asyncio.run(votes.run(revision_id, "vote-held"))["state"] == "completed"
        assert len(calls) == 1 and calls[0].content == saved.encode()
        assert calls[0].headers["idempotency-key"] == "vote-held"


def test_restored_vote_retries_original_bytes_under_same_contributor(import_database, tmp_path):
    import sqlite3

    from sqlalchemy import create_engine

    bodies = []

    def handle(request):
        bodies.append((request.headers["idempotency-key"], request.content))
        return response(
            {
                "version": 1,
                "state": "completed",
                "result": {
                    "version": 1,
                    "operationId": "vote-restore",
                    "revisionId": revision_id,
                    "vote": "neutral",
                },
            }
        )

    restored_path = tmp_path / "restored-votes.db"
    with Session(import_database) as db:
        votes, _, revision_id = sender(db, tmp_path, handle)
        votes.prepare(revision_id, "neutral", "vote-restore")
        with (
            sqlite3.connect(import_database.url.database) as original,
            sqlite3.connect(restored_path) as restored,
        ):
            original.backup(restored)
        asyncio.run(votes.run(revision_id, "vote-restore"))
    engine = create_engine(f"sqlite:///{restored_path}")
    try:
        with Session(engine) as db:
            votes = VoteOutbox(outbox(db, tmp_path, handle).client)
            asyncio.run(votes.run(revision_id, "vote-restore"))
    finally:
        engine.dispose()
    assert bodies == [("vote-restore", b'{"version":1,"vote":"neutral"}')] * 2


@pytest.mark.parametrize(
    "fault", ["invalid_vote", "invalid_count", "private_history", "foreign_revision"]
)
def test_private_reads_fail_closed_on_malformed_remote_values(import_database, tmp_path, fault):
    def handle(request):
        own = request.url.path.endswith("/vote")
        value = {"version": 1, "revisionId": request.url.path.split("/")[-2]}
        value |= (
            {"vote": [], "eligible": True}
            if fault == "invalid_vote"
            else {"vote": "like", "eligible": True}
        )
        if not own:
            value = {
                "version": 1,
                "revisionId": request.url.path.split("/")[-2],
                "likes": True if fault == "invalid_count" else 1,
                "dislikes": 0,
            }
        if fault == "private_history":
            value["history"] = ["private"]
        if fault == "foreign_revision":
            value["revisionId"] = "someone-else"
        return response(value)

    with Session(import_database) as db:
        votes, _, revision_id = sender(db, tmp_path, handle)
        result = asyncio.run(votes.read(revision_id))
        assert result["current"] is None and result["totals"] is None
        assert result["error"]["code"] == "invalid_response"


def test_private_pending_intents_are_scoped_to_current_contributor(import_database, tmp_path):
    from dataclasses import replace

    def handle(request):
        own = request.url.path.endswith("/vote")
        return response(
            {"version": 1, "revisionId": revision_id}
            | ({"vote": "neutral", "eligible": False} if own else {"likes": 1, "dislikes": 0})
        )

    with Session(import_database) as db:
        votes, _, revision_id = sender(db, tmp_path, handle)
        votes.prepare(revision_id, "like", "vote-private")
        original_identity = votes.client._identity()
        votes.client._identity = lambda: replace(original_identity, contributor_id="second-reader")
        result = asyncio.run(votes.read(revision_id))
        assert result["scope"]["contributorId"] == "second-reader"
        assert result["pending"] is None and result["current"]["vote"] == "neutral"
        with pytest.raises(CommunityFailure, match="vote_intent_unavailable"):
            asyncio.run(votes.run(revision_id, "vote-private"))


def test_cancelled_send_keeps_original_submitted_intent(import_database, tmp_path):
    async def handle(request):
        raise asyncio.CancelledError()

    with Session(import_database) as db:
        votes, _, revision_id = sender(db, tmp_path, handle)
        votes.prepare(revision_id, "neutral", "vote-cancelled")
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(votes.run(revision_id, "vote-cancelled"))
        row = db.get(CommunityOperationRow, "vote-cancelled")
        assert row.state == "submitted"
        assert row.receipt == '{"version":1,"vote":"neutral"}'


@pytest.mark.parametrize("fault", ["self", "unacknowledged", "foreign", "corrupt_response"])
def test_vote_denials_preserve_intent_and_do_not_expose_unverified_state(
    import_database, tmp_path, fault
):
    from scanner.db import CommunityImportRow

    with Session(import_database) as db:
        votes, imported, revision_id = sender(
            db, tmp_path, lambda request: response({"version": True})
        )
        if fault == "self":
            db.get(CommunityImportRow, imported.import_id).author_id = "reader"
        elif fault == "unacknowledged":
            db.query(CommunityOperationRow).one().state = "pending"
        db.commit()
        if fault in {"self", "unacknowledged"}:
            with pytest.raises(CommunityFailure):
                votes.prepare(revision_id, "like", "vote-denied")
            assert db.get(CommunityOperationRow, "vote-denied") is None
            db.rollback()
        else:
            votes.prepare(revision_id, "like", "vote-denied")
            if fault == "foreign":
                db.get(CommunityOperationRow, "vote-denied").contributor_id = "someone-else"
                db.commit()
            with pytest.raises(CommunityFailure):
                asyncio.run(votes.run(revision_id, "vote-denied"))
            state = asyncio.run(votes.read(revision_id))
            assert state["current"] is None
            if fault == "foreign":
                assert state["pending"] is None
