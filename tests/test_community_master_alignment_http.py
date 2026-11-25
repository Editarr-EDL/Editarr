"""Alignment preview and approval for fuzzy master sets over HTTP (local API §4)."""

from tests.community_master_http_support import activities, masters
from tests.community_master_service_support import served_filter
from tests.multi_user_access_support import PEER_USER, USER

__all__ = ["activities", "masters"]

FILTERS = (
    served_filter("f_a", 10_000, 20_000),
    served_filter("f_b", 400_000, 401_000),
    served_filter("f_c", 800_000, 801_000),
)


def drifted(master_ms: int) -> int:
    return round(master_ms * 1.001) + 1000


def anchors(*filter_ids):
    starts = {item["filterId"]: item["startMs"] for item in FILTERS}
    return [{"filter_id": name, "local_start_ms": drifted(starts[name])} for name in filter_ids]


def preview(masters, link_id, body, user=USER):
    return masters.send("POST", f"/masters/{link_id}/alignment/preview", user, body)


def approve(masters, link_id, token, revision=1, user=USER, confirmed=True):
    body = {"token": token, "for_revision": revision, "confirmed": confirmed}
    return masters.send("POST", f"/masters/{link_id}/alignment/approve", user, body)


def test_fuzzy_rows_stay_inactive_until_an_approved_alignment(masters):
    link = masters.link(USER, "v_1", "4", *FILTERS)
    assert link["alignmentState"] == "required" and link["alignment"] is None
    view = masters.ok("GET", f"/masters/{link['linkId']}", USER)
    assert not any(row["active"] for row in view["filters"])
    shown = preview(masters, link["linkId"], {"anchors": anchors("f_a", "f_b", "f_c")})
    assert shown.status_code == 200, shown.text
    body = shown.json()
    assert body["alignment"] == {"offsetMs": 1000, "scale": 1.001, "anchors": 3}
    assert [row["filterId"] for row in body["rows"]] == ["f_a", "f_b", "f_c"]
    assert body["rows"][1] == {
        "filterId": "f_b",
        "alignedStartMs": drifted(400_000),
        "alignedEndMs": drifted(401_000),
    }
    assert masters.ok("GET", f"/masters/{link['linkId']}", USER)["link"]["alignment"] is None
    approved = approve(masters, link["linkId"], body["token"])
    assert approved.status_code == 200, approved.text
    stored = approved.json()["link"]
    assert stored["alignmentState"] == "approved" and stored["alignment"] == body["alignment"]
    view = masters.ok("GET", f"/masters/{link['linkId']}", USER)
    assert all(row["active"] for row in view["filters"])
    assert view["filters"][0]["master"]["startMs"] == drifted(10_000)
    reused = approve(masters, link["linkId"], body["token"])
    assert reused.json() == {"code": "alignment_preview_changed", "retryable": False}


def test_few_or_close_anchors_fit_only_an_offset(masters):
    link = masters.link(USER, "v_1", "4", *FILTERS)
    two = preview(masters, link["linkId"], {"anchors": anchors("f_a", "f_c")}).json()
    assert two["alignment"]["scale"] == 1.0 and two["alignment"]["anchors"] == 2
    offset = preview(
        masters, link["linkId"], {"anchors": None, "offset_ms": -500, "audio_track_index": 1}
    ).json()
    assert offset["alignment"] == {"offsetMs": -500, "scale": 1.0, "anchors": 0}
    assert offset["rows"][0]["alignedStartMs"] == 9_500


def test_alignment_requests_are_validated(masters):
    link = masters.link(USER, "v_1", "4", *FILTERS)
    for body in (
        {"anchors": anchors("f_a"), "offset_ms": 10},
        {"anchors": None, "offset_ms": None},
        {"anchors": []},
        {"anchors": [{"filter_id": "f_missing", "local_start_ms": 1}]},
        {"anchors": [{"filter_id": "f_a", "local_start_ms": 1}] * 51},
    ):
        assert preview(masters, link["linkId"], body).status_code == 422, body
    wild = [
        {"filter_id": "f_a", "local_start_ms": 0},
        {"filter_id": "f_b", "local_start_ms": 800_000},
        {"filter_id": "f_c", "local_start_ms": 1_600_000},
    ]
    refused = preview(masters, link["linkId"], {"anchors": wild})
    assert refused.json() == {"code": "alignment_out_of_range", "retryable": False}


