"""Handler authorization for legacy sidecar deletion and item-scan listings,
through the real access middleware."""

from pathlib import Path

import pytest

from scanner.db import MediaItemExportRow, SavedReviewRow
from scanner.persistence.repository.repository_review_exports import stage_export_scans
from scanner.persistence.rows.db_policies import ExportScope
from tests.multi_user_access_support import ADMIN, USER
from tests.test_personal_exports_http import app as app
from tests.test_personal_exports_http import publish, seeded_review
from tests.test_personal_exports_http import review_app as review_app


def code(response):
    return response.status_code, response.json().get("code")


@pytest.fixture
def served(app):
    from scanner.http import scan_catalog_api

    app.client.app.include_router(scan_catalog_api.router)
    return app


def legacy_sidecar(app, review) -> tuple[str, Path]:
    """Store an adjacent pre-migration EDL beside the review's media file."""
    with app.accounts.factory() as db:
        row = db.get(SavedReviewRow, review.id)
        media = row.media_item
        sidecar = Path(row.memberships[0].scan.resolved_local_path).with_suffix(".edl")
        sidecar.write_text("0\t1\t0\n")
        export = MediaItemExportRow(
            media_item_id=media.id,
            media_source_id=row.media_source_id,
            format="edl",
            scope=ExportScope.LEGACY,
        )
        db.add(export)
        db.flush()
        stage_export_scans(db, export.id, row.memberships[:1])
        db.commit()
        return media.jellyfin_id, sidecar


def test_only_item_default_deleters_remove_a_legacy_sidecar(served):
    review = seeded_review(served, USER, "legacy")
    personal = publish(served, review, "edl").json()
    item_id, sidecar = legacy_sidecar(served, review)
    path = f"/api/library-items/{item_id}/exports/edl"

    refused = served.send("DELETE", path, served.accounts.unsafe_cookie(USER))
    assert code(refused) == (403, "capability_denied")
    assert sidecar.exists()

    removed = served.send("DELETE", path, served.accounts.unsafe_cookie(ADMIN))
    assert removed.status_code == 204, removed.text
    assert not sidecar.exists()
    with served.accounts.factory() as db:
        assert db.get(MediaItemExportRow, personal["id"]) is not None
    again = served.send("DELETE", path, served.accounts.unsafe_cookie(ADMIN))
    assert code(again)[0] == 404


def test_item_scans_ignore_a_client_source_path(served):
    review = seeded_review(served, USER, "scans")
    with served.accounts.factory() as db:
        row = db.get(SavedReviewRow, review.id)
        item_id = row.media_item.jellyfin_id
        scan_path = row.memberships[0].scan.resolved_local_path
        scan_id = row.memberships[0].scan.id
    scans = f"/api/library-items/{item_id}/scans"
    unrelated = "/api/library-items/unrelated-item/scans"

    for account in (USER, ADMIN):
        cookie = served.accounts.cookie(account)
        listed = served.send("GET", scans, cookie)
        assert listed.status_code == 200, listed.text
        assert [scan["id"] for scan in listed.json()] == [scan_id]
        steered = served.send("GET", f"{scans}?source_path=/remote/a.mkv", cookie)
        assert steered.status_code == 200, steered.text
        assert steered.json() == listed.json()
        borrowed = served.send("GET", f"{unrelated}?source_path={scan_path}", cookie)
        assert borrowed.status_code == 200, borrowed.text
        assert borrowed.json() == []
