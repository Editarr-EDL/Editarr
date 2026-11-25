"""Update checks, daily sync, votes and reports over HTTP (local API §6, §8)."""

from scanner.security.access.capability_ids import Capability as C
from scanner.security.access.overrides import OverrideEffect
from tests.community_master_http_support import activities, error, masters
from tests.community_master_service_support import served_filter
from tests.multi_user_access_support import PEER_USER, USER

__all__ = ["activities", "masters"]

REVISION_1 = (
    served_filter("f_a", 10_000, 20_000),
    served_filter("f_b", 30_000, 31_000),
    served_filter("f_c", 50_000, 51_000),
    served_filter("f_d", 70_000, 71_000),
)
REVISION_2 = (
    served_filter("f_a", 12_000, 20_000),
    served_filter("f_b", 30_000, 31_000, severity="low"),
    served_filter("f_d", 70_000, 71_000, lowConfidence=True),
    served_filter("f_e", 90_000, 91_000),
)


def updated_link(masters, tier="2"):
    link = masters.link(USER, "v_1", tier, *REVISION_1)
    if tier != "2":
        path = f"/masters/{link['linkId']}/alignment"
        token = masters.ok("POST", path + "/preview", USER, {"offset_ms": 500})["token"]
        masters.ok(
            "POST", path + "/approve", USER, {"token": token, "for_revision": 1, "confirmed": True}
        )
    masters.service.publish("v_1", 2, *REVISION_2)
    return link["linkId"]


def test_check_downloads_and_applies_a_newer_revision(masters):
    link_id = updated_link(masters)
    checked = masters.ok("POST", f"/masters/{link_id}/check", USER, {})
    assert checked == {"version": 1, "changed": True, "latestRevision": 2}
    assert masters.ok("POST", f"/masters/{link_id}/check", USER, {})["changed"] is False
    view = masters.ok("GET", f"/masters/{link_id}", USER)
    assert view["link"]["appliedRevision"] == 2 and view["link"]["latestRevision"] == 2
    assert "pendingUpdate" not in view["link"]
    rows = {row["filterId"]: row for row in view["filters"]}
    assert sorted(rows) == ["f_a", "f_b", "f_d", "f_e"]
    assert rows["f_a"]["master"]["startMs"] == 12_000
    assert set(rows["f_a"]) == {
        "filterId",
        "mergedIds",
        "category",
        "master",
        "locallyVerified",
        "myVote",
        "active",
    }
    assert not rows["f_d"]["active"]
    assert masters.send("POST", f"/masters/{link_id}/check", PEER_USER, {}).status_code == 404


def test_the_view_lists_the_earlier_ids_a_merged_filter_continues(masters):
    link = masters.link(USER, "v_1", "2", *REVISION_1)
    masters.service.publish(
        "v_1",
        2,
        served_filter("f_a", 10_000, 20_000),
        served_filter("f_m", 30_000, 31_000, mergedIds=["f_b", "f_c"]),
    )
    masters.ok("POST", f"/masters/{link['linkId']}/check", USER, {})
    view = masters.ok("GET", f"/masters/{link['linkId']}", USER)
    merged = {row["filterId"]: row["mergedIds"] for row in view["filters"]}
    assert merged == {"f_a": [], "f_m": ["f_b", "f_c"]}


def test_the_view_keeps_votes_on_filters_no_longer_served(masters):
    link_id = masters.link(USER, "v_1", "2", *REVISION_1)["linkId"]
    for filter_id, vote in (("f_a", "up"), ("f_c", "down")):
        body = {"vote": vote, "operation_id": f"vote-{filter_id}", "confirmed": True}
        voted = masters.send("POST", f"/masters/{link_id}/filters/{filter_id}/vote", USER, body)
        assert voted.status_code == 200, voted.text
    assert masters.ok("GET", f"/masters/{link_id}", USER)["retiredVotes"] == {}
    masters.service.publish("v_1", 2, *REVISION_2)
    masters.ok("POST", f"/masters/{link_id}/check", USER, {})
    view = masters.ok("GET", f"/masters/{link_id}", USER)
    assert view["retiredVotes"] == {"f_c": "down"}
    assert {row["filterId"]: row["myVote"] for row in view["filters"]}["f_a"] == "up"


def test_a_fuzzy_link_applies_new_revisions_through_its_alignment(masters):
    link_id = updated_link(masters, tier="4")
    masters.ok("POST", f"/masters/{link_id}/check", USER, {})
    view = masters.ok("GET", f"/masters/{link_id}", USER)
    assert {row["filterId"]: row["master"]["startMs"] for row in view["filters"]}["f_a"] == 12_500


