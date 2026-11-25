"""Update checks, update review, daily sync, votes and reports over HTTP (local API §6, §8, §9)."""

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
    masters.ok(
        "PATCH",
        f"/masters/{link['linkId']}/filters/f_b",
        USER,
        {"decision": "adjust", "start_ms": 30_500 + (500 if tier != "2" else 0)},
    )
    masters.service.publish("v_1", 2, *REVISION_2)
    return link["linkId"]


def groups(update):
    return {name: [row["filterId"] for row in rows] for name, rows in update["groups"].items()}


def test_check_stores_a_newer_revision_for_review_only(masters):
    link_id = updated_link(masters)
    checked = masters.ok("POST", f"/masters/{link_id}/check", USER, {})
    assert checked == {
        "version": 1,
        "changed": True,
        "latestRevision": 2,
        "pendingUpdate": {"new": 1, "changed": 2, "removed": 1, "nowLowConfidence": 1},
    }
    assert masters.ok("POST", f"/masters/{link_id}/check", USER, {})["changed"] is False
    view = masters.ok("GET", f"/masters/{link_id}", USER)
    assert view["link"]["appliedRevision"] == 1 and view["link"]["latestRevision"] == 2
    assert {row["filterId"]: row["effective"]["startMs"] for row in view["filters"]}[
        "f_a"
    ] == 10_000
    assert masters.send("POST", f"/masters/{link_id}/check", PEER_USER, {}).status_code == 404


def test_update_review_decisions_and_accept_all(masters):
    link_id = updated_link(masters)
    masters.ok("POST", f"/masters/{link_id}/check", USER, {})
    update = masters.ok("GET", f"/masters/{link_id}/update", USER)
    assert (update["fromRevision"], update["toRevision"], update["alignmentRequired"]) == (
        1,
        2,
        False,
    )
    assert groups(update) == {
        "new": ["f_e"],
        "changed": ["f_a", "f_b"],
        "removed": ["f_c"],
        "nowLowConfidence": ["f_d"],
    }
    mine = next(row for row in update["groups"]["changed"] if row["filterId"] == "f_b")
    assert mine["touched"] is True and mine["allowed"] == ["keep_mine", "accept", "dismiss"]
    assert mine["mine"]["startMs"] == 30_500 and mine["newMaster"]["severity"] == "low"
    assert mine["oldMaster"]["severity"] == "high" and mine["changedFields"] == ["severity"]
    decisions = f"/masters/{link_id}/update/decisions"
    not_allowed = masters.send(
        "POST",
        decisions,
        USER,
        {"to_revision": 2, "decisions": [{"row_id": "f_a", "decision": "keep_mine"}]},
    )
    assert not_allowed.json() == {"code": "decision_not_allowed", "retryable": False}
    stale = masters.send(
        "POST",
        decisions,
        USER,
        {"to_revision": 1, "decisions": [{"row_id": "f_a", "decision": "accept"}]},
    )
    assert stale.json() == {"code": "update_changed", "retryable": False}
    many = [{"row_id": f"f_{index}", "decision": "accept"} for index in range(501)]
    assert (
        masters.send("POST", decisions, USER, {"to_revision": 2, "decisions": many}).status_code
        == 422
    )
    remaining = masters.ok(
        "POST",
        f"/masters/{link_id}/update/accept-all",
        USER,
        {"to_revision": 2, "group": "changed"},
    )
    assert groups(remaining)["changed"] == ["f_b"]
    assert (
        masters.send(
            "POST",
            f"/masters/{link_id}/update/accept-all",
            PEER_USER,
            {"to_revision": 2, "group": "new"},
        ).status_code
        == 404
    )
    done = masters.send(
        "POST",
        decisions,
        USER,
        {
            "to_revision": 2,
            "decisions": [
                {"row_id": "f_b", "decision": "keep_mine"},
                {"row_id": "f_c", "decision": "accept"},
                {"row_id": "f_d", "decision": "accept"},
                {"row_id": "f_e", "decision": "dismiss"},
            ],
        },
    )
    assert done.status_code == 204 and done.content == b""
    assert not any(masters.ok("GET", f"/masters/{link_id}/update", USER)["groups"].values())
    view = masters.ok("GET", f"/masters/{link_id}", USER)
    assert view["link"]["appliedRevision"] == 2
    rows = {row["filterId"]: row for row in view["filters"]}
    assert rows["f_a"]["effective"]["startMs"] == 12_000
    assert (
        rows["f_b"]["effective"]["startMs"] == 30_500 and rows["f_b"]["master"]["severity"] == "low"
    )
    assert rows["f_d"]["state"] == "low_confidence" and not rows["f_d"]["active"]
    assert "f_c" not in rows and "f_e" not in rows


def test_fuzzy_updates_need_alignment_for_their_revision(masters):
    link_id = updated_link(masters, tier="4")
    masters.ok("POST", f"/masters/{link_id}/check", USER, {})
    update = masters.ok("GET", f"/masters/{link_id}/update", USER)
    assert (update["alignmentRequired"], update["alignmentApproved"]) == (True, False)
    accept = {
        row["filterId"]: row["canAccept"] for rows in update["groups"].values() for row in rows
    }
    assert accept == {"f_a": False, "f_b": False, "f_c": True, "f_d": False, "f_e": False}
    refused = masters.send(
        "POST",
        f"/masters/{link_id}/update/decisions",
        USER,
        {"to_revision": 2, "decisions": [{"row_id": "f_a", "decision": "accept"}]},
    )
    assert refused.json()["code"] == "decision_not_allowed"
    unchanged = masters.ok(
        "POST", f"/masters/{link_id}/update/accept-all", USER, {"to_revision": 2, "group": "new"}
    )
    assert groups(unchanged)["new"] == ["f_e"]
    path = f"/masters/{link_id}/alignment"
    token = masters.ok("POST", path + "/preview", USER, {"offset_ms": 500})["token"]
    masters.ok(
        "POST", path + "/approve", USER, {"token": token, "for_revision": 2, "confirmed": True}
    )
    update = masters.ok("GET", f"/masters/{link_id}/update", USER)
    assert update["alignmentApproved"] is True
    assert update["groups"]["changed"][0]["newMaster"]["startMs"] == 12_500
    accepted = masters.ok(
        "POST", f"/masters/{link_id}/update/accept-all", USER, {"to_revision": 2, "group": "new"}
    )
    assert groups(accepted)["new"] == []


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
    assert (row["myVote"], row["touched"]) == ("up", True)
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
