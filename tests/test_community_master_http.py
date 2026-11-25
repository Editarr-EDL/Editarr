"""Find, download, view, local decisions, preferences and the EDL read path over HTTP."""

from pathlib import Path

from sqlalchemy import select

from scanner.db import CommunityMasterLinkRow
from scanner.security.access.capability_ids import Capability as C
from scanner.security.access.overrides import OverrideEffect
from tests.community_master_http_support import (
    API,
    VERSION_ID,
    activities,
    error,
    masters,
)
from tests.community_master_service_support import served_filter
from tests.multi_user_access_support import ADMIN, PEER_USER, USER

__all__ = ["activities", "masters"]


def test_routes_require_a_session_csrf_and_answer_no_store(masters):
    app = masters.app
    assert app.client.get(API + "/masters?item_id=x").status_code == 401
    for path in ("/find", "/masters", "/sync/run", "/masters/l/check"):
        response = app.client.post(API + path, json={})
        assert response.status_code == 401
    headers = {key: value for key, value in app.accounts.unsafe_cookie(USER).items()}
    headers.pop("X-CSRF-Token")
    app.client.cookies.clear()
    refused = app.client.post(API + "/sync/run", json={}, headers=headers)
    assert refused.json()["code"] == "csrf_failed"
    listed = masters.send("GET", f"/masters?item_id={masters.item_id}", USER)
    assert listed.status_code == 200 and listed.headers["cache-control"] == "no-store"
    assert listed.json() == {"version": 1, "links": []}


def test_find_download_list_and_view_for_a_file_tier(masters):
    masters.service.publish("v_1", 3, served_filter("f_a", 10_000, 20_000))
    found = masters.find(USER)
    assert found["localVersionId"] == VERSION_ID and found["error"] is None
    assert found["keysUsed"] == ["md5_quick", "tmdb", "title_year"]
    (result,) = found["results"]
    assert (result["tier"], result["alignmentRequired"], result["linkedLinkId"]) == (
        "2",
        False,
        None,
    )
    applied = masters.apply(USER, find_key=found["findKey"])
    assert applied.status_code == 200, applied.text
    link = applied.json()["link"]
    assert link["alignmentState"] == "not_required" and link["latestRevision"] == 3
    assert (link["linkState"], link["mergedInto"], link["label"]) == ("active", None, "Theatrical")
    assert masters.find(USER)["results"][0]["linkedLinkId"] == link["linkId"]
    listed = masters.ok("GET", f"/masters?item_id={masters.item_id}", USER)
    assert [item["linkId"] for item in listed["links"]] == [link["linkId"]]
    view = masters.ok("GET", f"/masters/{link['linkId']}", USER)
    assert view["preferences"] == {
        "confidenceThreshold": 0.0,
        "showLowConfidence": False,
        "hideRejectedDownvoted": False,
    }
    (row,) = view["filters"]
    assert row["active"] and row["myVote"] is None
    assert row["master"]["startMs"] == 10_000 and row["master"]["confidence"] == 0.5


def test_download_errors(masters):
    masters.service.publish("v_1", 1, served_filter("f_a"))
    masters.service.publish("v_2", 1, served_filter("f_b"))
    expired = masters.apply(USER, find_key="unknown")
    assert (expired.status_code, expired.json()) == (
        409,
        {"code": "find_expired", "retryable": False},
    )
    key = masters.find(USER)["findKey"]
    Path(masters.path).write_bytes(b"a different generation of the file")
    changed = masters.apply(USER, find_key=key)
    assert changed.json()["code"] == "target_changed"
    assert masters.apply(USER).status_code == 200
    unconfirmed = masters.send(
        "POST",
        "/masters",
        USER,
        {
            "target": masters.target,
            "find_key": masters.find(USER, "v_2")["findKey"],
            "community_version_id": "v_2",
            "confirmed": False,
        },
    )
    assert unconfirmed.json()["code"] == "confirmation_required"
    assert masters.apply(USER, "v_2").json() == {"code": "replace_required", "retryable": False}
    replaced = masters.apply(USER, "v_2", replace=True)
    assert replaced.json()["link"]["communityVersionId"] == "v_2"
    masters.service.gone["v_3"] = error(409, "version_merged", mergedInto="v_2")
    merged = masters.apply(USER, "v_3", replace=True)
    assert merged.status_code == 409
    assert merged.json() == {"code": "version_merged", "retryable": False, "mergedInto": "v_2"}
    masters.service.gone["v_4"] = error(503, "service_unavailable")
    unavailable = masters.apply(USER, "v_4", replace=True)
    assert unavailable.status_code == 503 and unavailable.json()["retryable"] is True
    extra = masters.send("POST", "/find", USER, {"target": masters.target, "labels": None})
    assert extra.status_code == 422


