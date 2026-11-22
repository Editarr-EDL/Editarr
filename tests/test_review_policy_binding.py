"""Previously bound source evidence remains authoritative across draft edits."""

import json

import pytest

import scanner.api as root
from scanner.http.review_policy_errors import install_review_policy_errors
from tests.filter_policy_http_support import rule
from tests.multi_user_access_support import USER
from tests.scan_access_http import LIBRARY_A, guid
from tests.test_review_access_http import app as app
from tests.test_review_policy_view import draft, enforce
from tests.test_review_policy_view import policy_review as policy_review


def body(detail):
    """A save request from a review response (read-only filter provenance dropped)."""
    return {
        field: (
            [_input(value) for value in detail[field]] if field == "filters" else detail[field]
        )
        for field in (
            "revision",
            "name",
            "memberships",
            "filters",
            "mcf_document",
            "synchronization",
            "accepted_credits",
            "policy_owner_id",
            "policy_revision",
        )
    }


def _input(review_filter):
    return {key: value for key, value in review_filter.items() if key != "label_edited"}


def tokens(detail):
    return {field: detail[field] for field in ("revision", "policy_owner_id", "policy_revision")}


def import_review(app, monkeypatch, item_id, content):
    install_review_policy_errors(app.client.app)
    monkeypatch.setattr(root, "get_video_duration", lambda _path: 60.0)
    app.client.cookies.clear()
    response = app.client.post(
        "/api/edl-imports/upload",
        headers=app.accounts.unsafe_cookie(USER),
        data={
            "options": json.dumps(
                {"target": {"source": "jellyfin", "item_id": item_id}, "name": "Probe"}
            )
        },
        files={"file": ("input.edl", content)},
    )
    assert response.status_code == 201, response.text
    return response.json()["review"]


@pytest.mark.parametrize(
    "strategy", ["replace-membership", "copy-replace", "restore-moved", "narrow-role"]
)
def test_missing_evidence_cannot_be_laundered_into_outputs(app, monkeypatch, strategy):
    item_id = app.jellyfin_item(guid("mu24r2-evidence"), LIBRARY_A)
    original = import_review(app, monkeypatch, item_id, b"1 2 1\n3 4 3\n")
    neutral = import_review(app, monkeypatch, item_id, b"7 8 3\n")
    write = app.accounts.unsafe_cookie(USER)
    path = f"/api/reviews/{original['id']}"
    audio = next(value for value in original["filters"] if value["source"] == "audio")
    deleted = app.send(
        "PUT",
        path,
        write,
        body(original)
        | {"filters": [value for value in original["filters"] if value["source"] != "audio"]},
    )
    assert deleted.status_code == 200, deleted.text
    enforce(app.accounts, rules=[rule("deny", evidence_source="audio")])
    detail = app.send("GET", path, app.accounts.cookie(USER)).json()
    assert detail["policy_missing_evidence"]
    for format_name in ("edl", "mcf"):
        refused = app.send("POST", f"{path}/{format_name}", write, tokens(detail))
        assert refused.status_code == 409, refused.text
        assert refused.json()["detail"]["code"] == "review_policy_evidence_missing"
    if strategy == "copy-replace":
        response = app.send("POST", f"{path}/duplicate", write, tokens(detail) | {"name": "Copy"})
        assert response.status_code == 201, response.text
        detail = response.json()
        path = f"/api/reviews/{detail['id']}"
        assert detail["policy_missing_evidence"]
    proposal = body(detail)
    proposal["mcf_document"] = proposal["mcf_document"] | {"material_end_seconds": 60.0}
    if strategy == "restore-moved":
        proposal["filters"] = detail["filters"] + [audio | {"start_seconds": 20, "end_seconds": 21}]
    elif strategy == "narrow-role":
        proposal["memberships"] = [value | {"role": "video"} for value in detail["memberships"]]
    else:
        proposal["memberships"] = neutral["memberships"]
        proposal["filters"] = neutral["filters"]
    saved = app.send("PUT", path, write, proposal)
    assert saved.status_code == 409, saved.text
    assert saved.json()["detail"]["code"] in {
        "review_policy_enforced",
        "review_policy_evidence_missing",
    }
    fresh = app.send("GET", path, app.accounts.cookie(USER)).json()
    assert fresh["policy_missing_evidence"]
    for format_name in ("edl", "mcf"):
        assert app.send("POST", f"{path}/{format_name}", write, tokens(fresh)).status_code == 409


