"""Export the saved, effective review to a portable VideoSkip attachment."""

from urllib.parse import unquote

import pytest

from scanner.db import MediaItemExportRow, ReviewPolicyEvidenceRow, SavedReviewRow
from scanner.formats.videoskip import parse_videoskip
from scanner.security.access.capability_ids import Capability
from scanner.security.access.overrides import OverrideEffect
from tests.filter_policy_http_support import rule
from tests.multi_user_access_support import ADMIN, PEER_USER, USER
from tests.scan_access_http import LIBRARY_A, guid
from tests.test_personal_exports_http import app as app
from tests.test_personal_exports_http import review_app as review_app
from tests.test_review_policy_binding import body, import_review, tokens
from tests.test_review_policy_view import enforce

__all__ = ["app", "review_app"]


@pytest.fixture
def saved_review(app, monkeypatch):
    item_id = app.jellyfin_item(guid("videoskip-export"), LIBRARY_A)
    return import_review(
        app,
        monkeypatch,
        item_id,
        b"## swearing: Sound\n1.125 2.750 1\n"
        b"## gore: Scene\n3.000 4.500 3\n"
        b"## ageism: Bias\n5.000 6.000 3\n"
        b"## gore: Deselected\n7.000 8.000 3\n",
    )


def export_request(app, review, operation, *, actor=USER, **options):
    return app.send(
        "POST",
        f"/api/reviews/{review['id']}/exports/videoskip/{operation}",
        app.accounts.unsafe_cookie(actor),
        tokens(review) | options,
    )


def test_download_exports_selected_skip_and_mute_without_publishing(app, saved_review):
    review_path = f"/api/reviews/{saved_review['id']}"
    filters = body(saved_review)["filters"]
    for cue in filters:
        cue["selected"] = cue["start_seconds"] != 7
    saved = app.send(
        "PUT",
        review_path,
        app.accounts.unsafe_cookie(USER),
        body(saved_review) | {"filters": filters},
    )
    assert saved.status_code == 200, saved.text
    review = saved.json()
    before = sorted(app.media.rglob("*"))
    reference = {"reference_seconds": 0.125, "reference_description": "Studio logo"}
    preflight = export_request(app, review, "preflight", **reference)
    assert preflight.status_code == 200, preflight.text
    assert preflight.headers["cache-control"] == "private, no-store"
    report = preflight.json()
    assert report["eligible"]
    assert report["filter_count"] == report["exported_count"] == 3
    assert report["issue_count"] == len(report["issues"])
    ageism = next(cue for cue in review["filters"] if cue["subcategory"] == "ageism")
    assert any(
        (issue["code"], issue["filter_id"], issue["omitted"])
        == ("category_mapped", ageism["id"], False)
        for issue in report["issues"]
    )

    download = export_request(app, review, "download", **reference)
    assert download.status_code == 200, download.text
    assert download.headers["content-type"] == "text/plain; charset=utf-8"
    assert download.headers["cache-control"] == "private, no-store"
    filename = unquote(download.headers["content-disposition"].split("UTF-8''")[1])
    assert filename.endswith(".skp") and "/" not in filename and "\\" not in filename
    assert str(app.media) not in download.text
    parsed = parse_videoskip(download.content)
    assert parsed.issues == []
    assert (parsed.sync.reference_seconds, parsed.sync.reference_description) == (
        0.125,
        "Studio logo",
    )
    assert [
        (cue.start_seconds, cue.end_seconds, cue.source_action, cue.subcategory)
        for cue in parsed.cues
    ] == [
        (1.125, 2.750, "mute", "swearing"),
        (3, 4.5, "skip", "gore"),
        (5, 6, "skip", "discrimination"),
    ]
    assert sorted(app.media.rglob("*")) == before
    with app.accounts.factory() as db:
        assert db.query(MediaItemExportRow).count() == 0
    no_publish = app.send(
        "POST", f"{review_path}/exports/videoskip", app.accounts.unsafe_cookie(USER), tokens(review)
    )
    assert no_publish.status_code == 422, no_publish.text


