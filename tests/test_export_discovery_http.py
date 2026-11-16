import pytest

from scanner.db import MediaItemExportRow, SavedReviewRow
from tests.multi_user_access_support import ADMIN, PEER_USER, USER
from tests.test_personal_exports_http import app as app
from tests.test_personal_exports_http import publish, seeded_review
from tests.test_personal_exports_http import review_app as review_app


def discovered(app, review, user=USER):
    response = app.send("GET", f"/api/reviews/{review.id}/exports", app.accounts.cookie(user))
    assert response.status_code == 200, response.text
    assert str(app.media) not in response.text
    assert "snapshot" not in response.text
    assert "content_bytes" not in response.text
    return response.json()["publications"]


@pytest.mark.parametrize("format", ["edl", "mcf"])
def test_compatibility_publication_survives_reload_with_identity(app, format):
    review = seeded_review(app, USER, "reload")
    response = app.send(
        "POST",
        f"/api/reviews/{review.id}/{format}",
        app.accounts.unsafe_cookie(USER),
        {"revision": review.revision},
    )
    assert response.status_code == 200, response.text
    fresh = app.send("GET", f"/api/reviews/{review.id}", app.accounts.cookie(USER))
    assert fresh.status_code == 200, fresh.text
    status = fresh.json()["exports"]
    assert status["exports"][format]["present"] is True
    assert status["exports"][format]["current"] is True
    assert status["legacy_exports"][format]["present"] is False
    publication = discovered(app, review)[0]
    assert publication["format"] == format
    assert publication["target"] == "self"
    assert publication["current"] is True
    assert publication["outdated"] is False
    assert publication["publication_revision"] == 1
    assert publication["publication_id"]
    assert response.json()["publication"] == publication
    assert status["publications"] == [publication]


def test_discovery_preserves_users_targets_versions_and_independent_formats(app):
    review = seeded_review(app, USER, "targets")
    personal = publish(app, review, "edl").json()
    other = publish(app, review, "edl", user=ADMIN).json()
    default = publish(app, review, "mcf", user=ADMIN, target="item_default").json()
    assert [row["id"] for row in discovered(app, review)] == [personal["id"]]
    admin_rows = discovered(app, review, ADMIN)
    assert {row["id"]: row["target"] for row in admin_rows} == {
        personal["id"]: "self",
        other["id"]: "self",
        default["id"]: "item_default",
    }
    with app.accounts.factory() as db:
        row = db.get(SavedReviewRow, review.id)
        media_id = row.media_item_id
        row.edl_content_revision += 1
        db.commit()
    admin_rows = discovered(app, review, ADMIN)
    assert all(
        row["outdated"] and not row["current"] for row in admin_rows if row["format"] == "edl"
    )
    assert all(
        row["current"] and not row["outdated"] for row in admin_rows if row["format"] == "mcf"
    )
    version = f"/api/exports?media_item_id={media_id}&media_source_id="
    response = app.send("GET", version, app.accounts.cookie(ADMIN))
    assert response.status_code == 200, response.text
    assert response.json()["publications"] == admin_rows
    empty = app.send("GET", version + "other-version", app.accounts.cookie(ADMIN))
    assert empty.status_code == 200, empty.text
    assert empty.json()["publications"] == []
    deleted = app.send(
        "DELETE",
        f"/api/exports/{personal['id']}?revision=1&publication_id={personal['publication_id']}",
        app.accounts.unsafe_cookie(USER),
    )
    assert deleted.status_code == 204, deleted.text
    assert discovered(app, review) == []
    assert {row["id"] for row in discovered(app, review, ADMIN)} == {other["id"], default["id"]}
    with app.accounts.factory() as db:
        assert db.get(MediaItemExportRow, personal["id"]) is None


def test_hidden_review_and_unknown_version_discovery_are_absent(app):
    review = seeded_review(app, USER, "hidden")
    assert publish(app, review, "edl").status_code == 200
    for review_id in [review.id, "missing-review"]:
        response = app.send(
            "GET", f"/api/reviews/{review_id}/exports", app.accounts.cookie(PEER_USER)
        )
        assert response.status_code == 404, response.text
    response = app.send("GET", "/api/exports?media_item_id=999999", app.accounts.cookie(USER))
    assert response.status_code == 404, response.text


def test_catalog_item_and_version_status_reload_canonical_publications(app):
    from scanner.http import catalog_library_api, scan_catalog_api

    app.client.app.include_router(catalog_library_api.router)
    app.client.app.include_router(scan_catalog_api.router)
    review = seeded_review(app, USER, "catalog-reload")
    publication = publish(app, review, "edl").json()
    with app.accounts.factory() as db:
        item_id = db.get(SavedReviewRow, review.id).media_item.jellyfin_id
    for suffix in ["", "/exports"]:
        response = app.send(
            "GET", f"/api/library-items/{item_id}{suffix}", app.accounts.cookie(USER)
        )
        assert response.status_code == 200, response.text
        status = response.json() if suffix else response.json()["exports"]
        assert [row["id"] for row in status["publications"]] == [publication["id"]]
        assert status["exports"]["edl"]["current"] is True
        assert status["legacy_exports"]["edl"]["present"] is False


