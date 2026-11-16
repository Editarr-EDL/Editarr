import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine

from scanner import api, repository
from scanner.db import MediaItemExportRow, SavedReviewRow
from scanner.http import catalog_access, catalog_library_api, scan_catalog_api
from scanner.integrations.jellyfin import JellyfinNotFound
from scanner.models import ExportFormat, JellyfinLibrary
from scanner.persistence.repository import export_discovery, review_access
from scanner.persistence.rows.db_policies import ExportScope
from scanner.security.scan_jobs import media as scan_media
from tests.catalog_access_http import FakeJellyfin, serve_catalog
from tests.media_access_support import FAMILY, FAMILY_FILM, LATE_SHOW, MATURE
from tests.multi_user_access_support import ADMIN, OWNER, PEER_USER, USER, live_accounts
from tests.scan_access_http import LIBRARY_A, LIBRARY_B
from tests.test_personal_exports_http import app as app
from tests.test_personal_exports_http import publish, seeded_review
from tests.test_personal_exports_http import review_app as review_app


@pytest.fixture
def catalog(monkeypatch, tmp_path):
    accounts = live_accounts(tmp_path)
    app = serve_catalog(monkeypatch, accounts, tmp_path)

    class FixedDatetime:
        @staticmethod
        def now(_zone=None):
            return app.clock()

    monkeypatch.setattr(review_access, "datetime", FixedDatetime)
    monkeypatch.setattr(scan_media, "evidence_cache", catalog_access.evidence_cache)
    original = FakeJellyfin.item

    def item(client, item_id):
        try:
            return original(client, item_id)
        except KeyError as exc:
            raise JellyfinNotFound("Item not found") from exc

    monkeypatch.setattr(FakeJellyfin, "item", item)
    try:
        yield app
    finally:
        app.client.close()
        accounts.close()


def media_row(app, server_id, item_id, path, *, item_type="Episode", library=None):
    with app.accounts.factory() as db:
        row = repository.upsert_media_item(
            db,
            server_id=server_id,
            jellyfin_id=item_id,
            library_id=library,
            item_type=item_type,
            name=f"synthetic {item_id}",
            path=path,
        )
        db.commit()
        return row.id


def canonical_row(app, media_item_id, path):
    from scanner.persistence.repository.export_publication import export_identity

    with app.accounts.factory() as db:
        row = MediaItemExportRow(
            media_item_id=media_item_id,
            media_source_id="",
            format=ExportFormat.EDL,
            scope=ExportScope.PERSONAL,
            target_user_id=OWNER,
            target_export_id=export_identity(db, OWNER).value,
            actor_id=OWNER,
            policy_owner_id=OWNER,
            original_author_id=OWNER,
            policy_revision=0,
            exported_at=datetime.now(UTC),
            content_bytes=b"0.0\t1.0\t0\n",
            destination_path=f"{path}.edl",
            publication_revision=1,
            snapshot_json=json.dumps(
                {"publication_id": str(uuid4()), "media_path": path, "target": "self"}
            ),
        )
        db.add(row)
        db.commit()
        return row.id


def test_scanned_catalog_page_does_not_acquire_a_sqlite_writer(catalog):
    for item_id, name in [
        (FAMILY_FILM, "Synthetic Family Film"),
        (LATE_SHOW, "Synthetic Late Show"),
        (MATURE, "Synthetic Mature Film"),
    ]:
        media_row(
            catalog,
            "http://jellyfin.test",
            item_id,
            f"/jellyfin/family/{name}.mkv",
            item_type="Movie",
            library=FAMILY,
        )
    statements = []

    def record(_connection, _cursor, statement, *_args):
        if statement.strip().upper().startswith("BEGIN IMMEDIATE"):
            statements.append(statement)

    event.listen(Engine, "before_cursor_execute", record)
    try:
        response = catalog.get(OWNER, f"/api/libraries/{FAMILY}/items")
        assert response.status_code == 200, response.text
        assert len(response.json()["items"]) == 3
        assert statements == []
    finally:
        event.remove(Engine, "before_cursor_execute", record)


@pytest.mark.parametrize("scanned", [False, True])
def test_jellyfin_status_hides_hidden_and_absent_ids_equally(catalog, scanned):
    if scanned:
        media_row(
            catalog,
            "http://jellyfin.test",
            MATURE,
            "/jellyfin/family/Synthetic Mature Film.mkv",
            item_type="Movie",
            library=FAMILY,
        )
    hidden = catalog.get(USER, f"/api/library-items/{MATURE}/exports")
    absent = catalog.get(USER, f"/api/library-items/{'9' * 32}/exports")
    assert hidden.status_code == absent.status_code == 404
    assert hidden.content == absent.content
    assert catalog.get(USER, f"/api/library-items/{FAMILY_FILM}/exports").status_code == 200


