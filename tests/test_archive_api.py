"""The archive lists records from removed sources read-only, and only the archive reads them."""

from datetime import datetime, timedelta

import pytest

import scanner.api  # noqa: F401  # loads the HTTP package in import order
from scanner.db import (
    ArchivedSource,
    MediaItemExportRow,
    MediaItemRow,
    ReviewMcfDocumentRow,
    SavedReviewRow,
    ScanRow,
)
from scanner.http import archive_api, scan_catalog_api
from scanner.models import ExportFormat
from scanner.persistence.archive import archived_reads
from scanner.persistence.repository.archive_records import episode_from_path
from scanner.persistence.rows.db_policies import ExportScope
from scanner.security.access.capability_ids import Capability as C
from scanner.security.access.overrides import OverrideEffect
from tests.multi_user_access_http import serve_access_fixture
from tests.multi_user_access_support import ADMIN, OWNER, USER, live_accounts


def _item(item_id, source, name, item_type, **values):
    servers = {ArchivedSource.MANUAL_FILES: "editarr-manual", None: "jellyfin-server"}
    server = servers.get(source, f"{source}:http://{source}.test")
    return MediaItemRow(
        id=item_id,
        server_id=server,
        jellyfin_id=item_id,
        item_type=item_type,
        name=name,
        path=f"/media/{name}.mkv",
        archived_source=source,
        **values,
    )


def _scan(scan_id, item_id, number):
    return ScanRow(
        id=scan_id,
        media_item_id=item_id,
        scan_number=number,
        name=f"Scan {number}",
        source_path="/media/file.mkv",
        resolved_local_path="/media/file.mkv",
        status="complete",
    )


@pytest.fixture
def archive(monkeypatch, tmp_path):
    accounts = live_accounts(tmp_path)
    with accounts.factory() as db:
        db.add_all(
            [
                _item("movie", ArchivedSource.RADARR, "Archived Movie", "Movie"),
                _item("episode", ArchivedSource.SONARR, "Pilot", "Episode"),
                _item("home", ArchivedSource.MANUAL_FILES, "Home Video", "Video"),
                _item("live", None, "Live Movie", "Movie"),
                _scan("movie-scan", "movie", 1),
                _scan("episode-scan", "episode", 1),
                _scan("live-scan", "live", 1),
                SavedReviewRow(
                    id="episode-review",
                    media_item_id="episode",
                    name="Pilot review",
                    owner_id=OWNER,
                    original_author_id=OWNER,
                    mcf_document=ReviewMcfDocumentRow(
                        title="Pilot", season=1, episode=2, material_start_seconds=0
                    ),
                ),
                MediaItemExportRow(
                    media_item_id="movie", format=ExportFormat.EDL, scope=ExportScope.LEGACY
                ),
            ]
        )
        db.commit()
    client = serve_access_fixture(monkeypatch, accounts)
    client.app.include_router(archive_api.router)
    client.app.include_router(scan_catalog_api.router)
    yield client, accounts
    client.close()
    accounts.close()


def test_owner_sees_each_archived_item_with_its_source_records(archive):
    client, accounts = archive
    response = client.get("/api/archive", headers=accounts.cookie(OWNER))
    assert response.status_code == 200
    page = response.json()
    assert (page["total"], page["offset"], page["limit"]) == (3, 0, 50)
    items = {item["id"]: item for item in page["items"]}
    assert set(items) == {"movie", "episode", "home"}
    assert (items["home"]["source"], items["home"]["title"]) == ("manual_files", "Home Video")
    movie, episode = items["movie"], items["episode"]
    assert (movie["source"], movie["title"], movie["path"]) == (
        "radarr",
        "Archived Movie",
        "/media/Archived Movie.mkv",
    )
    assert [scan["id"] for scan in movie["scans"]] == ["movie-scan"]
    assert [(export["format"], export["scope"]) for export in movie["exports"]] == [
        ("edl", "legacy")
    ]
    assert (episode["source"], episode["season"], episode["episode"]) == ("sonarr", 1, 2)
    assert [(review["name"], review["owner"]) for review in episode["reviews"]] == [
        ("Pilot review", "Owner")
    ]


