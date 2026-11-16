"""Review writes must use the stored authority behind their provider evidence."""

from contextlib import contextmanager
from dataclasses import replace

import pytest

import scanner.api as root
from scanner import repository
from scanner.db import SavedReviewRow
from scanner.http import (
    catalog_access,
    catalog_targets,
    review_edit_api,
    review_import_routes_api,
    review_target_routes,
    review_targets,
)
from scanner.integrations.jellyfin_user_snapshot import ItemAccess
from scanner.models import JellyfinLibrary, ReviewCreate
from scanner.persistence.repository import review_access
from scanner.persistence.rows.db_auth import AccountSettingsRow
from scanner.security.jellyfin_accounts import reads, store
from scanner.security.jellyfin_accounts.evidence import evidence_cache
from tests.media_access_support import FAMILY, FAMILY_FILM, jellyfin_user
from tests.multi_user_access_support import USER
from tests.scan_access_http import JELLYFIN
from tests.test_jellyfin_account_evidence import ScriptedAdapter
from tests.test_jellyfin_account_evidence import clock as clock
from tests.test_jellyfin_account_evidence import linked as linked
from tests.test_scan_http_write_boundaries import submission_app as submission_app


@pytest.fixture
def review_app(submission_app, clock, monkeypatch):
    served = submission_app
    catalog_targets.jellyfin_libraries.clear()
    monkeypatch.setattr(
        root.JellyfinClient,
        "libraries",
        lambda: [JellyfinLibrary(id=FAMILY, name="Family", locations=[str(served.media)])],
        raising=False,
    )
    for router in (
        review_edit_api.router,
        review_target_routes.router,
        review_import_routes_api.router,
    ):
        served.client.app.include_router(router)

    class FixedDatetime:
        @staticmethod
        def now(_zone):
            return clock()

    for module in (review_access, catalog_access, review_targets, review_target_routes):
        monkeypatch.setattr(module, "datetime", FixedDatetime)
    monkeypatch.setattr(evidence_cache, "adapters", lambda _db: ScriptedAdapter(clock))
    yield served
    catalog_targets.jellyfin_libraries.clear()


def test_canonical_version_discovery_uses_the_request_evidence_clock(review_app):
    from scanner.http import review_export_api

    app = review_app
    app.client.app.include_router(review_export_api.router)
    review = linked_review(app)
    with app.accounts.factory() as db:
        media_id = db.get(SavedReviewRow, review.id).media_item_id
    response = app.send(
        "GET",
        f"/api/exports?media_item_id={media_id}",
        app.accounts.cookie(USER),
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"publications": []}


def linked_review(app):
    item = app.items[FAMILY_FILM]
    with app.accounts.factory() as db:
        media = repository.upsert_media_item(
            db,
            server_id=JELLYFIN,
            jellyfin_id=FAMILY_FILM,
            library_id=FAMILY,
            name=item.name,
            item_type="Movie",
            path=item.path,
        )
        review = repository.create_saved_review(
            db,
            media_item_id=media.id,
            media_source_id=None,
            data=ReviewCreate(name="linked private review"),
            duration_seconds=60,
        )
        row = db.get(SavedReviewRow, review.id)
        row.owner_id = row.original_author_id = USER
        db.commit()
    allowed = app.send("GET", f"/api/reviews/{review.id}", app.accounts.cookie(USER))
    assert allowed.status_code == 200, allowed.text
    return review