@pytest.mark.parametrize("operation", ["preflight", "download"])
def test_download_uses_current_revision_and_owner_policy(app, saved_review, operation):
    stale_revision = export_request(
        app, saved_review, operation, revision=saved_review["revision"] + 1
    )
    assert stale_revision.status_code == 409
    assert stale_revision.json()["detail"]["code"] == "stale_review_revision"
    enforce(app.accounts, rules=[rule("deny", evidence_source="audio")])
    stale_policy = export_request(app, saved_review, operation)
    assert stale_policy.status_code == 409
    assert stale_policy.json()["detail"]["code"] == "stale_review_policy"


@pytest.mark.parametrize("operation", ["preflight", "download"])
def test_download_admits_only_authorized_visible_reviews(app, saved_review, operation):
    assert export_request(app, saved_review, operation, actor=PEER_USER).status_code == 404
    assert export_request(app, saved_review, operation, actor=ADMIN).status_code == 200
    for actor in (USER, ADMIN):
        app.accounts.set_override(
            actor, Capability.EXPORTS_PERSONAL_DOWNLOAD_OWN, OverrideEffect.DENY
        )
        assert export_request(app, saved_review, operation, actor=actor).status_code == 403


def test_preflight_reports_unrepresentable_cues_and_rejects_empty_export(app, saved_review):
    with app.accounts.factory() as db:
        review = db.get(SavedReviewRow, saved_review["id"])
        for cue in review.filters:
            cue.selected = cue.start_seconds == 3
            if cue.selected:
                cue.end_seconds = 3.0002
                selected_id = cue.id
        db.commit()
    preflight = export_request(app, saved_review, "preflight")
    assert preflight.status_code == 200, preflight.text
    report = preflight.json()
    assert (report["eligible"], report["filter_count"], report["exported_count"]) == (False, 1, 0)
    (issue,) = report["issues"]
    assert (issue["filter_id"], issue["code"], issue["omitted"]) == (
        selected_id,
        "invalid_cue_range",
        True,
    )
    refusal = export_request(app, saved_review, "download")
    assert refusal.status_code == 422
    assert refusal.json()["detail"]["code"] == "incomplete_videoskip_export"


def test_a_required_cue_cannot_be_silently_omitted(app, saved_review):
    with app.accounts.factory() as db:
        review = db.get(SavedReviewRow, saved_review["id"])
        audio = next(cue for cue in review.filters if cue.source == "audio")
        evidence = db.get(ReviewPolicyEvidenceRow, (review.id, audio.source_finding_id))
        # Older evidence can have an open `end_seconds`. VideoSkip needs a range.
        evidence.end_seconds = audio.end_seconds = None
        audio.selected = False
        db.commit()
    enforce(app.accounts, rules=[rule("deny", evidence_source="audio")])
    detail = app.send("GET", f"/api/reviews/{saved_review['id']}", app.accounts.cookie(USER)).json()
    report_response = export_request(app, detail, "preflight")
    assert report_response.status_code == 200, report_response.text
    report = report_response.json()
    assert (report["eligible"], report["filter_count"], report["exported_count"]) == (False, 4, 3)
    refusal = export_request(app, detail, "download")
    assert refusal.status_code == 422
    assert (
        refusal.json()["detail"]["message"]
        == "A required filter cannot be represented in VideoSkip."
    )


@pytest.mark.parametrize(
    "reference",
    [
        {"reference_seconds": 10},
        {"reference_description": "Only description"},
        {"reference_seconds": 61, "reference_description": "Outside the cut"},
        {"reference_seconds": 59.9997, "reference_description": "Rounds to the end"},
    ],
)
def test_export_reference_requires_a_labeled_timestamp_inside_runtime(app, saved_review, reference):
    response = export_request(app, saved_review, "preflight", **reference)
    assert response.status_code == 422, response.text
