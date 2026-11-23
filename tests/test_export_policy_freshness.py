"""Old canonical exports advertise newly mandatory coverage without rewriting bytes."""

import json
from pathlib import Path

import pytest

from scanner import repository
from scanner.db import MediaItemExportRow, ReviewFilterRow, SavedReviewRow, ScanFindingRow
from scanner.models import FilterDefaultsSettings
from scanner.persistence.repository import review_export_content
from scanner.persistence.repository.export_discovery import publication_status
from scanner.persistence.repository.export_regeneration import align_regeneration
from scanner.persistence.repository.repository_settings import replace_filter_defaults
from scanner.settings import filter_defaults
from tests.filter_policy_http_support import rule
from tests.multi_user_access_support import ADMIN, USER
from tests.scan_access_http import LIBRARY_A, guid
from tests.test_export_discovery_http import discovered
from tests.test_personal_exports_http import app as app
from tests.test_personal_exports_http import publish
from tests.test_personal_exports_http import review_app as review_app
from tests.test_review_policy_binding import import_review
from tests.test_review_policy_view import enforce


def _prepare(app, monkeypatch, *, selected=False, covering_skip=False, enforced=True):
    item_id = app.jellyfin_item(guid("implicit-channel-policy"), LIBRARY_A)
    content = b"## swearing: Expletive\n1.000 2.000 1\n"
    if covering_skip:
        content += b"1.000 1.500 3\n1.500 2.000 3\n"
    imported = import_review(app, monkeypatch, item_id, content)
    review_id = imported["id"]
    with app.accounts.factory() as db:
        saved = db.query(ReviewFilterRow).filter_by(review_id=review_id, source="audio").one()
        finding = db.get(ScanFindingRow, saved.source_finding_id)
        finding.mcf_channel = saved.mcf_channel = None
        finding.selected = saved.selected = selected
        finding.severity = saved.severity = "low"
        db.get(SavedReviewRow, review_id).mcf_document.material_end_seconds = 60
        db.commit()
    deny = rule("deny", mcf_channel="audio")
    if enforced:
        enforce(app.accounts, rules=[deny])
    else:
        with app.accounts.factory() as db:
            settings = repository.get_structured_filter_defaults_settings(db)
            replace_filter_defaults(
                db,
                FilterDefaultsSettings(
                    revision=settings.revision,
                    selection_rules=[deny],
                    severity_defaults=settings.severity_defaults,
                ),
            )
            db.commit()
    return review_id


def _old_publication(app, monkeypatch, review_id, format, target, user):
    matcher = filter_defaults._rule_matches
    field_value = review_export_content.filter_mcf_value

    def old_match(rule, candidate):
        if rule.mcf_channel is not None and candidate.mcf_channel != rule.mcf_channel:
            return False
        return matcher(rule, candidate)

    def old_mcf_value(cue, field):
        if field == "mcf_channel" and cue.mcf_channel is None:
            return "both"
        return field_value(cue, field)

    with monkeypatch.context() as patch, app.accounts.factory() as db:
        patch.setattr(filter_defaults, "_rule_matches", old_match)
        patch.setattr(review_export_content, "filter_mcf_value", old_mcf_value)
        review = repository.get_saved_review(db, review_id)
        response = publish(app, review, format, user=user, target=target, **_policy(review))
        assert response.status_code == 200, response.text
        publication = response.json()
        write = db.get(MediaItemExportRow, publication["id"])
        snapshot = json.loads(write.snapshot_json)
        snapshot.pop("policy_channel_evaluation")
        write.snapshot_json = json.dumps(snapshot)
        db.commit()
    return review, publication


def _policy(review):
    return {"policy_owner_id": review.policy_owner_id, "policy_revision": review.policy_revision}


@pytest.mark.parametrize("format", ["edl", "mcf"])
@pytest.mark.parametrize(("target", "user"), [("self", USER), ("item_default", ADMIN)])
def test_missing_mandatory_cue_stays_served_outdated_until_republished(
    app, monkeypatch, format, target, user
):
    review_id = _prepare(app, monkeypatch)
    old, publication = _old_publication(app, monkeypatch, review_id, format, target, user)
    with app.accounts.factory() as db:
        row = db.get(MediaItemExportRow, publication["id"])
        retained = row.snapshot_json
        path = Path(row.destination_path)
        before = row.content_bytes
        assert path.read_bytes() == before
        assert b"1.000 2.000 1" not in before and b"00:00:01.000" not in before
        assert not align_regeneration(db, row)
        assert row.regeneration is None

    direct = app.send("GET", f"/api/exports/{publication['id']}", app.accounts.cookie(user))
    assert direct.status_code == 200, direct.text
    status = discovered(app, old, user)[0]
    assert direct.json() == status
    assert status["outdated"] and status["serving"] and not status["current"]
    assert status["regeneration_state"] == "idle"
    with app.accounts.factory() as db:
        row = db.get(MediaItemExportRow, publication["id"])
        assert (row.content_bytes, row.snapshot_json, row.regeneration) == (before, retained, None)
        assert path.read_bytes() == before
        current = repository.get_saved_review(db, old.id)
        assert _policy(current) == _policy(old)
        assert current.revision == old.revision
        assert current.filters[0].selected

    response = publish(app, current, format, user=user, target=target, **_policy(current))
    assert response.status_code == 200, response.text
    updated = response.json()
    assert updated["id"] == publication["id"]
    assert updated["current"] and not updated["outdated"]
    assert b"1.000 2.000 1" in path.read_bytes() or b"00:00:01.000" in path.read_bytes()
    with app.accounts.factory() as db:
        row = db.get(MediaItemExportRow, publication["id"])
        assert "policy_channel_evaluation" in json.loads(row.snapshot_json)


@pytest.mark.parametrize(
    ("format", "options"),
    [
        ("edl", {"covering_skip": True}),
        ("edl", {"selected": True}),
        ("mcf", {"selected": True}),
        ("edl", {"enforced": False}),
        ("mcf", {"enforced": False}),
    ],
)
def test_old_publication_remains_current_when_no_mandatory_cue_is_missing(
    app, monkeypatch, format, options
):
    review_id = _prepare(app, monkeypatch, **options)
    review, _ = _old_publication(app, monkeypatch, review_id, format, "self", USER)
    status = discovered(app, review)[0]
    assert status["current"] and not status["outdated"]


def test_detached_personal_publication_uses_its_retained_timing_and_evidence(app, monkeypatch):
    review_id = _prepare(app, monkeypatch)
    _, publication = _old_publication(app, monkeypatch, review_id, "edl", "self", USER)
    with app.accounts.factory() as db:
        row = db.get(MediaItemExportRow, publication["id"])
        row.review_id = None
        row.exported_format_revision = None
        db.commit()
        status = publication_status(row, USER)
        assert status["outdated"] and status["serving"] and not status["current"]
