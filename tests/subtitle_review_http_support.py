"""Live linked accounts, review inputs, and subtitle publication requests."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

import scanner.api as root
from scanner.db import SavedReviewRow, ScanFindingRow, ScanRow
from scanner.http import review_subtitle_api
from scanner.integrations.jellyfin_catalog import parse_catalog
from scanner.integrations.jellyfin_library_folders import parse_library_folders
from scanner.integrations.jellyfin_user_snapshot import (
    ItemAccess,
    ItemAccessSnapshot,
    JellyfinUserSnapshot,
    MediaPolicy,
)
from scanner.models import (
    JellyfinMediaItem,
    JellyfinMediaVersion,
    McfCategoryFamily,
    ReviewCreate,
    ReviewScanMembership,
)
from scanner.persistence.repository.media_identity_store import (
    bind_review_version,
    observe_sampled_version,
)
from scanner.runtime.media_fingerprints import sampled_media_fingerprint
from scanner.security.access.identity import JellyfinServerId, ProviderIdentity
from scanner.security.jellyfin_accounts.evidence import evidence_cache
from tests.media_access_support import listing
from tests.multi_user_access_support import USER, stand_in_identity
from tests.test_review_access_http import app as app

SUBTITLE_TEXT = (
    b"1\n00:00:01,000 --> 00:00:03,000\nDamn, close the gate.\n\n"
    b"2\n00:00:03,000 --> 00:00:06,000\nThis stays.\n"
)


@pytest.fixture(autouse=True)
def subtitle_publishing_dependencies(app, monkeypatch):
    class Provider:
        def catalog(self, server):
            records = [
                {
                    "Id": item.id,
                    "Name": item.name,
                    "Type": "Movie",
                    "MediaType": "Video",
                    "Path": item.path,
                    "MediaSources": [
                        {"Id": version.id, "Path": version.path} for version in item.versions
                    ],
                }
                for item in app.items.values()
            ]
            return parse_catalog([listing(records)], server, datetime.now(UTC))

        def library_folders(self, server):
            folders = [{"ItemId": "a" * 32, "Locations": [str(app.media)]}]
            return parse_library_folders(folders, server, datetime.now(UTC))

        def item_access(self, identity, item_ids):
            return ItemAccessSnapshot(
                identity, dict.fromkeys(item_ids, ItemAccess.ALLOWED), datetime.now(UTC)
            )

        def close(self):
            pass

    monkeypatch.setattr(evidence_cache, "adapters", lambda _db: Provider())
    monkeypatch.setattr(evidence_cache, "_copies", {})
    monkeypatch.setattr(evidence_cache, "_items", {})
    monkeypatch.setattr(
        review_subtitle_api.JellyfinClient,
        "subtitle_language_tokens",
        lambda _: frozenset({"en", "eng", "fr", "fra", "hi", "hin"}),
    )


def link_publisher(app, user, name):
    snapshot = JellyfinUserSnapshot(
        identity=ProviderIdentity(JellyfinServerId("d" * 32), stand_in_identity(user).user_id),
        name=name,
        avatar_tag=None,
        is_administrator=False,
        is_disabled=False,
        media=MediaPolicy(True, frozenset(), None, None, (), (), frozenset()),
        verified_at=datetime.now(UTC),
    )
    app.accounts.link_jellyfin_user(user, snapshot)


def seed_subtitle_review(app, *, linked=True):
    if linked:
        link_publisher(app, USER, "Sam")
    scan_id, _queue = app.seed_scan(USER, "Subtitle movie - alternate", status="complete")
    with app.accounts.factory() as db:
        scan = db.get(ScanRow, scan_id)
        selected = Path(scan.source_path)
        primary = app.file("Subtitle movie - primary.mkv")
        source = selected.with_suffix(".en.forced.srt")
        source.write_bytes(SUBTITLE_TEXT)
        primary_subtitle = primary.with_suffix(".en.srt")
        primary_subtitle.write_bytes(SUBTITLE_TEXT)

        def stream(path, title, forced=False):
            return {
                "Type": "Subtitle",
                "Index": 5,
                "Codec": "subrip",
                "IsExternal": True,
                "IsForced": forced,
                "DisplayTitle": title,
                "Language": "eng",
                "Path": str(path),
            }

        item_id = scan.media_item.jellyfin_id
        app.items[item_id] = JellyfinMediaItem(
            id=item_id,
            library_id=scan.media_item.library_id,
            name="Subtitle movie",
            item_type="Movie",
            path=str(primary),
            duration_seconds=60,
            media_streams=[stream(primary_subtitle, "Wrong version")],
            versions=[
                JellyfinMediaVersion(id="primary", path=str(primary)),
                JellyfinMediaVersion(
                    id="alternate",
                    path=str(selected),
                    subtitle_streams=[stream(source, "English forced", True)],
                ),
            ],
        )
        scan.duration_seconds = 60
        scan.media_source_id = "alternate"
        scan.scan_audio = True
        db.add(
            ScanFindingRow(
                scan_id=scan_id,
                source="audio",
                category=McfCategoryFamily.LANGUAGE,
                subcategory="swearing",
                label="damn",
                start_seconds=1.25,
                end_seconds=1.75,
                selected=True,
                severity="medium",
                edit_action="mute",
            )
        )
        db.commit()
        review = root.create_saved_review(
            db,
            media_item_id=scan.media_item_id,
            media_source_id="alternate",
            data=ReviewCreate(
                name="Subtitle review",
                memberships=[ReviewScanMembership(scan_id=scan_id, role="audio")],
            ),
            duration_seconds=60,
        )
        row = db.get(SavedReviewRow, review.id)
        row.owner_id = row.original_author_id = USER
        db.commit()
    return review, source


def subtitle_request(app, review, endpoint, body, *, user=USER):
    return app.send(
        "POST",
        f"/api/reviews/{review.id}/subtitles/{endpoint}",
        app.accounts.unsafe_cookie(user),
        {"revision": review.revision} | body,
    )


def pin_review_file(app, review):
    with app.accounts.factory() as db:
        row = db.get(SavedReviewRow, review.id)
        scan = row.memberships[0].scan
        path = Path(scan.resolved_local_path)
        version_id = observe_sampled_version(
            db,
            media=row.media_item,
            media_source_id=scan.media_source_id,
            source_path=scan.source_path,
            fingerprint=sampled_media_fingerprint(path),
            scan_id=scan.id,
        )
        bind_review_version(db, row.id, version_id)
        db.commit()
    return path


def selected_source(app, review, *, user=USER):
    listed = subtitle_request(app, review, "sources", {}, user=user)
    assert listed.status_code == 200, listed.text
    return {"source_id": listed.json()["sources"][0]["id"]}


def preview_for_publication(app, review, *, user=USER):
    selected = selected_source(app, review, user=user)
    preview = subtitle_request(app, review, "preview", selected, user=user)
    assert preview.status_code == 200, preview.text
    return selected | {"preview_token": preview.json()["preview_token"]}
