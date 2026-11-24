"""Visible broken reviews refuse ordinary handlers but remain explicitly repairable."""

import json
from datetime import datetime, timezone

import pytest

import scanner.api  # noqa: F401
from scanner.db import ReviewScanRepairRow
from scanner.http import catalog_settings_api, community_sharing_api, review_repair_api
from scanner.http.review_policy_errors import install_review_policy_errors
from scanner.security.access.capability_ids import Capability as C
from scanner.security.access.overrides import OverrideEffect
from tests.multi_user_access_support import PEER_USER, USER
from tests.test_review_access_http import ACTIONS, action_body, seeded_review
from tests.test_review_access_http import app as app


@pytest.fixture
def flagged(app):
    install_review_policy_errors(app.client.app)
    app.client.app.include_router(review_repair_api.router)
    app.client.app.include_router(catalog_settings_api.router)
    app.client.app.include_router(community_sharing_api.router)
    review = seeded_review(app, USER, "Broken review")
    with app.accounts.factory() as db:
        db.add(
            ReviewScanRepairRow(
                review_id=review.id,
                missing_scan_id="deleted-scan",
                detected_at=datetime.now(timezone.utc),
                preserved_json=json.dumps({}),
            )
        )
        db.commit()
    return review


@pytest.mark.parametrize("method,suffix,body", [value for value in ACTIONS if value[0] != "DELETE"])
def test_ordinary_review_handlers_return_repair_conflict(app, flagged, method, suffix, body):
    response = app.send(
        method,
        f"/api/reviews/{flagged.id}{suffix}",
        app.accounts.unsafe_cookie(USER),
        action_body(flagged, body),
    )
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "review_needs_repair"
    assert response.json()["detail"]["repair"]["missing_scan_ids"] == ["deleted-scan"]
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("suffix", ["", "/repair/recover", "/repair/community"])
def test_flag_is_not_disclosed_to_another_user(app, flagged, suffix):
    method = "POST" if suffix else "GET"
    response = app.send(
        method, f"/api/reviews/{flagged.id}{suffix}", app.accounts.unsafe_cookie(PEER_USER)
    )
    assert response.status_code == 404, response.text
    assert "repair" not in response.text


def test_explicit_recover_and_delete_remain_available(app, flagged):
    headers = app.accounts.unsafe_cookie(USER)
    path = f"/api/reviews/{flagged.id}"
    response = app.send("POST", path + "/repair/recover", headers)
    assert response.status_code == 200, response.text
    assert response.json()["repair"] is None
    assert {"filters", "memberships", "mcf_document"}.isdisjoint(response.json())
    repeated = app.send("POST", path + "/repair/recover", headers)
    assert repeated.json()["revision"] == response.json()["revision"]
    assert (
        app.send("DELETE", path + f"?revision={response.json()['revision']}", headers).status_code
        == 204
    )


def test_recovery_requires_save_permission(app, flagged):
    app.accounts.set_override(USER, C.REVIEWS_SAVE_OWN, OverrideEffect.DENY)
    response = app.send(
        "POST", f"/api/reviews/{flagged.id}/repair/recover", app.accounts.unsafe_cookie(USER)
    )
    assert response.status_code == 403, response.text
    with app.accounts.factory() as db:
        assert db.query(ReviewScanRepairRow).filter_by(review_id=flagged.id).count() == 1


@pytest.mark.parametrize("kind", ["apply", "credits", "sharing"])
def test_non_review_router_paths_also_refuse_repair(app, flagged, kind):
    if kind == "apply":
        path, body = "/api/settings/filters/apply", {"review_id": flagged.id, "filters": []}
    elif kind == "credits":
        from tests.test_review_access_workflows import credits_scan

        scan_id = credits_scan(app, flagged)
        path = f"/api/credits-detections/{scan_id}/accept"
        body = {
            "context": {"kind": "review", "review_id": flagged.id, "revision": 0},
            "accepted_boundary_seconds": 51,
        }
    else:
        app.accounts.set_override(USER, C.COMMUNITY_PUBLISH_OWN_REVIEW, OverrideEffect.ALLOW)
        path, body = f"/api/community/sharing/sources/review/{flagged.id}", None
    response = app.send(
        "GET" if kind == "sharing" else "POST", path, app.accounts.unsafe_cookie(USER), body
    )
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "review_needs_repair"


def test_community_repair_is_retired_with_the_revision_protocol(app, flagged):
    response = app.send(
        "POST", f"/api/reviews/{flagged.id}/repair/community", app.accounts.unsafe_cookie(USER)
    )
    assert response.status_code == 410, response.text
    assert response.json() == {"error": "protocol_retired"}
    with app.accounts.factory() as db:
        assert db.query(ReviewScanRepairRow).filter_by(review_id=flagged.id).count() == 1


def test_delete_flagged_review_cascades_repair_records(app, flagged):
    response = app.send(
        "DELETE", f"/api/reviews/{flagged.id}?revision=0", app.accounts.unsafe_cookie(USER)
    )
    assert response.status_code == 204, response.text
    with app.accounts.factory() as db:
        assert db.query(ReviewScanRepairRow).filter_by(review_id=flagged.id).count() == 0