def test_decision_and_update_review_routes_are_gone(masters):
    link_id = updated_link(masters)
    base = f"/masters/{link_id}"
    for method, path, body in (
        ("PATCH", base + "/filters/f_a", {"decision": "verify"}),
        ("GET", base + "/update", None),
        ("POST", base + "/update/decisions", {"to_revision": 2, "decisions": []}),
        ("POST", base + "/update/accept-all", {"to_revision": 2, "group": "new"}),
    ):
        assert masters.send(method, path, USER, body).status_code in (404, 405), path


def test_daily_sync_records_located_notifications_per_account(masters):
    link = masters.link(USER, "v_1", "2", *REVISION_1)
    peer = masters.link(PEER_USER, "v_1", "2", *REVISION_1)
    masters.service.publish("v_1", 2, *REVISION_2)
    masters.service.sync_changes = [{"versionId": "v_1", "status": "changed", "revision": 2}]
    ran = masters.ok("POST", "/sync/run", USER, {})
    assert (ran["ran"], ran["state"], ran["lastError"], ran["unreadCount"]) == (
        True,
        "idle",
        None,
        1,
    )
    assert ran["nextDueAt"] - ran["lastRunAt"] == 86_400
    (notice,) = ran["notifications"]
    assert notice["linkId"] == link["linkId"] and notice["kind"] == "changed"
    assert (notice["libraryId"], notice["itemId"], notice["mediaSourceId"]) == (
        masters.library_id,
        masters.item_id,
        None,
    )
    assert (notice["fromRevision"], notice["toRevision"], notice["read"]) == (1, 2, False)
    assert notice["title"] == "masters"
    assert masters.ok("POST", "/sync/run", USER, {})["ran"] is False
    assert masters.ok("GET", "/sync", USER)["unreadCount"] == 1
    other = masters.ok("GET", "/sync", PEER_USER)
    assert (other["state"], other["notifications"]) == ("idle", [])
    assert masters.ok("GET", f"/masters/{peer['linkId']}", PEER_USER)["link"]["latestRevision"] == 1
    read = masters.send(
        "POST",
        "/sync/notifications/read",
        PEER_USER,
        {"notification_ids": [notice["notificationId"]]},
    )
    assert read.status_code == 204
    assert masters.ok("GET", "/sync", USER)["unreadCount"] == 1
    for body in ({}, {"all": True, "notification_ids": []}, {"notification_ids": ["x"] * 101}):
        assert masters.send("POST", "/sync/notifications/read", USER, body).status_code == 422
    assert (
        masters.send(
            "POST",
            "/sync/notifications/read",
            USER,
            {"notification_ids": [notice["notificationId"]]},
        ).status_code
        == 204
    )
    assert masters.ok("GET", "/sync", USER)["notifications"][0]["read"] is True
    masters.ok("POST", "/sync/notifications/read", USER, {"all": True}, status=204)
    masters.hide_media(USER)
    hidden = masters.ok("GET", "/sync", USER)
    assert hidden["notifications"] == [] and hidden["unreadCount"] == 0


def test_sync_marks_merged_links_and_the_view_reports_them(masters):
    link = masters.link(USER, "v_1", "2", *REVISION_1)
    masters.service.sync_changes = [
        {"versionId": "v_1", "status": "merged", "revision": None, "mergedInto": "v_9"}
    ]
    ran = masters.ok("POST", "/sync/run", USER, {})
    assert [item["kind"] for item in ran["notifications"]] == ["merged"]
    view = masters.ok("GET", f"/masters/{link['linkId']}", USER)
    assert (view["link"]["linkState"], view["link"]["mergedInto"]) == ("merged", "v_9")
    masters.service.gone["v_1"] = error(409, "version_merged", mergedInto="v_9")
    checked = masters.send("POST", f"/masters/{link['linkId']}/check", USER, {})
    assert checked.json() == {"code": "version_merged", "retryable": False, "mergedInto": "v_9"}


