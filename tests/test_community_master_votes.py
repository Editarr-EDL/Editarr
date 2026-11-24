"""Receipt-bound filter votes through the outbox, vote eligibility, and reports."""

import asyncio
import json

import pytest
from sqlalchemy import select

from scanner.db import (
    CommunityMasterFilterRow,
    CommunityMasterLinkRow,
    CommunityOperationRow,
    CommunityPublicationRow,
)
from scanner.formats.community.artifact_v3 import encode_artifact_v3
from scanner.formats.community.artifact_v3_models import ArtifactV3
from scanner.integrations.community.master_votes import MasterFeedback
from scanner.integrations.community.masters import MasterSets
from scanner.integrations.community.transport import CommunityFailure
from tests.community_master_service_support import (
    CONTRIBUTOR,
    FIXTURES,
    FakeService,
    connect,
    error,
    found_download,
    master_response,
    master_route,
    served_filter,
)
from tests.community_master_support import OWNER, SERVICE

ARTIFACT = json.loads((FIXTURES / "artifact-v3.json").read_text())["artifact"]


@pytest.fixture
def voting(tmp_path):
    service = FakeService()
    factory, client = connect(tmp_path, service)
    master_route(
        service,
        "v_1",
        {
            None: master_response(
                "v_1", 2, served_filter("f_1"), served_filter("f_9c1d", 50_000, 60_000)
            )
        },
    )
    link = found_download(MasterSets(client, OWNER), "v_1")

    def accept(request):
        body = json.loads(request.content)
        return {
            "version": 1,
            "state": "completed",
            "result": {
                "version": 1,
                "operationId": request.headers["idempotency-key"],
                "filterId": request.url.path.split("/")[3],
                "versionId": "v_1",
                "vote": body["vote"],
                "revision": 3,
            },
        }

    service.on("POST", "/v1/filters/f_1/votes", accept)
    yield service, factory, MasterFeedback(client, OWNER), link.id
    client.db.close()


def vote(feedback, link_id, filter_id="f_1", choice="up", operation="vote-1"):
    return asyncio.run(feedback.vote(link_id, filter_id, choice, operation))


def test_vote_sends_the_snapshot_receipt_and_records_my_vote(voting):
    service, factory, feedback, link_id = voting
    first = vote(feedback, link_id)
    assert first["result"]["vote"] == "up" and first["result"]["revision"] == 3
    assert asyncio.run(feedback.send_vote(link_id, "f_1", "vote-1")) == first
    (sent,) = service.sent("POST", "/v1/filters/f_1/votes")
    with factory() as db:
        operation = db.get(CommunityOperationRow, "vote-1")
        assert operation.state == "acknowledged" and operation.kind == "filter_vote"
        assert json.loads(sent.content) == {
            "version": 1,
            "vote": "up",
            "receiptId": operation.receipt_id,
        }
        assert sent.headers["idempotency-key"] == "vote-1"
        row = db.get(CommunityMasterFilterRow, (link_id, "f_1"))
        assert (row.my_vote, row.local_touched) == ("up", True)


def test_retryable_failures_keep_the_intent_and_retry_resends_saved_bytes(voting):
    service, factory, feedback, link_id = voting
    accept = service.routes[("POST", "/v1/filters/f_1/votes")]
    service.on("POST", "/v1/filters/f_1/votes", lambda _r: error(503, "service_unavailable"))
    with pytest.raises(CommunityFailure, match="service_unavailable"):
        vote(feedback, link_id)
    with factory() as db:
        assert db.get(CommunityOperationRow, "vote-1").state == "submitted"
    service.on("POST", "/v1/filters/f_1/votes", accept)
    asyncio.run(feedback.send_vote(link_id, "f_1", "vote-1"))
    bodies = {request.content for request in service.sent("POST", "/v1/filters/f_1/votes")}
    assert len(bodies) == 1
    with pytest.raises(CommunityFailure, match="operation_conflict"):
        vote(feedback, link_id, choice="down")


