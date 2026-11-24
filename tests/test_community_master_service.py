"""Signed master-set service calls against a fake service: happy paths and contract errors."""

import asyncio
import base64
import json

import pytest

from scanner.integrations.community.master_service import MasterService, encode
from scanner.integrations.community.transport import CommunityFailure
from scanner.security.community_master_snapshots import MasterUnchanged, VerifiedMaster
from tests.community_master_service_support import (
    CONTRIBUTOR,
    MASTER_FIXTURE,
    FakeService,
    connect,
    error,
    master_response,
    master_route,
    served_filter,
)

MATCH = {
    "versionId": "v_12ab",
    "status": "confirmed",
    "aliasId": "theatrical",
    "label": "Theatrical",
    "labelText": None,
    "work": {
        "workId": "w_77",
        "kind": "movie",
        "tmdb": "603",
        "tvdb": None,
        "season": None,
        "episode": None,
        "lastEpisode": None,
        "title": "The Matrix",
        "year": 1999,
    },
    "tier": "2",
    "matchPercent": 80,
    "matchedKeys": ["md5_quick"],
    "runtimeAgreement": "not_applicable",
    "runtimeMs": 8160500,
    "trust": 0.41,
    "revision": 12,
    "filterCount": 34,
    "contributorCount": 5,
}
SUBMISSION = {
    "version": 1,
    "operationId": "submit-sub_1",
    "submissionId": "sub_1",
    "submissionDigest": "a" * 64,
    "workId": "w_77",
    "versionId": "v_12ab",
    "versionStatus": "confirmed",
    "disposition": "attached",
    "tier": "2",
    "revision": 13,
    "supersedes": None,
    "outcome": {
        "newFilters": 2,
        "supports": 31,
        "disputes": 1,
        "coverageNegatives": 0,
        "ignored": 3,
        "unassigned": 0,
    },
    "aliasSuggestion": None,
}


@pytest.fixture
def fake(tmp_path):
    service = FakeService()
    factory, client = connect(tmp_path, service)
    yield service, MasterService(client)
    client.db.close()


def run(coroutine):
    return asyncio.run(coroutine)


def failure(coroutine) -> CommunityFailure:
    with pytest.raises(CommunityFailure) as raised:
        asyncio.run(coroutine)
    return raised.value


def test_match_returns_results_and_maps_invalid_match(fake):
    service, master = fake
    service.on("POST", "/v1/match", lambda _r: {"version": 1, "results": [MATCH]})
    query = {"version": 1, "keys": [{"type": "tmdb", "kind": "movie", "id": "603"}], "limit": 20}
    (result,) = run(master.match(query))
    assert (result.version_id, result.tier, result.match_percent) == ("v_12ab", "2", 80)
    sent = service.sent("POST", "/v1/match")[0]
    assert json.loads(sent.content) == query and sent.headers["idempotency-key"]

    service.on("POST", "/v1/match", lambda _r: error(400, "invalid_match"))
    assert failure(master.match(query)).code == "invalid_match"
    service.on("POST", "/v1/match", lambda _r: {"version": 1, "results": [MATCH, MATCH]})
    assert failure(master.match(query)).code == "invalid_response"


@pytest.mark.parametrize(
    ("status", "code", "retryable"),
    [
        (401, "invalid_signature", False),
        (401, "clock_skew", False),
        (403, "membership_ineligible", False),
        (503, "service_unavailable", True),
        (429, "rate_limited", True),
    ],
)
def test_common_errors_keep_their_code(fake, status, code, retryable):
    service, master = fake
    service.on("GET", "/v1/aliases", lambda _r: error(status, code))
    raised = failure(master.aliases())
    assert (raised.code, raised.retryable) == (code, retryable)


def test_aliases_are_parsed_theatrical_first(fake):
    service, master = fake
    aliases = [
        {"aliasId": "theatrical", "label": "Theatrical", "spellings": ["", "theatrical"]},
        {"aliasId": "directors_cut", "label": "Director's Cut", "spellings": ["directors cut"]},
        {"aliasId": "extended", "label": "Extended Edition", "spellings": ["extended"]},
        {"aliasId": "unrated", "label": "Unrated", "spellings": ["unrated"]},
    ]
    service.on("GET", "/v1/aliases", lambda _r: {"version": 1, "aliases": aliases})
    assert [alias.alias_id for alias in run(master.aliases())][0] == "theatrical"
    service.on("GET", "/v1/aliases", lambda _r: {"version": 1, "aliases": aliases[1:]})
    assert failure(master.aliases()).code == "invalid_response"


