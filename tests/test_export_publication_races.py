import errno
from pathlib import Path

import pytest

from scanner import api
from scanner.db import AuditEventRow, MediaItemExportRow, SavedReviewRow
from scanner.http import review_export_api
from scanner.models import JellyfinLibrary
from scanner.persistence.repository import export_publication, export_recovery
from tests.multi_user_access_support import ADMIN, PEER_USER, USER
from tests.scan_access_http import LIBRARY_A, LIBRARY_B
from tests.test_personal_exports_http import app as app
from tests.test_personal_exports_http import publish, seeded_review
from tests.test_personal_exports_http import review_app as review_app


@pytest.mark.parametrize("format", ["edl", "mcf"])
@pytest.mark.parametrize("route", ["canonical", "compatibility"])
@pytest.mark.parametrize("interleaving", ["none", "delete", "reuse"])
def test_publish_response_retains_its_locked_record(app, monkeypatch, format, route, interleaving):
    public = app.media / "public"
    private = app.media / "private"
    public.mkdir()
    private.mkdir()
    monkeypatch.setattr(
        api.JellyfinClient,
        "libraries",
        lambda: [
            JellyfinLibrary(id=LIBRARY_A, name="Public", locations=[str(public)]),
            JellyfinLibrary(id=LIBRARY_B, name="Private", locations=[str(private)]),
        ],
    )
    own = seeded_review(app, USER, "public/visible-publish")
    peer = seeded_review(app, PEER_USER, "private/private-peer-publish")
    app.limit_to_library(USER, LIBRARY_A)
    with app.accounts.factory() as db:
        hidden = db.get(SavedReviewRow, peer.id)
        hidden.media_item.library_id = LIBRARY_B
        peer_media_id = hidden.media_item_id
        peer_scan_id = hidden.memberships[0].scan_id
        app.items[hidden.media_item.jellyfin_id].library_id = LIBRARY_B
        source = db.get(SavedReviewRow, own.id)
        own_media_id = source.media_item_id
        own_scan_id = source.memberships[0].scan_id
        db.commit()
    for path in (f"/api/exports?media_item_id={peer_media_id}", f"/api/reviews/{peer.id}"):
        assert app.send("GET", path, app.accounts.cookie(USER)).status_code == 404
    original = review_export_api.publish
    captured = []

    def interleave(caller, review_id, request, export_format):
        result = original(caller, review_id, request, export_format)
        if review_id != own.id:
            return result
        direct = app.send("GET", f"/api/exports/{result['id']}", app.accounts.cookie(USER))
        assert direct.status_code == 200, direct.text
        captured.append(direct.json())
        if interleaving != "none":
            removed = app.send(
                "DELETE",
                f"/api/exports/{result['id']}?revision={result['publication_revision']}"
                f"&publication_id={result['publication_id']}",
                app.accounts.unsafe_cookie(USER),
            )
            assert removed.status_code == 204, removed.text
            if interleaving == "reuse":
                replaced = publish(app, peer, "mcf" if format == "edl" else "edl", user=PEER_USER)
                assert replaced.status_code == 200, replaced.text
                assert replaced.json()["id"] == result["id"]
                assert replaced.json()["publication_id"] != result["publication_id"]
        return result

    monkeypatch.setattr(review_export_api, "publish", interleave)
    suffix = f"exports/{format}" if route == "canonical" else format
    response = app.send(
        "POST",
        f"/api/reviews/{own.id}/{suffix}",
        app.accounts.unsafe_cookie(USER),
        {"revision": own.revision},
    )
    assert response.status_code == 200, response.text
    assert len(captured) == 1
    body = response.json()
    publication = body if route == "canonical" else body["publication"]
    assert publication == captured[0]
    assert publication["format"] == format
    assert publication["media_item_id"] == own_media_id
    assert publication["review_id"] == own.id
    assert publication["target_user_id"] == USER
    assert peer_scan_id not in response.text
    assert peer_media_id not in response.text
    assert "private-peer-publish" not in response.text
    if route == "compatibility":
        assert [scan["id"] for scan in body["exported_scans"]] == [own_scan_id]
        assert body["exported_format_revision"] == publication["exported_format_revision"]
        assert body["exported_review"]["id"] == own.id
        assert body["exported_at"] is not None
    with app.accounts.factory() as db:
        remaining = db.get(MediaItemExportRow, publication["id"])
        if interleaving == "delete":
            assert remaining is None
        else:
            assert remaining.review_id == (peer.id if interleaving == "reuse" else own.id)


@pytest.mark.parametrize("format", ["edl", "mcf"])
@pytest.mark.parametrize("target", ["self", "item_default"])
def test_media_parent_swap_refuses_before_ancestor_sync_or_active_write(
    app, monkeypatch, format, target
):
    (app.media / "library").mkdir()
    review = seeded_review(app, USER, "library/movie")
    library = (app.media / "library").resolve()
    original_media = (library / "movie.mkv").read_bytes()
    moved = library.with_name("library-moved")
    calls = []
    original_destination = export_publication.safe_destination
    original_sync = export_recovery._sync

    def swap_then_resolve(*args, **kwargs):
        if not moved.exists():
            library.rename(moved)
            library.symlink_to(moved, target_is_directory=True)
        return original_destination(*args, **kwargs)

    def bounded_sync(directory):
        calls.append(Path(directory))
        if len(calls) > 32:
            raise OSError(errno.EIO, "Stopped an unbounded ancestor walk")
        original_sync(directory)

    with monkeypatch.context() as fault:
        fault.setattr(export_publication, "safe_destination", swap_then_resolve)
        fault.setattr(export_recovery, "_sync", bounded_sync)
        actor = ADMIN if target == "item_default" else USER
        response = publish(app, review, format, user=actor, target=target)
    assert moved.is_dir() and library.is_symlink()
    assert calls == []
    assert response.status_code == 409, response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["detail"]["code"] == "export_destination_changed"
    assert (moved / "movie.mkv").read_bytes() == original_media
    assert list(moved.iterdir()) == [moved / "movie.mkv"]
    with app.accounts.factory() as db:
        assert db.query(MediaItemExportRow).count() == 0
        assert (
            db.query(AuditEventRow).filter_by(action="export.publish", outcome="succeeded").count()
            == 0
        )
    retried = publish(app, review, format, user=actor, target=target)
    assert retried.status_code == 200, retried.text
    with app.accounts.factory() as db:
        row = db.get(MediaItemExportRow, retried.json()["id"])
        assert Path(row.destination_path).read_bytes() == row.content_bytes