@pytest.mark.parametrize("operation", ["rename", "create", "duplicate"])
def test_new_stored_jellyfin_restriction_wins_before_review_write(
    review_app, clock, monkeypatch, operation
):
    app = review_app
    review = linked_review(app)
    original_lock = review_access.write_lock
    changed = []
    locked = []

    class CurrentAdapter(ScriptedAdapter):
        def item_access(self, identity, item_ids):
            answer = super().item_access(identity, item_ids)
            if changed:
                return replace(answer, decisions={item: ItemAccess.RESTRICTED for item in item_ids})
            return answer

    @contextmanager
    def competing_policy_update(db):
        if not changed:
            snapshot = jellyfin_user("Casey")
            restricted = replace(
                snapshot,
                media=replace(snapshot.media, all_libraries=False, library_ids=frozenset()),
            )
            with app.accounts.factory() as other:
                store.apply(other, USER, restricted, reads.begin_read(other, clock))
            changed.append("new restriction committed")
        with original_lock(db):
            locked.append(db.connection().connection.driver_connection.in_transaction)
            yield

    monkeypatch.setattr(evidence_cache, "adapters", lambda _db: CurrentAdapter(clock))
    monkeypatch.setattr(review_access, "write_lock", competing_policy_update)
    with app.accounts.factory() as db:
        before_count = db.query(SavedReviewRow).count()
    if operation == "create":
        response = app.send(
            "POST",
            f"/api/library-items/{FAMILY_FILM}/reviews",
            app.accounts.unsafe_cookie(USER),
            {"name": "must not create"},
        )
    else:
        response = app.send(
            "PATCH" if operation == "rename" else "POST",
            f"/api/reviews/{review.id}" + ("/duplicate" if operation == "duplicate" else ""),
            app.accounts.unsafe_cookie(USER),
            {"revision": 0, "name": "must not mutate"},
        )
    assert changed and all(locked)
    assert response.status_code in {403, 404}, response.text
    fresh = app.send("GET", f"/api/reviews/{review.id}", app.accounts.cookie(USER))
    assert fresh.status_code == 404
    with app.accounts.factory() as db:
        assert db.get(SavedReviewRow, review.id).revision == 0
        assert db.query(SavedReviewRow).count() == before_count


@pytest.mark.parametrize("operation", ["rename", "create", "duplicate"])
@pytest.mark.parametrize("change", ["generation", "fence", "epoch", "continuous"])
def test_review_write_refreshes_ordered_evidence_or_refuses_after_three_attempts(
    review_app, clock, monkeypatch, operation, change
):
    app = review_app
    review = linked_review(app)
    original_lock = review_access.write_lock
    locks = []
    provider_calls = []
    locked = False

    class FreshAdapter(ScriptedAdapter):
        def item_access(self, identity, item_ids):
            assert not locked
            provider_calls.append(len(locks))
            return super().item_access(identity, item_ids)

    @contextmanager
    def change_order(db):
        nonlocal locked
        if not locks or change == "continuous":
            with app.accounts.factory() as other:
                settings = other.get(AccountSettingsRow, 1)
                if change == "generation":
                    settings.jellyfin_read_generation += 1
                elif change == "fence":
                    settings.jellyfin_clock_fence = settings.jellyfin_read_generation
                else:
                    settings.jellyfin_read_epoch = f"{len(locks) + 1:032x}"
                other.commit()
        with original_lock(db):
            locks.append(True)
            locked = True
            try:
                yield
            finally:
                locked = False

    monkeypatch.setattr(evidence_cache, "adapters", lambda _db: FreshAdapter(clock))
    monkeypatch.setattr(review_access, "write_lock", change_order)
    with app.accounts.factory() as db:
        before_count = db.query(SavedReviewRow).count()
    if operation == "create":
        response = app.send(
            "POST",
            f"/api/library-items/{FAMILY_FILM}/reviews",
            app.accounts.unsafe_cookie(USER),
            {"name": "fresh authority"},
        )
    else:
        response = app.send(
            "PATCH" if operation == "rename" else "POST",
            f"/api/reviews/{review.id}" + ("/duplicate" if operation == "duplicate" else ""),
            app.accounts.unsafe_cookie(USER),
            {"revision": 0, "name": "fresh authority"},
        )
    if change == "continuous":
        assert response.status_code == 404, response.text
        assert len(locks) == 3
    else:
        assert response.status_code == (200 if operation == "rename" else 201), response.text
        assert 2 <= len(locks) <= 3
        assert any(call > 0 for call in provider_calls)
    with app.accounts.factory() as db:
        assert db.get(SavedReviewRow, review.id).revision == int(
            operation == "rename" and change != "continuous"
        )
        assert db.query(SavedReviewRow).count() == before_count + int(
            operation != "rename" and change != "continuous"
        )