def test_master_verifies_the_shared_fixture_against_pinned_receipt_keys(fake):
    service, master = fake
    request = MASTER_FIXTURE["request"]
    master_route(service, request["versionId"], {12: MASTER_FIXTURE["response"]})
    verified = run(master.master(request["versionId"], known_revision=12))
    assert isinstance(verified, VerifiedMaster)
    assert verified.receipt.receipt.receipt_id == MASTER_FIXTURE["receiptId"]
    assert service.sent("GET", "/v1/receipt-keys")
    assert service.sent("GET", "/v1/versions/v_12ab/master")[0].url.query == b"knownRevision=12"


def test_master_rejects_tampered_snapshots_and_foreign_receipts(fake):
    service, master = fake
    tampered = json.loads(json.dumps(MASTER_FIXTURE["response"]))
    raw = base64.b64decode(tampered["snapshot"]).replace(b'"high"', b'"low"', 1)
    tampered["snapshot"] = base64.b64encode(raw).decode()
    master_route(service, "v_12ab", {None: tampered})
    assert failure(master.master("v_12ab")).code == "snapshot_invalid"
    master_route(service, "v_1", {None: master_response("v_1", 1, contributor="c_other")})
    assert failure(master.master("v_1")).code == "snapshot_invalid"
    master_route(service, "v_1", {None: master_response("v_1", 1, served_filter("f_a"))})
    assert isinstance(run(master.master("v_1")), VerifiedMaster)


def test_master_unchanged_and_errors(fake):
    service, master = fake
    unchanged = {"version": 1, "changed": False, "versionId": "v_1", "revision": 3}
    master_route(service, "v_1", {3: unchanged})
    assert run(master.master("v_1", known_revision=3)) == MasterUnchanged("v_1", 3)
    for status, code, extra in (
        (404, "version_not_found", {}),
        (410, "version_unavailable", {}),
        (400, "invalid_master_request", {}),
        (409, "version_merged", {"mergedInto": "v_9"}),
    ):
        master_route(service, "v_1", {None: error(status, code, **extra)})
        raised = failure(master.master("v_1"))
        assert raised.code == code and raised.details.get("mergedInto") == extra.get("mergedInto")


def test_submit_sends_operation_key_and_validates_the_result(fake):
    service, master = fake
    service.on(
        "POST",
        "/v1/submissions",
        lambda _r: {"version": 1, "state": "completed", "result": SUBMISSION},
    )
    result = run(master.submit(b'{"version":1}', "sub_1"))
    assert result.disposition == "attached" and result.outcome.new_filters == 2
    assert service.sent("POST", "/v1/submissions")[0].headers["idempotency-key"] == "submit-sub_1"
    wrong = {**SUBMISSION, "submissionId": "sub_2"}
    service.on(
        "POST", "/v1/submissions", lambda _r: {"version": 1, "state": "completed", "result": wrong}
    )
    assert failure(master.submit(b"{}", "sub_1")).code == "invalid_response"


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (400, "invalid_submission"),
        (403, "upload_suspended"),
        (409, "submission_conflict"),
        (422, "work_identity_required"),
        (422, "unknown_alias"),
        (422, "invalid_taxonomy"),
        (422, "invalid_bounds"),
        (422, "key_mismatch"),
        (413, "body_too_large"),
    ],
)
def test_submit_contract_errors(fake, status, code):
    service, master = fake
    service.on("POST", "/v1/submissions", lambda _r: error(status, code))
    raised = failure(master.submit(b"{}", "sub_1"))
    assert raised.code == code and not raised.retryable