def test_discovery_rechecks_live_capability_and_media_scope(app):
    from scanner.security.access.capability_ids import Capability as C
    from scanner.security.access.overrides import OverrideEffect

    review = seeded_review(app, USER, "revocation")
    personal = publish(app, review, "edl").json()
    default = publish(app, review, "mcf", user=ADMIN, target="item_default").json()
    with app.accounts.factory() as db:
        media_id = db.get(SavedReviewRow, review.id).media_item_id
    app.accounts.set_override(USER, C.EXPORTS_PERSONAL_DOWNLOAD_OWN, OverrideEffect.DENY)
    assert discovered(app, review) == []
    fresh = app.send("GET", f"/api/reviews/{review.id}", app.accounts.cookie(USER))
    assert fresh.status_code == 200, fresh.text
    assert fresh.json()["exports"]["publications"] == []
    assert fresh.json()["exports"]["exports"]["edl"]["present"] is False
    app.accounts.set_override(ADMIN, C.EXPORTS_DEFAULT_PUBLISH, OverrideEffect.DENY)
    assert {row["id"] for row in discovered(app, review, ADMIN)} == {personal["id"]}
    assert (
        app.send("GET", f"/api/exports/{default['id']}", app.accounts.cookie(ADMIN)).status_code
        == 403
    )
    from tests.scan_access_http import LIBRARY_B

    app.limit_to_library(USER, LIBRARY_B)
    hidden = app.send("GET", f"/api/exports?media_item_id={media_id}", app.accounts.cookie(USER))
    absent = app.send("GET", "/api/exports?media_item_id=absent", app.accounts.cookie(USER))
    assert hidden.status_code == absent.status_code == 404
    assert hidden.content == absent.content


def test_two_ordinary_users_discover_only_their_own_format_rows(app):
    first = seeded_review(app, USER, "first-member")
    second = seeded_review(app, PEER_USER, "second-member")
    with app.accounts.factory() as db:
        source = db.get(SavedReviewRow, first.id)
        peer = db.get(SavedReviewRow, second.id)
        peer.media_item_id = source.media_item_id
        peer.memberships[0].scan_id = source.memberships[0].scan_id
        media_id = source.media_item_id
        db.commit()
    personal = publish(app, first, "edl").json()
    peer = publish(app, second, "edl", user=PEER_USER)
    assert peer.status_code == 200, peer.text
    for user, expected in [(USER, personal["id"]), (PEER_USER, peer.json()["id"])]:
        response = app.send(
            "GET", f"/api/exports?media_item_id={media_id}", app.accounts.cookie(user)
        )
        assert response.status_code == 200, response.text
        assert [row["id"] for row in response.json()["publications"]] == [expected]
    response = app.send("GET", f"/api/exports?media_item_id={media_id}", app.accounts.cookie(ADMIN))
    assert response.status_code == 200, response.text
    assert {row["id"] for row in response.json()["publications"]} == {
        personal["id"],
        peer.json()["id"],
    }


def test_review_owner_target_and_default_deletion_are_reloadable(app):
    review = seeded_review(app, USER, "owner-target")
    personal = publish(app, review, "edl", user=ADMIN, target="review_owner").json()
    default = publish(app, review, "edl", user=ADMIN, target="item_default").json()
    rows = discovered(app, review, ADMIN)
    assert {row["id"]: row["target"] for row in rows} == {
        personal["id"]: "review_owner",
        default["id"]: "item_default",
    }
    deleted = app.send(
        "DELETE",
        f"/api/exports/{default['id']}?revision=1&publication_id={default['publication_id']}",
        app.accounts.unsafe_cookie(ADMIN),
    )
    assert deleted.status_code == 204, deleted.text
    assert [row["id"] for row in discovered(app, review, ADMIN)] == [personal["id"]]
    fresh = app.send("GET", f"/api/reviews/{review.id}", app.accounts.cookie(USER))
    assert fresh.status_code == 200, fresh.text
    assert fresh.json()["exports"]["publications"][0]["target"] == "review_owner"
    assert fresh.json()["exports"]["exports"]["edl"]["current"] is True


def test_legacy_status_remains_separate_from_canonical_discovery(app):
    from datetime import UTC, datetime

    from scanner.models import ExportFormat
    from scanner.persistence.rows.db_policies import ExportScope

    review = seeded_review(app, USER, "legacy-status")
    with app.accounts.factory() as db:
        source = db.get(SavedReviewRow, review.id)
        legacy = MediaItemExportRow(
            media_item_id=source.media_item_id,
            media_source_id="",
            format=ExportFormat.EDL,
            scope=ExportScope.LEGACY,
            review_id=source.id,
            exported_format_revision=source.edl_content_revision,
            exported_at=datetime.now(UTC),
        )
        db.add(legacy)
        db.commit()
        legacy_id = legacy.id
    publication = publish(app, review, "edl").json()
    fresh = app.send("GET", f"/api/reviews/{review.id}", app.accounts.cookie(USER))
    assert fresh.status_code == 200, fresh.text
    status = fresh.json()["exports"]
    assert status["legacy_exports"]["edl"]["present"] is True
    assert [row["id"] for row in status["publications"]] == [publication["id"]]
    assert legacy_id != publication["id"]
    assert discovered(app, review) == status["publications"]
