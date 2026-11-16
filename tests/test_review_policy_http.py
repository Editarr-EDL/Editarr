"""Real authenticated handlers must return effective content and reject stale policies."""

import json

import pytest

import scanner.api as root
from scanner.db import SavedReviewRow
from scanner.http import catalog_settings_api
from scanner.http.review_policy_errors import install_review_policy_errors
from tests.filter_policy_http_support import rule
from tests.multi_user_access_support import USER
from tests.scan_access_http import LIBRARY_A, guid
from tests.test_review_access_http import IMPORTS
from tests.test_review_access_http import app as app
from tests.test_review_policy_view import enforce


@pytest.mark.parametrize("credential", ["session", "key"])
@pytest.mark.parametrize("kind", ["edl", "mcf"])
def test_policy_import_read_save_apply_and_export(app, monkeypatch, credential, kind):
    from tests.test_review_policy_origins import independent_copy

    install_review_policy_errors(app.client.app)
    app.client.app.include_router(catalog_settings_api.router)
    item_id = app.jellyfin_item(guid("policy-import"), LIBRARY_A)
    monkeypatch.setattr(root, "get_video_duration", lambda _path: 60.0)
    independent_copy(app.accounts)
    enforce(
        app.accounts,
        rules=[rule("deny", evidence_source="audio" if kind == "edl" else "visual")],
        severities=["violence"],
    )
    options = {"target": {"source": "jellyfin", "item_id": item_id}, "name": "Policy import"}
    if kind == "mcf":
        options |= {
            "synchronization": {"start_seconds": 0, "end_seconds": 60, "end_mode": "file_end"},
            "mcf_material_end_seconds": 60,
        }
    headers = (
        app.accounts.unsafe_cookie(USER) if credential == "session" else app.accounts.key(USER)
    )
    app.client.cookies.clear()
    imported = app.client.post(
        f"/api/{kind}-imports/upload",
        headers=headers,
        data={"options": json.dumps(options)},
        files={"file": (f"input.{kind}", IMPORTS[kind])},
    )
    assert imported.status_code == 201, imported.text
    detail = imported.json()["review"]
    review_id = detail["id"]
    assert detail["filters"][0]["selected"]
    assert detail["policy_owner_id"] == USER
    with app.accounts.factory() as db:
        row = db.get(SavedReviewRow, review_id)
        assert row.owner_id == USER
        base = row.filters[0].severity
    stale = app.send(
        "POST",
        f"/api/reviews/{review_id}/{kind}",
        headers,
        {"revision": detail["revision"], "policy_revision": 0, "policy_owner_id": USER},
    )
    assert stale.status_code == 409, stale.text
    assert stale.json()["detail"]["code"] == "stale_review_policy"
    applied = app.send(
        "POST",
        "/api/settings/filters/apply",
        headers,
        {
            "review_id": review_id,
            "filters": detail["base_filters"],
            "policy_owner_id": USER,
            "policy_revision": detail["policy_revision"],
        },
    )
    assert applied.status_code == 200, applied.text
    assert applied.json()["filters"][0]["selected"]
    if kind == "mcf":
        assert applied.json()["filters"][0]["severity"] == "high"
        assert applied.json()["base_filters"][0]["severity"] == "low"
    with app.accounts.factory() as db:
        assert db.get(SavedReviewRow, review_id).filters[0].severity == base


def test_credits_preparation_emits_base_and_owner_policy(app):
    from tests.test_review_access_http import seeded_review
    from tests.test_review_access_workflows import credits_scan

    review = seeded_review(app, USER, "Policy credits")
    scan_id = credits_scan(app, review)
    enforce(app.accounts, severities=["dispensable"])
    detail = app.send("GET", f"/api/reviews/{review.id}", app.accounts.cookie(USER)).json()
    context = {
        "kind": "review",
        "review_id": review.id,
        "revision": detail["revision"],
        "policy_owner_id": USER,
        "policy_revision": detail["policy_revision"],
    }
    response = app.send(
        "POST",
        f"/api/credits-detections/{scan_id}/accept",
        app.accounts.unsafe_cookie(USER),
        {"context": context, "accepted_boundary_seconds": 51},
    )
    assert response.status_code == 200, response.text
    assert response.json()["filter"]["severity"] == "high"
    assert response.json()["base_filter"]["severity"] == "medium"
    refused = app.send(
        "POST",
        f"/api/credits-detections/{scan_id}/accept",
        app.accounts.unsafe_cookie(USER),
        {"context": context | {"policy_revision": 0}, "accepted_boundary_seconds": 51},
    )
    assert refused.status_code == 409
    assert refused.json()["detail"]["code"] == "stale_review_policy"


@pytest.mark.parametrize("deliberate_severity", [None, "high"])
def test_credits_accept_save_remove_policy_restores_base_severity(app, deliberate_severity):
    from tests.test_review_access_http import seeded_review
    from tests.test_review_access_workflows import credits_scan
    from tests.test_review_policy_binding import body

    install_review_policy_errors(app.client.app)
    review = seeded_review(app, USER, "Credits base roundtrip")
    scan_id = credits_scan(app, review)
    enforce(app.accounts, rules=[], severities=["dispensable"])
    path = f"/api/reviews/{review.id}"
    detail = app.send("GET", path, app.accounts.cookie(USER)).json()
    accepted = app.send(
        "POST",
        f"/api/credits-detections/{scan_id}/accept",
        app.accounts.unsafe_cookie(USER),
        {
            "context": {
                "kind": "review",
                "review_id": review.id,
                **{
                    field: detail[field]
                    for field in ("revision", "policy_owner_id", "policy_revision")
                },
            },
            "accepted_boundary_seconds": 51,
        },
    )
    assert accepted.status_code == 200, accepted.text
    result = accepted.json()
    cue = result["base_filter"]
    if deliberate_severity:
        cue = cue | {"severity": deliberate_severity}
    saved = app.send(
        "PUT",
        path,
        app.accounts.unsafe_cookie(USER),
        body(detail)
        | {
            "memberships": detail["memberships"] + [result["membership"]],
            "filters": detail["base_filters"] + [cue],
            "filter_basis": "base",
            "accepted_credits": result["accepted_credits"],
        },
    )
    assert saved.status_code == 200, saved.text
    assert (
        next(value for value in saved.json()["filters"] if value["source_scan_id"] == scan_id)[
            "severity"
        ]
        == "high"
    )
    enforce(app.accounts, rules=[], severities=[])
    fresh = app.send("GET", path, app.accounts.cookie(USER))
    assert fresh.status_code == 200, fresh.text
    assert next(value for value in fresh.json()["filters"] if value["source_scan_id"] == scan_id)[
        "severity"
    ] == (deliberate_severity or "medium")
