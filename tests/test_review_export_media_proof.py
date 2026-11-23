"""Interactive exports consume current exact-media evidence under the SQLite writer lock."""

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from scanner.db import MediaItemExportRow, SavedReviewRow
from scanner.persistence.repository import review_access, review_version_evidence
from scanner.persistence.repository.media_identity_store import (
    bind_review_version,
    observe_sampled_version,
)
from scanner.runtime.media_fingerprints import sampled_media_fingerprint
from scanner.security.access.capability_ids import Capability as C
from scanner.security.access.overrides import OverrideEffect
from tests.multi_user_access_support import USER
from tests.test_personal_exports_http import app as app
from tests.test_personal_exports_http import publish, seeded_review
from tests.test_personal_exports_http import review_app as review_app


def _pinned_review(app):
    review = seeded_review(app, USER, "Exact export media")
    with app.accounts.factory() as db:
        row = db.get(SavedReviewRow, review.id)
        scan = row.memberships[0].scan
        path = Path(scan.resolved_local_path)
        version = observe_sampled_version(
            db,
            media=row.media_item,
            media_source_id=scan.media_source_id,
            source_path=scan.source_path,
            fingerprint=sampled_media_fingerprint(path),
            scan_id=scan.id,
        )
        bind_review_version(db, review.id, version)
        db.commit()
    return review, path


@pytest.mark.parametrize(
    ("method", "endpoint"),
    (
        ("POST", "exports/edl"),
        ("POST", "exports/mcf/download"),
        ("POST", "edl"),
        ("POST", "mcf"),
        ("GET", "mcf/preflight"),
    ),
)
def test_interactive_exports_fingerprint_once_before_the_writer_lock(
    app, monkeypatch, method, endpoint
):
    review, media = _pinned_review(app)
    observed = []

    def fingerprint(path):
        with closing(sqlite3.connect(app.accounts.path, timeout=0.1)) as writer:
            writer.execute("BEGIN IMMEDIATE")
        observed.append(Path(path))
        return sampled_media_fingerprint(path)

    monkeypatch.setattr(review_version_evidence, "sampled_media_fingerprint", fingerprint)
    headers = app.accounts.unsafe_cookie(USER)
    url = f"/api/reviews/{review.id}/{endpoint}"
    response = (
        app.send(method, url, headers, {"revision": review.revision})
        if method == "POST"
        else app.send(method, url, headers)
    )
    assert response.status_code == 200, response.text
    assert observed == [media]


@pytest.mark.parametrize("denied", [C.EXPORTS_PERSONAL_PUBLISH_OWN, C.SCANS_FINDINGS_READ])
def test_interactive_export_checks_authority_before_fingerprinting(app, monkeypatch, denied):
    review, _ = _pinned_review(app)
    app.accounts.set_override(USER, denied, OverrideEffect.DENY)

    def unexpected_fingerprint(_path):
        pytest.fail("Media was read before authorization was checked")

    monkeypatch.setattr(
        review_version_evidence, "sampled_media_fingerprint", unexpected_fingerprint
    )
    assert publish(app, review, "edl").status_code == 403


def test_interactive_export_refuses_replacement_after_fingerprint_without_rehashing(
    app, monkeypatch
):
    review, media = _pinned_review(app)
    capture = review_access.capture_review_media_proof
    reads = []

    def fingerprint(path):
        reads.append(Path(path))
        return sampled_media_fingerprint(path)

    def capture_before_replacement(*args):
        proof = capture(*args)
        replacement = media.with_suffix(".replacement")
        replacement.write_bytes(b"Changed after the export fingerprint was read")
        replacement.replace(media)
        return proof

    monkeypatch.setattr(review_version_evidence, "sampled_media_fingerprint", fingerprint)
    monkeypatch.setattr(review_access, "capture_review_media_proof", capture_before_replacement)
    response = publish(app, review, "edl")
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "invalid_membership"
    assert reads == [media]
    with app.accounts.factory() as db:
        assert db.query(MediaItemExportRow).count() == 0