def test_links_are_per_account_and_respect_capabilities_and_media(masters):
    link = masters.link(USER)
    path = f"/masters/{link['linkId']}"
    assert masters.send("GET", path, PEER_USER).status_code == 404
    assert masters.send("GET", path, ADMIN).status_code == 404
    peer = masters.link(PEER_USER)
    assert peer["linkId"] != link["linkId"]
    assert (
        masters.ok("GET", f"/masters?item_id={masters.item_id}", USER)["links"][0]["linkId"]
        == link["linkId"]
    )
    voted = masters.send(
        "POST", f"{path}/filters/f_a/vote", PEER_USER, {"vote": "up", "operation_id": "vote-x"}
    )
    assert voted.status_code in (404, 422)
    masters.app.accounts.set_override(USER, C.COMMUNITY_SEARCH, OverrideEffect.DENY)
    denied = masters.send("GET", path, USER)
    assert (denied.status_code, denied.json()["code"]) == (403, "capability_denied")
    masters.app.accounts.set_override(USER, C.COMMUNITY_SEARCH, OverrideEffect.INHERIT)
    masters.hide_media(USER)
    assert masters.send("GET", path, USER).status_code == 404
    assert masters.send("GET", f"/masters?item_id={masters.item_id}", USER).status_code == 404


def test_preferences_are_per_account_and_decide_which_filters_start_enabled(masters):
    low = served_filter("f_low", 30_000, 31_000, confidence=0.2)
    link = masters.link(USER, "v_1", "2", served_filter("f_a"), low)
    assert masters.ok("GET", "/preferences", USER) == {
        "version": 1,
        "confidenceThreshold": 0.0,
        "showLowConfidence": False,
        "hideRejectedDownvoted": False,
    }
    saved = masters.ok("PATCH", "/preferences", USER, {"confidence_threshold": 0.3})
    assert saved["confidenceThreshold"] == 0.3
    assert masters.ok("GET", "/preferences", PEER_USER)["confidenceThreshold"] == 0.0
    view = masters.ok("GET", f"/masters/{link['linkId']}", USER)
    assert {row["filterId"]: row["active"] for row in view["filters"]} == {
        "f_a": True,
        "f_low": False,
    }
    assert (
        masters.send("PATCH", "/preferences", USER, {"confidence_threshold": 1.5}).status_code
        == 422
    )
    assert masters.send("PATCH", "/preferences", USER, {"threshold": 0.1}).status_code == 422


def test_active_filters_feed_the_edl_range_merging(masters):
    filters = (
        served_filter("f_a", 10_000, 20_000),
        served_filter("f_b", 15_000, 25_000),
        served_filter("f_c", 40_000, 41_000, action="mute"),
        served_filter("f_d", 60_000, 61_000, confidence=0.1),
    )
    link = masters.link(USER, "v_1", "2", *filters)
    masters.ok("PATCH", "/preferences", USER, {"confidence_threshold": 0.3})
    active = masters.ok("GET", f"/masters/{link['linkId']}/active-filters", USER)
    assert [item["filterId"] for item in active["filters"]] == ["f_a", "f_b", "f_c"]
    assert active["localVersionId"] == VERSION_ID
    lines = [line for line in active["edl"].splitlines() if not line.startswith("#")]
    assert lines == ["10.000 25.000 3", "40.000 41.000 1"]
    assert (
        masters.send("GET", f"/masters/{link['linkId']}/active-filters", PEER_USER).status_code
        == 404
    )


def test_replaced_links_stay_as_history(masters):
    masters.link(USER)
    masters.service.publish("v_2", 1, served_filter("f_b"))
    assert masters.apply(USER, "v_2", replace=True).status_code == 200
    with masters.app.accounts.factory() as db:
        states = sorted(db.scalars(select(CommunityMasterLinkRow.link_state)))
    assert states == ["active", "replaced"]


def test_aliases_list_theatrical_first_and_other_last(masters):
    choices = masters.ok("GET", "/aliases", USER)["choices"]
    assert choices[0] == {"kind": "alias", "aliasId": "theatrical", "label": "Theatrical"}
    assert choices[-1] == {"kind": "other", "label": "Other"}
    masters.app.accounts.set_override(USER, C.COMMUNITY_SEARCH, OverrideEffect.DENY)
    assert masters.send("GET", "/aliases", USER).status_code == 403