def test_service_refusals_mark_the_intent_failed(voting):
    service, factory, feedback, link_id = voting
    service.on(
        "POST",
        "/v1/filters/f_1/votes",
        lambda _r: error(403, "vote_ineligible", reason="self_vote"),
    )
    with pytest.raises(CommunityFailure) as raised:
        vote(feedback, link_id)
    assert raised.value.details == {"reason": "self_vote"}
    with factory() as db:
        assert db.get(CommunityOperationRow, "vote-1").state == "failed"
        assert db.get(CommunityMasterFilterRow, (link_id, "f_1")).my_vote is None


def test_a_transferred_link_refuses_votes_with_the_original_receipts(voting):
    service, factory, feedback, link_id = voting
    with factory() as db:
        db.get(CommunityMasterLinkRow, link_id).contributor_id = "c_original"
        db.commit()
    with pytest.raises(CommunityFailure, match="receipt_owner_mismatch"):
        vote(feedback, link_id)
    assert not service.sent("POST", "/v1/filters/f_1/votes")
    with factory() as db:
        assert db.scalars(select(CommunityOperationRow)).all() == []


def test_filters_outside_the_receipt_snapshot_are_ineligible(voting):
    _service, _factory, feedback, link_id = voting
    with pytest.raises(CommunityFailure) as raised:
        vote(feedback, link_id, filter_id="f_unknown")
    assert raised.value.details == {"reason": "receipt_required"}


def test_supporting_my_own_submission_is_a_self_vote(voting):
    service, factory, feedback, link_id = voting
    artifact = ArtifactV3.model_validate(ARTIFACT, strict=True, by_alias=True, by_name=False)
    artifact = artifact.model_copy(
        update={"author": artifact.author.model_copy(update={"contributor_id": CONTRIBUTOR})}
    )
    gzip_bytes = encode_artifact_v3(artifact).gzip_bytes
    with factory() as db:
        db.add(
            CommunityOperationRow(
                operation_id="publish-1",
                service_url=SERVICE,
                kind="submission",
                state="acknowledged",
                contributor_id=CONTRIBUTOR,
                updated_at="2026-11-01T00:00:00+00:00",
                owner_id=OWNER,
            )
        )
        db.flush()
        db.add(
            CommunityPublicationRow(
                operation_id="publish-1",
                artifact_bytes=gzip_bytes,
                manifest_bytes=b"{}",
                signature_bytes=b"{}",
                manifest_digest="0" * 64,
                signature_digest="0" * 64,
                preview_digest="0" * 64,
                consent_token="0" * 64,
                author_key_id=artifact.author.key_id,
                source_kind="scan",
                source_id="scan",
                result_json=json.dumps({"versionId": "v_1"}),
            )
        )
        db.commit()
    with pytest.raises(CommunityFailure) as raised:
        vote(feedback, link_id, filter_id="f_9c1d")
    assert raised.value.details == {"reason": "self_vote"}
    assert not service.sent("POST", "/v1/filters/f_9c1d/votes")


def test_reports_need_the_links_version_or_a_served_filter(voting):
    service, _factory, feedback, link_id = voting
    result = {"version": 1, "operationId": "report-1", "reportId": "r_1", "state": "open"}
    service.on(
        "POST", "/v1/reports", lambda _r: {"version": 1, "state": "completed", "result": result}
    )
    report = asyncio.run(
        feedback.report(
            link_id,
            target_type="filter",
            target_id="f_1",
            reason="inaccurate",
            note="Starts late",
            operation_id="report-1",
        )
    )
    assert report["result"]["reportId"] == "r_1"
    assert json.loads(service.sent("POST", "/v1/reports")[0].content) == {
        "version": 1,
        "target": {"type": "filter", "id": "f_1"},
        "reason": "inaccurate",
        "note": "Starts late",
    }
    with pytest.raises(CommunityFailure, match="report_ineligible"):
        asyncio.run(
            feedback.report(link_id, target_type="version", target_id="v_other", reason="spam")
        )
    with pytest.raises(CommunityFailure, match="invalid_report"):
        asyncio.run(feedback.report(link_id, target_type="filter", target_id="f_1", reason="x"))