def test_an_admin_may_read_the_archive_and_an_ordinary_user_may_not(archive):
    client, accounts = archive
    assert client.get("/api/archive", headers=accounts.cookie(ADMIN)).status_code == 200
    refused = client.get("/api/archive", headers=accounts.cookie(USER))
    assert refused.status_code == 403
    assert "Archived Movie" not in refused.text


def test_live_reads_never_return_archived_rows(archive):
    client, accounts = archive
    owner = accounts.cookie(OWNER)
    for item, expected in (("movie", []), ("episode", []), ("home", []), ("live", ["live-scan"])):
        response = client.get(f"/api/library-items/{item}/scans", headers=owner)
        assert [scan["id"] for scan in response.json()] == expected
    with accounts.factory() as db:
        assert [item.id for item in db.query(MediaItemRow)] == ["live"]
        assert db.get(ScanRow, "movie-scan") is None
        assert db.get(SavedReviewRow, "episode-review") is None
        assert db.query(MediaItemExportRow).count() == 0
        with archived_reads(db):
            assert db.get(ScanRow, "movie-scan").media_item.name == "Archived Movie"
        assert db.get(ScanRow, "episode-scan") is None


def test_the_archive_pages_newest_first(archive):
    client, accounts = archive
    start = datetime(2026, 1, 1)
    with accounts.factory() as db, archived_reads(db):
        for index in range(5):
            db.add(
                _item(
                    f"extra-{index}",
                    ArchivedSource.RADARR,
                    f"Extra {index}",
                    "Movie",
                    updated_at=start + timedelta(days=index),
                )
            )
        for row in db.query(MediaItemRow).filter(MediaItemRow.id.in_(["movie", "episode", "home"])):
            row.updated_at = start - timedelta(days=1)
        db.commit()
    owner = accounts.cookie(OWNER)

    def page(**params):
        response = client.get("/api/archive", params=params, headers=owner)
        assert response.status_code == 200
        body = response.json()
        return body["total"], [item["id"] for item in body["items"]]

    assert page(limit=3) == (8, ["extra-4", "extra-3", "extra-2"])
    assert page(limit=3, offset=3) == (8, ["extra-1", "extra-0", "episode"])
    assert page(limit=3, offset=6) == (8, ["home", "movie"])
    assert page(offset=8) == (8, [])
    for params in ({"limit": 0}, {"limit": 101}, {"offset": -1}):
        assert client.get("/api/archive", params=params, headers=owner).status_code == 422


def test_paths_are_null_for_a_caller_without_media_paths_view(archive):
    client, accounts = archive
    accounts.set_override(ADMIN, C.MEDIA_PATHS_VIEW, OverrideEffect.DENY)
    items = client.get("/api/archive", headers=accounts.cookie(ADMIN)).json()["items"]
    assert [item["path"] for item in items] == [None, None, None]
    owner = client.get("/api/archive", headers=accounts.cookie(OWNER)).json()["items"]
    assert all(item["path"] for item in owner)


def test_api_keys_reach_the_archive_like_any_other_gated_read(archive):
    client, accounts = archive
    for user in (OWNER, ADMIN):
        response = client.get("/api/archive", headers=accounts.key(user))
        assert response.status_code == 200
        assert response.json()["total"] == 3
    refused = client.get("/api/archive", headers=accounts.key(USER))
    assert (refused.status_code, refused.json()["code"]) == (403, "capability_denied")
    assert "Archived Movie" not in refused.text
    anonymous = client.get("/api/archive")
    assert anonymous.status_code == 401
    assert "Archived Movie" not in anonymous.text