@pytest.mark.parametrize("different_owner", [False, True])
def test_detached_history_survives_copy_and_uses_destination_policy(policy_review, different_owner):
    from scanner import repository
    from scanner.models import ReviewDuplicate
    from scanner.persistence.repository.activity_ownership import ActivityOwner
    from tests.multi_user_access_support import PEER_USER

    accounts, review_id = policy_review
    with accounts.factory() as db:
        original = repository.get_saved_review(db, review_id)
        detached = repository.save_saved_review(
            db, review_id, draft(original, memberships=[], filters=[])
        )
        references = repository.get_scan_references(db, "scan")
        assert references.reviews == []
    enforce(accounts)
    with accounts.factory() as db:
        detail = repository.get_saved_review(db, review_id)
        assert not detail.policy_missing_evidence
        renamed = repository.save_saved_review(
            db, review_id, draft(detail, name="Still repairable")
        )
        assert not renamed.policy_missing_evidence
        db.info["review_actor"] = ActivityOwner(PEER_USER if different_owner else USER, 1)
        copied = repository.duplicate_saved_review(
            db,
            review_id,
            ReviewDuplicate(
                revision=renamed.revision,
                name="History copy",
                policy_owner_id=renamed.policy_owner_id,
                policy_revision=renamed.policy_revision,
            ),
        )
        assert not copied.policy_missing_evidence
        if different_owner:
            from scanner.db import SavedReviewRow

            db.get(SavedReviewRow, copied.id).owner_id = USER
            db.commit()
            copied = repository.get_saved_review(db, copied.id)
            assert not copied.policy_missing_evidence
        repaired = repository.save_saved_review(
            db,
            copied.id,
            draft(
                copied,
                memberships=[value.model_dump() for value in original.memberships],
                filters=[value.model_dump() for value in original.filters],
            ),
        )
        assert not repaired.policy_missing_evidence
        assert repaired.filters[0].selected
        assert detached.memberships == []


def test_required_interval_is_source_floor_not_previous_widening(policy_review):
    from scanner import repository

    accounts, review_id = policy_review
    enforce(accounts)
    with accounts.factory() as db:
        detail = repository.get_saved_review(db, review_id)
        widened = detail.filters[0].model_copy(update={"start_seconds": 0.5, "end_seconds": 3})
        saved = repository.save_saved_review(
            db, review_id, draft(detail, filters=[widened.model_dump()])
        )
        repaired = saved.filters[0].model_copy(update={"start_seconds": 1, "end_seconds": 2})
        saved = repository.save_saved_review(
            db, review_id, draft(saved, filters=[repaired.model_dump()])
        )
        assert not saved.policy_missing_evidence
        assert (saved.filters[0].start_seconds, saved.filters[0].end_seconds) == (1, 2)


def test_preview_refuses_other_filters_while_evidence_is_missing(policy_review, monkeypatch):
    from scanner import repository
    from scanner.db import ReviewFilterRow
    from scanner.persistence.repository.review_policy import ReviewPolicyConflict

    accounts, review_id = policy_review
    with accounts.factory() as db:
        detail = repository.get_saved_review(db, review_id)
        row = db.get(ReviewFilterRow, (review_id, detail.filters[0].id))
        row.start_seconds, row.end_seconds = 20, 21
        db.commit()
    enforce(accounts)
    with accounts.factory() as db:
        with pytest.raises(ReviewPolicyConflict) as refused:
            repository.render_saved_review_filter_preview(
                db, review_id, detail.filters[0].id, preview_kind="audio"
            )
        assert refused.value.code == "review_policy_evidence_missing"