def test_approval_needs_the_current_preview_confirmation_and_owner(masters):
    link = masters.link(USER, "v_1", "4", *FILTERS)
    token = preview(masters, link["linkId"], {"offset_ms": 0}).json()["token"]
    assert preview(masters, link["linkId"], {"offset_ms": 0}, PEER_USER).status_code == 404
    assert approve(masters, link["linkId"], token, user=PEER_USER).status_code == 404
    unconfirmed = approve(masters, link["linkId"], token, confirmed=False)
    assert unconfirmed.json()["code"] == "confirmation_required"
    stale = approve(masters, link["linkId"], token, revision=2)
    assert stale.json()["code"] == "alignment_preview_changed"
    token = preview(masters, link["linkId"], {"offset_ms": 0}).json()["token"]
    masters.service.publish("v_1", 2, *FILTERS, served_filter("f_new", 900_000, 901_000))
    assert masters.ok("POST", f"/masters/{link['linkId']}/check", USER, {})["changed"] is True
    changed = approve(masters, link["linkId"], token, revision=1)
    assert changed.json()["code"] == "alignment_preview_changed"
    assert approve(masters, link["linkId"], "unknown").json()["code"] == (
        "alignment_preview_changed"
    )


def test_file_tier_links_need_no_alignment(masters):
    link = masters.link(USER, "v_1", "2", *FILTERS)
    refused = preview(masters, link["linkId"], {"offset_ms": 0})
    assert refused.json() == {"code": "alignment_not_required", "retryable": False}


def test_alignment_must_keep_rows_on_the_local_file(masters):
    link = masters.link(USER, "v_1", "4", *FILTERS)
    before = preview(masters, link["linkId"], {"offset_ms": -900_000})
    assert before.json() == {"code": "alignment_out_of_range", "retryable": False}
    token = preview(masters, link["linkId"], {"offset_ms": 0}).json()["token"]
    masters.set_duration(600)
    after = preview(masters, link["linkId"], {"offset_ms": 0})
    assert after.json()["code"] == "alignment_out_of_range"
    refused = approve(masters, link["linkId"], token)
    assert refused.json() == {"code": "alignment_out_of_range", "retryable": False}
    assert masters.ok("GET", f"/masters/{link['linkId']}", USER)["link"]["alignment"] is None


def test_approval_capability_is_checked_before_confirmation(masters):
    from scanner.security.access.capability_ids import Capability as C

    link = masters.link(USER, "v_1", "4", *FILTERS)
    masters.deny(USER, C.COMMUNITY_DOWNLOAD)
    refused = approve(masters, link["linkId"], "token", confirmed=False)
    assert (refused.status_code, refused.json()["code"]) == (403, "capability_denied")


def test_preview_tokens_expire_and_are_capped_per_account():
    from scanner.http.community_master_alignment_api import (
        MAX_PREVIEWS_PER_OWNER,
        PREVIEW_TTL_SECONDS,
        AlignmentPreviews,
    )

    now = [0.0]
    store = AlignmentPreviews(clock=lambda: now[0])

    def put(owner, link="l_1"):
        return store.put(owner_id=owner, link_id=link, revision=1, alignment={}, fingerprint="f")

    expiring = put(1)
    now[0] = PREVIEW_TTL_SECONDS + 1
    assert store.take(expiring, 1, "l_1") is None
    other = put(2)
    first = put(1)
    later = [put(1) for _ in range(MAX_PREVIEWS_PER_OWNER)]
    assert store.take(first, 1, "l_1") is None
    assert store.take(later[0], 1, "l_2") is None
    assert store.take(later[0], 1, "l_1").owner_id == 1
    assert store.take(later[0], 1, "l_1") is None
    assert store.take(other, 2, "l_1").owner_id == 2