_SONARR_SERIES = "/data/tv/Synthetic Sonarr series"


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        (
            f"{_SONARR_SERIES}/Season 1/Synthetic Sonarr series - S01E02 - The Second Hour.mkv",
            ("Synthetic Sonarr series", 1, 2, None),
        ),
        (
            "/data/tv/Other Sonarr show/Season 02/S02E06 Finale.mkv",
            ("Other Sonarr show", 2, 6, None),
        ),
        ("/data/tv/Old Show/Specials/Old Show - S00E01 - Pilot.mkv", ("Old Show", 0, 1, None)),
        ("/data/tv/Old Show/specials/S00E02.mkv", ("Old Show", 0, 2, None)),
        ("/data/tv/Flat Show/Flat.Show.s03e11.1080p.WEB-DL.mkv", (None, 3, 11, None)),
        ("/data/tv/Show - S01E02.mkv", ("Show", 1, 2, None)),
        ("/tv/Custom Show/S01/Custom Show - S01E02.mkv", ("Custom Show", 1, 2, None)),
        ("/tv/Custom Show/S01/S01E02.mkv", (None, 1, 2, None)),
        (r"D:\TV\Windows Show\Season 4\Windows Show - S04E10.mkv", ("Windows Show", 4, 10, None)),
        (r"D:\Season 4\S04E10.mkv", (None, 4, 10, None)),
        (r"\\nas\tv\Show - S01E02.mkv", ("Show", 1, 2, None)),
        (r"\\nas\tv\Season 1\S01E02.mkv", (None, 1, 2, None)),
        (r"\\nas\tv\UNC Show\Season 1\S01E02.mkv", ("UNC Show", 1, 2, None)),
        ("//nas/tv/UNC Show/Season 1/S01E02.mkv", ("UNC Show", 1, 2, None)),
        ("/mnt/editarr/tv/Mapped Show/Season 03/S03E04.mkv", ("Mapped Show", 3, 4, None)),
        ("Z:/tv/Mapped Show/Season 3/Mapped Show - S03E04.mkv", ("Mapped Show", 3, 4, None)),
        ("/tv/Double/Season 1/Double - S01E01E02 - Two Parts.mkv", ("Double", 1, 1, 2)),
        ("/tv/Double/Season 1/Double - S01E01-E03.mkv", ("Double", 1, 1, 3)),
        ("/tv/Double/Season 1/Double - S01E05E03.mkv", ("Double", 1, 5, None)),
        ("/tv/Double/Season 1/Double - S01E05-E05.mkv", ("Double", 1, 5, None)),
        ("/data/tv/Unnumbered Show/Season 1/Finale.mkv", ("Unnumbered Show", None, None, None)),
        ("/data/tv/Release 2024/Show.2024.1080p.mkv", (None, None, None, None)),
        ("S01E01.mkv", (None, 1, 1, None)),
        (None, (None, None, None, None)),
    ],
)
def test_episode_numbering_comes_from_a_standard_sonarr_path(path, expected):
    assert tuple(episode_from_path(path)) == expected


def test_archived_episodes_show_their_series_and_number_from_recorded_data(archive):
    client, accounts = archive
    with accounts.factory() as db, archived_reads(db):
        db.add_all(
            [
                _item("finale", ArchivedSource.SONARR, "Finale", "Episode"),
                _item("loose", ArchivedSource.SONARR, "Loose", "Episode"),
                _scan("finale-scan", "finale", 1),
            ]
        )
        db.flush()
        db.get(
            MediaItemRow, "finale"
        ).path = "/data/tv/Other Sonarr show/Season 2/Other Sonarr show - S02E06E07 - Finale.mkv"
        db.get(
            MediaItemRow, "episode"
        ).path = f"{_SONARR_SERIES}/Season 1/Synthetic Sonarr series - S01E09 - Pilot.mkv"
        db.get(MediaItemRow, "loose").path = "/data/tv/unsorted/loose.mkv"
        db.get(MediaItemRow, "home").path = "/data/home/Season 1/Home Video - S01E03.mkv"
        db.commit()

    def numbering(user):
        return {
            item["id"]: (item["series"], item["season"], item["episode"], item["last_episode"])
            for item in client.get("/api/archive", headers=accounts.cookie(user)).json()["items"]
        }

    assert numbering(OWNER) == {
        "movie": (None, None, None, None),
        "episode": ("Synthetic Sonarr series", 1, 2, None),
        "finale": ("Other Sonarr show", 2, 6, 7),
        "loose": (None, None, None, None),
        "home": (None, None, None, None),
    }
    accounts.set_override(ADMIN, C.MEDIA_PATHS_VIEW, OverrideEffect.DENY)
    assert numbering(ADMIN) == {
        "movie": (None, None, None, None),
        "episode": (None, 1, 2, None),
        "finale": (None, 2, 6, 7),
        "loose": (None, None, None, None),
        "home": (None, None, None, None),
    }
