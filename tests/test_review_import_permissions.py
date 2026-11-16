"""Import and copy permissions cannot grant independently denied review fields."""

import json

import pytest

import scanner.api as root
from scanner.db import SavedReviewRow, ScanRow
from scanner.security.access.capability_ids import Capability as C
from scanner.security.access.overrides import OverrideEffect
from tests.multi_user_access_support import ADMIN, USER
from tests.scan_access_http import LIBRARY_A, guid
from tests.test_review_access_http import IMPORTS, seeded_review
from tests.test_review_access_http import app as app
from tests.test_review_access_workflows import credits_scan

CONTENT_CAPABILITIES = (
    C.REVIEWS_EVIDENCE_EDIT_OWN,
    C.REVIEWS_FILTERS_EDIT_OWN,
    C.REVIEWS_CUES_EDIT_OWN,
    C.REVIEWS_METADATA_EDIT_OWN,
    C.REVIEWS_TIMING_EDIT_OWN,
)


def import_review(app, monkeypatch, kind, owner=USER, *, credits=None, item_id=None):
    item_id = item_id or app.jellyfin_item(guid("field-import"), LIBRARY_A)
    monkeypatch.setattr(root, "get_video_duration", lambda _path: 60)
    options = {"target": {"source": "jellyfin", "item_id": item_id}, "name": "import"}
    if kind == "mcf":
        options.update(
            synchronization={"start_seconds": 0, "end_seconds": 60, "end_mode": "file_end"},
            mcf_material_end_seconds=60,
            accepted_credits=credits,
        )
    app.client.cookies.clear()
    return app.client.post(
        f"/api/{kind}-imports/upload",
        headers=app.accounts.unsafe_cookie(owner),
        data={"options": json.dumps(options)},
        files={"file": (f"field-input.{kind}", IMPORTS[kind])},
    )


@pytest.mark.parametrize("kind", ["edl", "mcf"])
@pytest.mark.parametrize("owner", [USER, ADMIN])
@pytest.mark.parametrize("capability", CONTENT_CAPABILITIES)
def test_import_refuses_denied_content_field_atomically(app, monkeypatch, kind, owner, capability):
    accepted = import_review(app, monkeypatch, kind, owner)
    assert accepted.status_code == 201, accepted.text
    app.accounts.set_override(owner, capability, OverrideEffect.DENY)
    with app.accounts.factory() as db:
        before = (db.query(SavedReviewRow).count(), db.query(ScanRow).count())
    denied = import_review(app, monkeypatch, kind, owner)
    assert denied.status_code == 403, denied.text
    assert denied.json()["code"] == "capability_denied"
    with app.accounts.factory() as db:
        assert (db.query(SavedReviewRow).count(), db.query(ScanRow).count()) == before


@pytest.mark.parametrize("actor", [USER, ADMIN])
@pytest.mark.parametrize("capability", (*CONTENT_CAPABILITIES, C.REVIEWS_CREDITS_EDIT_OWN))
def test_copy_checks_destination_own_field_permissions(app, monkeypatch, actor, capability):
    source = seeded_review(app, USER, "copied-credits")
    scan_id = credits_scan(app, source)
    accepted = app.send(
        "POST",
        f"/api/credits-detections/{scan_id}/accept",
        app.accounts.unsafe_cookie(USER),
        {
            "context": {"kind": "review", "review_id": source.id, "revision": 0},
            "accepted_boundary_seconds": 51,
        },
    )
    assert accepted.status_code == 200, accepted.text
    imported = import_review(
        app,
        monkeypatch,
        "mcf",
        credits=accepted.json()["accepted_credits"],
        item_id=guid("copied-credits"),
    )
    assert imported.status_code == 201, imported.text
    review_id = imported.json()["review"]["id"]
    path = f"/api/reviews/{review_id}/duplicate"
    allowed = app.send(
        "POST", path, app.accounts.unsafe_cookie(actor), {"revision": 0, "name": "allowed copy"}
    )
    assert allowed.status_code == 201, allowed.text
    assert allowed.json()["accepted_credits"] is not None
    app.accounts.set_override(actor, capability, OverrideEffect.DENY)
    with app.accounts.factory() as db:
        before = (db.query(SavedReviewRow).count(), db.query(ScanRow).count())
    denied = app.send(
        "POST", path, app.accounts.unsafe_cookie(actor), {"revision": 0, "name": "denied copy"}
    )
    assert denied.status_code == 403, denied.text
    assert denied.json()["code"] == "capability_denied"
    with app.accounts.factory() as db:
        assert (db.query(SavedReviewRow).count(), db.query(ScanRow).count()) == before
        assert db.get(SavedReviewRow, review_id).revision == 0
