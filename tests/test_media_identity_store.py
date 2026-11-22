"""Stable title matching and verified byte-level version separation."""

import pytest

from scanner.db import MediaItemRow, SavedReviewRow, ScanRow
from scanner.modeling.media_identity import EpisodeNumber, MediaIdentity, ProviderIdentifier
from scanner.persistence.repository import media_identity_store as store
from tests.review_repository_support import review_db


@pytest.fixture
def db():
    with review_db() as session:
        yield session


def _item(db, item_id, *, item_type="Movie", server_id="server"):
    row = MediaItemRow(
        id=item_id, server_id=server_id, jellyfin_id=item_id, item_type=item_type, name=item_id
    )
    db.add(row)
    db.flush()
    return row


def _movie(**providers):
    return MediaIdentity(
        "movie",
        tuple(
            ProviderIdentifier(provider, "movie", number, "test")
            for provider, number in providers.items()
        ),
    )


def test_matches_typed_aliases_refuses_conflicts_and_keeps_original_binding(db):
    first = _item(db, "old")
    stable_id = store.observe_title(db, first, _movie(tmdb="777", tvdb="4"))
    same = _item(db, "replacement")
    assert store.observe_title(db, same, _movie(tmdb="0777")) == stable_id
    assert store.find_title_id(db, server_id="server", jellyfin_id="old") == stable_id
    assert {item.id for item in store.title_media_items(db, stable_id)} == {"old", "replacement"}
    assert store.find_title_id(db, server_id="another", jellyfin_id="old") is None
    assert store.identity_matches_title(db, stable_id, _movie(tvdb="4"))
    assert not store.identity_matches_title(db, stable_id, _movie(tmdb="777", tvdb="9"))
    conflicting = _item(db, "conflicting")
    assert store.observe_title(db, conflicting, _movie(tmdb="777", tvdb="9")) != stable_id
    assert store.observe_title(db, first, _movie(tmdb="999")) == stable_id
    assert not store.identity_matches_title(db, stable_id, _movie(tmdb="999"))


def test_episodes_keep_separate_series_namespaces_and_ranges(db):
    def identity(*numbers):
        return MediaIdentity(
            "episode",
            (),
            series_identifiers=(ProviderIdentifier("tvdb", "series", "88", "test"),),
            episode_numbers=tuple(EpisodeNumber("aired", 1, number) for number in numbers),
        )

    first = store.observe_title(db, _item(db, "one", item_type="Episode"), identity(1))
    same = store.observe_title(db, _item(db, "same", item_type="Episode"), identity(1))
    second = store.observe_title(db, _item(db, "two", item_type="Episode"), identity(2))
    double = store.observe_title(db, _item(db, "double", item_type="Episode"), identity(1, 2))
    assert first == same
    assert len({first, second, double}) == 3
    assert not store.identity_matches_title(db, first, identity())


def test_same_jellyfin_source_can_have_multiple_versions_and_original_pins(db):
    media = db.get(MediaItemRow, "media")
    scan = db.get(ScanRow, "scan")
    scan.media_source_id = "reused-source"
    review = SavedReviewRow(
        id="review", media_item_id="media", media_source_id="reused-source", name="Original"
    )
    db.add(review)

    kwargs = dict(
        media=media,
        media_source_id="reused-source",
        source_path="/same.mkv",
        resolved_path="/mnt/same.mkv",
        video_hash="identical-sampled-hash",
    )
    assert store.observe_version(db, **kwargs) is None
    first = store.observe_version(db, content_sha256="a" * 64, scan_id=scan.id, **kwargs)
    assert first is not None
    store.bind_review_version(db, review.id, first)
    other = store.observe_version(db, content_sha256="b" * 64, **kwargs)
    assert other is not None and other != first
    assert store.observe_version(db, content_sha256="b" * 64, **kwargs) == other
    db.commit()

    assert store.scan_version_id(db, "scan") == store.review_version_id(db, "review") == first
    assert {item.id for item in store.title_versions(db, store.observe_title(db, media))} == {
        first,
        other,
    }
    assert store.version_locators(db, other)[0].media_source_id == "reused-source"
    with pytest.raises(ValueError, match="already bound"):
        store.bind_review_version(db, "review", other)
    assert store.review_version_id(db, "review") == first


def test_proven_bytes_can_follow_a_bounded_catalog_replacement(db):
    old = _item(db, "old")
    new = _item(db, "replacement")
    store.observe_title(db, old, _movie(tmdb="42"))
    store.observe_title(db, new, _movie(tmdb="42"))
    digest = "d" * 64
    earlier = store.observe_version(
        db, media=old, media_source_id="old-source", source_path="/old.mkv", content_sha256=digest
    )
    later = store.observe_version(
        db, media=new, media_source_id="new-source", source_path="/new.mkv", content_sha256=digest
    )
    assert earlier == later
    assert {locator.media_item_id for locator in store.version_locators(db, earlier)} == {
        "old",
        "replacement",
    }