def test_vote_and_its_errors(fake):
    service, master = fake
    body = encode({"version": 1, "vote": "up", "receiptId": "a" * 64})
    result = {"version": 1, "operationId": "vote-1", "filterId": "f_1", "versionId": "v_1"}
    service.on(
        "POST",
        "/v1/filters/f_1/votes",
        lambda _r: {
            "version": 1,
            "state": "completed",
            "result": {**result, "vote": "up", "revision": 4},
        },
    )
    assert run(master.vote("f_1", body, "vote-1")).revision == 4
    for status, code, extra in (
        (403, "vote_ineligible", {"reason": "self_vote"}),
        (403, "vote_ineligible", {"reason": "receipt_required"}),
        (404, "filter_not_found", {}),
        (400, "invalid_vote", {}),
    ):
        service.on(
            "POST", "/v1/filters/f_1/votes", lambda _r, s=status, c=code, e=extra: error(s, c, **e)
        )
        raised = failure(master.vote("f_1", body, "vote-1"))
        assert raised.code == code and raised.details.get("reason") == extra.get("reason")


def test_sync_revisions_validates_changes_and_rate_limits(fake):
    service, master = fake
    changed = [
        {"versionId": "v_1", "status": "changed", "revision": 5},
        {"versionId": "v_2", "status": "merged", "revision": None, "mergedInto": "v_1"},
        {"versionId": "v_3", "status": "unavailable", "revision": None},
    ]
    service.on(
        "POST",
        "/v1/sync/revisions",
        lambda _r: {"version": 1, "changed": changed, "nextRunAllowedAt": 1},
    )
    items = [("v_1", 4), ("v_2", 1), ("v_3", 1)]
    response = run(master.sync_revisions("run-1", items))
    assert [change.status for change in response.changed] == ["changed", "merged", "unavailable"]
    sent = json.loads(service.sent("POST", "/v1/sync/revisions")[0].content)
    assert sent["runId"] == "run-1" and sent["items"][0] == {"versionId": "v_1", "revision": 4}
    assert failure(master.sync_revisions("run-1", [("v_1", 1), ("v_1", 2)])).code == "invalid_sync"
    service.on(
        "POST", "/v1/sync/revisions", lambda _r: error(429, "sync_rate_limited", retry_after=3600)
    )
    raised = failure(master.sync_revisions("run-2", items))
    assert raised.code == "sync_rate_limited" and raised.details["retryAfter"] == 3600


def test_report_and_its_errors(fake):
    service, master = fake
    result = {"version": 1, "operationId": "report-1", "reportId": "r_1", "state": "open"}
    service.on(
        "POST", "/v1/reports", lambda _r: {"version": 1, "state": "completed", "result": result}
    )
    document = {"version": 1, "target": {"type": "filter", "id": "f_1"}, "reason": "spam"}
    assert run(master.report(document, "report-1")).report_id == "r_1"
    for status, code in ((403, "report_ineligible"), (409, "report_already_open")):
        service.on("POST", "/v1/reports", lambda _r, s=status, c=code: error(s, c))
        assert failure(master.report(document, "report-1")).code == code


def test_requests_require_a_connected_contributor(fake):
    _service, master = fake
    assert master.identity().contributor_id == CONTRIBUTOR


def test_my_votes_and_malformed_sync_and_report_codes(fake):
    service, master = fake
    votes = {"version": 1, "versionId": "v_1", "votes": [{"filterId": "f_1", "vote": "up"}]}
    service.on("GET", "/v1/versions/v_1/votes", lambda _r: votes)
    assert [vote.vote for vote in run(master.my_votes("v_1")).votes] == ["up"]
    service.on(
        "GET", "/v1/versions/v_1/votes", lambda _r: error(409, "version_merged", mergedInto="v_2")
    )
    assert failure(master.my_votes("v_1")).details == {"mergedInto": "v_2"}
    service.on("POST", "/v1/sync/revisions", lambda _r: error(400, "invalid_sync"))
    assert failure(master.sync_revisions("run-1", [("v_1", 1)])).code == "invalid_sync"
    service.on("POST", "/v1/reports", lambda _r: error(400, "invalid_report"))
    document = {"version": 1, "target": {"type": "version", "id": "v_1"}, "reason": "spam"}
    assert failure(master.report(document, "report-1")).code == "invalid_report"