@pytest.mark.parametrize("denied", [True, False])
def test_catalog_discovery_refusal_keeps_legacy_status(catalog, monkeypatch, denied):
    from scanner.models import ExportStatus, ExportSummary
    from scanner.persistence.repository import export_discovery
    from scanner.security.access.api_shapes import AccessErrorCode
    from scanner.security.access.decisions import AccessDenied

    path = "/jellyfin/family/Synthetic Family Film.mkv"
    media_id = media_row(
        catalog, "http://jellyfin.test", FAMILY_FILM, path, item_type="Movie", library=FAMILY
    )
    canonical_row(catalog, media_id, path)
    status = ExportStatus(
        exports={
            ExportFormat.EDL: ExportSummary(format=ExportFormat.EDL),
            ExportFormat.MCF: ExportSummary(format=ExportFormat.MCF, present=True),
        }
    )
    monkeypatch.setattr(
        "scanner.api.library_item_export_statuses", lambda *args, **kwargs: {FAMILY_FILM: status}
    )

    def unavailable(*args, **kwargs):
        raise AccessDenied(AccessErrorCode.NOT_FOUND)

    if denied:
        monkeypatch.setattr(export_discovery, "discover_publications", unavailable)
    else:
        with catalog.accounts.factory() as db:
            from scanner.db import MediaItemRow

            db.get(MediaItemRow, media_id).item_type = "Episode"
            db.commit()
    response = catalog.get(OWNER, f"/api/libraries/{FAMILY}/items")
    assert response.status_code == 200, response.text
    card = next(item for item in response.json()["items"] if item["id"] == FAMILY_FILM)
    assert card["exports"]["publications"] == []
    assert card["exports"]["exports"]["mcf"]["present"] is True


@pytest.mark.parametrize("replacement", [None, False, True])
def test_catalog_snapshot_survives_delete_and_row_id_reuse(app, monkeypatch, replacement):
    app.client.app.include_router(catalog_library_api.router)
    app.client.app.include_router(scan_catalog_api.router)
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
    own = seeded_review(app, USER, "public/visible-snapshot")
    peer = seeded_review(app, PEER_USER, "private/private-peer-snapshot")
    app.limit_to_library(USER, LIBRARY_A)
    with app.accounts.factory() as db:
        hidden = db.get(SavedReviewRow, peer.id)
        hidden.media_item.library_id = LIBRARY_B
        peer_media_id = hidden.media_item_id
        peer_item_id = hidden.media_item.jellyfin_id
        app.items[peer_item_id].library_id = LIBRARY_B
        db.commit()
    for route in (
        f"/api/exports?media_item_id={peer_media_id}",
        f"/api/library-items/{peer_item_id}",
        f"/api/reviews/{peer.id}",
    ):
        hidden = app.send("GET", route, app.accounts.cookie(USER))
        assert hidden.status_code == 404, hidden.text
    created = publish(app, own, "edl")
    assert created.status_code == 200, created.text
    publication = created.json()
    with app.accounts.factory() as db:
        item_id = db.get(SavedReviewRow, own.id).media_item.jellyfin_id
        peer_scan = db.get(SavedReviewRow, peer.id).memberships[0].scan_id
    baseline = app.send("GET", f"/api/library-items/{item_id}", app.accounts.cookie(USER))
    assert baseline.status_code == 200, baseline.text
    original = export_discovery.discover_publications
    raced = False

    def interleave(*args, **kwargs):
        nonlocal raced
        result = original(*args, **kwargs)
        if result and not raced and replacement is not None:
            raced = True
            removed = app.send(
                "DELETE",
                f"/api/exports/{publication['id']}?revision=1"
                f"&publication_id={publication['publication_id']}",
                app.accounts.unsafe_cookie(USER),
            )
            assert removed.status_code == 204, removed.text
            if replacement:
                replaced = publish(app, peer, "mcf", user=PEER_USER)
                assert replaced.status_code == 200, replaced.text
                assert replaced.json()["id"] == publication["id"]
        return result

    monkeypatch.setattr(export_discovery, "discover_publications", interleave)
    response = app.send("GET", f"/api/library-items/{item_id}", app.accounts.cookie(USER))
    assert raced == (replacement is not None)
    assert response.status_code == 200, response.text
    status = response.json()["exports"]
    assert status == baseline.json()["exports"]
    assert status["exports"]["mcf"]["present"] is False
    assert status["exports"]["edl"]["present"] is True
    assert status["publications"][0]["publication_id"] == publication["publication_id"]
    assert peer_scan not in response.text
    assert peer.id not in response.text
    with app.accounts.factory() as db:
        remaining = db.get(MediaItemExportRow, publication["id"])
        assert (remaining is not None) == (replacement is not False)


@pytest.mark.parametrize("format", ["edl", "mcf"])
def test_catalog_redacts_source_review_like_direct_export_status(app, format):
    from scanner.security.access.capability_ids import Capability as C
    from scanner.security.access.overrides import OverrideEffect

    app.client.app.include_router(catalog_library_api.router)
    review = seeded_review(app, USER, "source-now-private")
    result = publish(app, review, format, user=ADMIN)
    assert result.status_code == 200, result.text
    with app.accounts.factory() as db:
        item_id = db.get(SavedReviewRow, review.id).media_item.jellyfin_id
    app.accounts.set_override(ADMIN, C.REVIEWS_VIEW_OTHER, OverrideEffect.DENY)
    hidden = app.send("GET", f"/api/reviews/{review.id}", app.accounts.cookie(ADMIN))
    assert hidden.status_code == 404, hidden.text
    direct = app.send("GET", f"/api/exports/{result.json()['id']}", app.accounts.cookie(ADMIN))
    assert direct.status_code == 200, direct.text
    assert direct.json()["review_id"] is None
    catalog = app.send("GET", f"/api/library-items/{item_id}", app.accounts.cookie(ADMIN))
    assert catalog.status_code == 200, catalog.text
    status = catalog.json()["exports"]
    publication = status["publications"][0]
    assert publication == direct.json()
    for projected in (publication, status["exports"][format]):
        assert projected["review_id"] is None
        assert projected["exported_format_revision"] is None
    assert review.id not in catalog.text