def test_find_keys_belong_to_one_account_and_media_access_applies(masters):
    masters.service.publish("v_1", 1, served_filter("f_a"))
    key = masters.find(USER)["findKey"]
    foreign = masters.apply(PEER_USER, find_key=key)
    assert foreign.json() == {"code": "find_expired", "retryable": False}
    masters.app.accounts.set_override(PEER_USER, C.COMMUNITY_DOWNLOAD, OverrideEffect.DENY)
    assert masters.apply(PEER_USER, find_key=key).status_code == 403
    masters.hide_media(USER)
    hidden = masters.send("POST", "/find", USER, {"target": masters.target, "label": None})
    assert hidden.status_code == 404


def test_download_capability_is_checked_before_confirmation(masters):
    masters.deny(USER, C.COMMUNITY_DOWNLOAD)
    body = {
        "target": masters.target,
        "find_key": "unknown",
        "community_version_id": "v_1",
        "confirmed": False,
    }
    refused = masters.send("POST", "/masters", USER, body)
    assert (refused.status_code, refused.json()["code"]) == (403, "capability_denied")


def test_patch_routes_refuse_requests_without_csrf(masters):
    link = masters.link(USER)
    headers = dict(masters.app.accounts.unsafe_cookie(USER))
    headers.pop("X-CSRF-Token")
    masters.app.client.cookies.clear()
    refused = masters.app.client.patch(
        API + "/preferences", json={"show_low_confidence": True}, headers=headers
    )
    assert refused.json()["code"] == "csrf_failed"
    assert masters.ok("GET", f"/masters/{link['linkId']}", USER)["filters"]
    assert masters.ok("GET", "/preferences", USER)["showLowConfidence"] is False


def test_path_identifiers_and_preference_numbers_are_strict(masters):
    link = masters.link(USER)
    assert masters.send("GET", "/masters/bad%20id", USER).status_code == 422
    assert masters.send("GET", "/masters/" + "a" * 129, USER).status_code == 422
    bad_filter = f"/masters/{link['linkId']}/filters/f%20a/vote"
    body = {"vote": "up", "operation_id": "vote-1", "confirmed": True}
    assert masters.send("POST", bad_filter, USER, body).status_code == 422
    for value in ("0.5", True):
        body = {"confidence_threshold": value}
        assert masters.send("PATCH", "/preferences", USER, body).status_code == 422
    assert (
        masters.ok("PATCH", "/preferences", USER, {"confidence_threshold": 1})[
            "confidenceThreshold"
        ]
        == 1.0
    )


def test_active_filters_are_clipped_to_the_local_file(masters):
    masters.set_duration(100)
    filters = (
        served_filter("f_a", 10_000, 20_000),
        served_filter("f_b", 95_000, 105_000),
        served_filter("f_c", 120_000, 130_000),
    )
    link = masters.link(USER, "v_1", "2", *filters)
    active = masters.ok("GET", f"/masters/{link['linkId']}/active-filters", USER)
    assert [(item["filterId"], item["startMs"], item["endMs"]) for item in active["filters"]] == [
        ("f_a", 10_000, 20_000),
        ("f_b", 95_000, 100_000),
    ]
    lines = [line for line in active["edl"].splitlines() if not line.startswith("#")]
    assert lines == ["10.000 20.000 3", "95.000 100.000 3"]
    unclipped = masters.ok("GET", f"/masters/{link['linkId']}/active-filters?clipped=false", USER)
    assert [(item["filterId"], item["endMs"]) for item in unclipped["filters"]] == [
        ("f_a", 20_000),
        ("f_b", 105_000),
        ("f_c", 130_000),
    ]


def test_repeated_find_and_check_wait_for_the_cooldown(masters, monkeypatch):
    from scanner.http.community_master_cooldown import cooldowns

    link = masters.link(USER)
    monkeypatch.setattr(cooldowns, "seconds", 10)
    masters.find(USER)
    again = masters.send("POST", "/find", USER, {"target": masters.target, "label": None})
    assert again.status_code == 429 and again.headers["retry-after"] == "10"
    assert again.json() == {"code": "rate_limited", "retryable": True, "retryAfter": 10}
    other_label = {"target": masters.target, "label": {"alias_id": "extended"}}
    assert masters.send("POST", "/find", USER, other_label).status_code == 200
    assert masters.find(PEER_USER)["results"]
    check = f"/masters/{link['linkId']}/check"
    assert masters.send("POST", check, USER, {}).status_code == 200
    limited = masters.send("POST", check, USER, {})
    assert limited.status_code == 429 and limited.json()["code"] == "rate_limited"
