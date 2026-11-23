"""Reject stale observations before persisting an empty saved review."""

from pathlib import Path

import pytest

from scanner.db import MediaVersionRow, SavedReviewRow
from scanner.persistence.repository.media_identity_store import get_version, review_version_id
from scanner.runtime.media_fingerprints import sampled_media_fingerprint
from tests.multi_user_access_support import USER
from tests.scan_access_http import LIBRARY_A, guid
from tests.test_review_access_http import app as app


@pytest.mark.parametrize("remap", [False, True])
def test_empty_review_rejects_replacement_before_admission(app, monkeypatch, remap):
    from scanner.http import review_target_routes

    item_id = app.jellyfin_item(guid("empty-sampled-review"), LIBRARY_A)
    target = Path(app.items[item_id].path)
    link = target.with_suffix(".link.mkv")
    if remap:
        link.symlink_to(target)
        app.items[item_id] = app.items[item_id].model_copy(update={"path": str(link)})
    destination = target.with_suffix(".replacement.mkv")
    destination.write_bytes(b"a different physical generation")
    expected = sampled_media_fingerprint(destination).digest

    def sample_then_replace(filename):
        fingerprint = sampled_media_fingerprint(filename)
        if remap:
            link.unlink()
            link.symlink_to(destination)
        else:
            destination.replace(target)
        return fingerprint

    route = f"/api/library-items/{item_id}/reviews"
    headers = app.accounts.unsafe_cookie(USER)
    with monkeypatch.context() as patch:
        patch.setattr(review_target_routes, "sampled_media_fingerprint", sample_then_replace)
        refused = app.send("POST", route, headers, {"name": "Empty review"})
    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"]["code"] == "media_unavailable"
    with app.accounts.factory() as db:
        assert db.query(SavedReviewRow).count() == 0
        assert db.query(MediaVersionRow).count() == 0

    created = app.send("POST", route, headers, {"name": "Empty review"})
    assert created.status_code == 201, created.text
    with app.accounts.factory() as db:
        pinned = get_version(db, review_version_id(db, created.json()["id"]))
        assert pinned.sampled_md5 == expected
        assert pinned.content_sha256 is None