def test_votes_are_receipt_bound_and_retry_saved_bytes(masters):
    link = masters.link(USER, "v_1", "2", *REVISION_1)
    path = f"/masters/{link['linkId']}/filters"
    voted = masters.ok(
        "POST",
        f"{path}/f_a/vote",
        USER,
        {"vote": "up", "operation_id": "vote-1", "confirmed": True},
    )
    assert voted["state"] == "completed" and voted["result"]["vote"] == "up"
    assert masters.service.votes[0]["vote"] == "up" and "receiptId" in masters.service.votes[0]
    row = masters.ok("GET", f"/masters/{link['linkId']}", USER)["filters"][0]
    assert row["myVote"] == "up"
    ineligible = masters.send(
        "POST",
        f"{path}/f_zz/vote",
        USER,
        {"vote": "up", "operation_id": "vote-2", "confirmed": True},
    )
    assert ineligible.json() == {
        "code": "vote_ineligible",
        "retryable": False,
        "reason": "receipt_required",
    }
    # The transport retries twice before reporting a retryable failure.
    masters.service.vote_failures.extend([error(503, "service_unavailable")] * 3)
    failed = masters.send(
        "POST",
        f"{path}/f_b/vote",
        USER,
        {"vote": "down", "operation_id": "vote-3", "confirmed": True},
    )
    assert failed.status_code == 503 and failed.json()["retryable"] is True
    retried = masters.ok(
        "POST", f"{path}/f_b/vote/retry", USER, {"operation_id": "vote-3", "confirmed": True}
    )
    assert retried["result"]["vote"] == "down"
    unconfirmed = masters.send(
        "POST",
        f"{path}/f_c/vote",
        USER,
        {"vote": "up", "operation_id": "vote-4", "confirmed": False},
    )
    assert unconfirmed.json()["code"] == "confirmation_required"
    assert (
        masters.send(
            "POST",
            f"{path}/f_a/vote",
            PEER_USER,
            {"vote": "up", "operation_id": "vote-5", "confirmed": True},
        ).status_code
        == 404
    )
    assert (
        masters.send(
            "POST",
            f"{path}/f_a/vote",
            USER,
            {"vote": "sideways", "operation_id": "vote-6", "confirmed": True},
        ).status_code
        == 422
    )
    masters.app.accounts.set_override(USER, C.COMMUNITY_FEEDBACK_SUBMIT, OverrideEffect.DENY)
    denied = masters.send(
        "POST",
        f"{path}/f_a/vote",
        USER,
        {"vote": "none", "operation_id": "vote-7", "confirmed": True},
    )
    assert (denied.status_code, denied.json()["code"]) == (403, "capability_denied")


def test_reports_proxy_to_the_service(masters):
    link = masters.link(USER, "v_1", "2", *REVISION_1)
    path = f"/masters/{link['linkId']}/reports"
    body = {
        "target": {"type": "filter", "id": "f_a"},
        "reason": "inaccurate",
        "note": " ",
        "confirmed": True,
    }
    reported = masters.ok("POST", path, USER, body)
    assert reported["result"]["reportId"] == "r_1"
    assert masters.service.reports == [
        {"version": 1, "target": {"type": "filter", "id": "f_a"}, "reason": "inaccurate"}
    ]
    wrong = masters.send("POST", path, USER, {**body, "target": {"type": "version", "id": "v_2"}})
    assert wrong.json() == {"code": "report_ineligible", "retryable": False}
    assert masters.send("POST", path, USER, {**body, "reason": "boring"}).status_code == 422
    assert masters.send("POST", path, PEER_USER, body).status_code == 404


def test_download_routes_refuse_without_the_capability(masters):
    link = masters.link(USER, "v_1", "2", *REVISION_1)
    base = f"/masters/{link['linkId']}"
    masters.deny(USER, C.COMMUNITY_DOWNLOAD)
    for method, path, body in (
        ("POST", base + "/check", {}),
        ("POST", base + "/alignment/preview", {"offset_ms": 0}),
        ("PATCH", "/preferences", {"show_low_confidence": True}),
        ("GET", "/sync", None),
        ("POST", "/sync/run", {}),
        ("POST", "/sync/notifications/read", {"all": True}),
    ):
        refused = masters.send(method, path, USER, body)
        assert (refused.status_code, refused.json()["code"]) == (403, "capability_denied"), path


def test_feedback_capability_is_checked_before_confirmation(masters):
    link = masters.link(USER, "v_1", "2", *REVISION_1)
    path = f"/masters/{link['linkId']}"
    masters.deny(USER, C.COMMUNITY_FEEDBACK_SUBMIT)
    for route, body in (
        ("/filters/f_a/vote", {"vote": "up", "operation_id": "vote-1", "confirmed": False}),
        ("/filters/f_a/vote/retry", {"operation_id": "vote-1", "confirmed": False}),
        (
            "/reports",
            {"target": {"type": "version", "id": "v_1"}, "reason": "spam", "confirmed": False},
        ),
    ):
        refused = masters.send("POST", path + route, USER, body)
        assert (refused.status_code, refused.json()["code"]) == (403, "capability_denied"), route
    assert masters.service.votes == [] and masters.service.reports == []


def test_sync_skips_links_the_account_can_no_longer_reach(masters):
    masters.link(USER, "v_1", "2", *REVISION_1)
    masters.link(PEER_USER, "v_1", "2", *REVISION_1)
    masters.service.sync_changes = [{"versionId": "v_1", "status": "changed", "revision": 2}]
    masters.service.publish("v_1", 2, *REVISION_2)
    masters.hide_media(USER)
    ran = masters.ok("POST", "/sync/run", USER, {})
    assert ran["ran"] is True and ran["notifications"] == []
    assert masters.service.synced == []
    assert masters.ok("POST", "/sync/run", PEER_USER, {})["unreadCount"] == 1
    assert masters.service.synced == [["v_1"]]
