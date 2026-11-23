"""MU-03: owner, author, actor, target, and policy owner are distinct validated storage."""

import hashlib
import sqlite3

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from scanner import repository
from scanner.db import ArchivedSource, MediaItemExportRow, MediaItemRow, SavedReviewRow, ScanRow
from scanner.models import ExportFormat, ReviewDuplicate
from scanner.persistence.archive import archived_reads
from scanner.persistence.migrations import adoption
from scanner.persistence.migrations.runner import migrate_fresh_database
from scanner.persistence.repository.activity_ownership import (
    ActivityOwner,
    assign_awaiting_activity,
)
from scanner.persistence.repository.media_identity_store import get_version, review_version_id
from scanner.persistence.repository.review_deletion import PublishedReviewDeletionBlocked
from scanner.persistence.rows.db_policies import ExportScope
from scanner.runtime import media_fingerprints
from scanner.runtime.scan_version import file_generation
from tests.activity_migration_support import build_activity_database, sidecar_state

_TIME = "2026-03-01 00:00:00.000000"
_LOCAL = {user: f"local-{user:032x}" for user in (1, 2, 3, 4)}
_USERS = (
    "INSERT INTO users (id, role, status, username, normalized_username, local_export_id, "
    "permission_revision, created_at, updated_at) VALUES "
    "(1, 'owner', 'active', 'o', 'o', :l1, 1, :t, :t), "
    "(2, 'admin', 'active', NULL, NULL, :l2, 1, :t, :t), "
    "(3, 'user', 'active', NULL, NULL, :l3, 1, :t, :t), "
    "(4, 'user', 'active', NULL, NULL, :l4, 1, :t, :t)"
)
_EXPORT = (
    "INSERT INTO media_item_exports (media_item_id, media_source_id, format, exported_at, "
    "review_id, exported_format_revision, scope, target_user_id, target_export_id, actor_id, "
    "policy_owner_id, original_author_id, policy_revision) VALUES "
    "('media', 'v1', 'edl', '{t}', 'review', 1, {values})"
)
# An admin (2) exports to self a review owned by 3 and written by 4.
_PERSONAL = "'personal', 2, '{l2}', 2, 3, 4, 7"
_DEFAULT = "'default', NULL, NULL, 2, 3, 4, 7"
_LEGACY = "'legacy', NULL, NULL, 1, 3, 4, NULL"


def _export(values):
    return _EXPORT.format(t=_TIME, values=values.format(l2=_LOCAL[2], l3=_LOCAL[3]))


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "scanner.db"
    migrate_fresh_database(path, tmp_path)
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute(_USERS, {"t": _TIME, **{f"l{user}": _LOCAL[user] for user in _LOCAL}})
    connection.execute(
        "INSERT INTO media_items (id, server_id, jellyfin_id, item_type, name, created_at, "
        "updated_at) VALUES ('media', 'server', 'item', 'Movie', 'Movie', :t, :t)",
        {"t": _TIME},
    )
    connection.execute(
        "INSERT INTO saved_reviews (id, media_item_id, media_source_id, name, created_at, "
        "updated_at, owner_id, original_author_id) VALUES "
        "('review', 'media', 'v1', 'Transferred', :t, :t, 3, 4)",
        {"t": _TIME},
    )
    connection.commit()
    yield connection
    connection.close()


def test_one_row_keeps_actor_target_policy_owner_and_author_apart(database):
    for values in (_PERSONAL, _DEFAULT, _LEGACY, "'personal', 3, '{l3}', 2, 3, 4, 7"):
        database.execute(_export(values))
    assert database.execute(
        "SELECT scope, target_user_id, actor_id, policy_owner_id, original_author_id "
        "FROM media_item_exports ORDER BY id"
    ).fetchall() == [
        ("personal", 2, 2, 3, 4),
        ("default", None, 2, 3, 4),
        ("legacy", None, 1, 3, 4),
        ("personal", 3, 2, 3, 4),
    ]
    assert database.execute(
        "SELECT owner_id, original_author_id FROM saved_reviews"
    ).fetchall() == [(3, 4)]


@pytest.mark.parametrize(
    ("statement", "existing"),
    [
        (_export(_PERSONAL), _export(_PERSONAL)),
        (_export(_DEFAULT), _export(_DEFAULT)),
        (_export(_LEGACY), _export(_LEGACY)),
        (_export("'personal', NULL, NULL, 2, 3, 4, 7"), None),
        (_export("'default', 2, '{l2}', 2, 3, 4, 7"), None),
        (_export("'personal', 2, NULL, 2, 3, 4, 7"), None),
        (_export("'personal', 2, 'user-2', 2, 3, 4, 7"), None),
        (_export("'personal', 2, '00000000000000000000000000000000', 2, 3, 4, 7"), None),
        (_export("'default', NULL, NULL, 2, 3, 4, NULL"), None),
        (_export("'default', NULL, NULL, NULL, 3, 4, 7"), None),
        (_export("'legacy', NULL, NULL, NULL, 3, 4, NULL"), None),
        (_export("'legacy', NULL, NULL, 1, 3, NULL, NULL"), None),
        (_export("'shared', NULL, NULL, 2, 3, 4, 7"), None),
        (_export("'personal', 9, '{l2}', 2, 3, 4, 7"), None),
        ("UPDATE saved_reviews SET original_author_id = NULL", None),
        ("UPDATE filter_settings SET default_mode = 'mixed'", None),
        ("INSERT INTO user_filter_preferences VALUES (3, 'both', 0, 0, '2026')", None),
        ("INSERT INTO user_filter_preferences VALUES (3, NULL, 0, 0, '2026')", None),
        ("INSERT INTO user_filter_enforcements VALUES (3, 'severity', 'slurs')", None),
        ("INSERT INTO media_access_scopes VALUES (2, NULL, 1, '2026')", None),
        ("INSERT INTO media_access_scope_sources VALUES (1, 'manual_files', 'files-1')", None),
        ("INSERT INTO media_access_scope_sources VALUES (1, 'radarr', '')", None),
        ("INSERT INTO media_access_scope_sources VALUES (1, 'jellyfin_library', '')", None),
        ("INSERT INTO media_access_scope_sources VALUES (1, 'plex', '')", None),
    ],
)
def test_storage_refuses_ambiguous_or_duplicate_provenance(database, statement, existing):
    if existing is not None:
        database.execute(existing)
    with pytest.raises(sqlite3.IntegrityError):
        database.execute(statement)


def test_filter_and_media_policy_storage_accepts_its_valid_states(database):
    database.execute("INSERT INTO user_filter_preferences VALUES (3, 'live', 0, 0, '2026')")
    database.execute("INSERT INTO user_filter_preferences VALUES (4, 'copy', 2, 5, '2026')")
    database.execute(
        "INSERT INTO user_filter_enforcements VALUES (4, 'severity', 'slurs'), "
        "(4, 'selection_rule', 'rule-1')"
    )
    database.execute("INSERT INTO media_access_scopes VALUES (2, 3, 0, '2026')")
    database.execute(
        "INSERT INTO media_access_scope_sources VALUES (2, 'jellyfin_library', 'library-1'), "
        "(2, 'jellyfin_library', 'library-2')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        database.execute("INSERT INTO media_access_scopes VALUES (3, 3, 1, '2026')")
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        database.execute("DELETE FROM users WHERE id = 4")
    database.execute("DELETE FROM saved_reviews")
    database.execute("DELETE FROM users WHERE id = 4")
    assert database.execute("SELECT count(*) FROM user_filter_enforcements").fetchone() == (0,)
    database.execute("DELETE FROM users WHERE id = 3")
    assert database.execute("SELECT user_id FROM media_access_scopes").fetchall() == [(None,)]
    assert database.execute("SELECT count(*) FROM media_access_scope_sources").fetchone() == (0,)


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _personal(export_format, revision):
    """The owner's personal export of the imported review, as MU-31 will record it."""
    return MediaItemExportRow(
        media_item_id="media-legacy",
        media_source_id="",
        format=export_format,
        review_id="review-imported-002",
        exported_format_revision=revision,
        scope="personal",
        target_user_id=1,
        target_export_id=_LOCAL[1],
        actor_id=1,
        policy_owner_id=1,
        original_author_id=1,
        policy_revision=0,
    )


_JELLYFIN, _ITEM = "http://jellyfin.test", "movie-legacy"


def test_existing_owner_workflows_run_on_an_upgraded_database(tmp_path, monkeypatch):
    data_dir, media = tmp_path / "data", tmp_path / "media"
    data_dir.mkdir()
    monkeypatch.setattr(media_fingerprints.settings, "data_dir", data_dir)
    path = data_dir / "scanner.db"
    _cookie, before = build_activity_database(path, media, "0009_readable_api_key")
    with sqlite3.connect(path) as legacy:
        legacy.execute(
            "UPDATE media_items SET server_id = ?, jellyfin_id = ?, item_type = 'Movie'",
            (_JELLYFIN, _ITEM),
        )
    adoption.upgrade_existing_database(path, data_dir)
    assert sidecar_state(media) == before
    engine = create_engine(f"sqlite:///{path}")
    try:
        with sessionmaker(bind=engine)() as db:
            reviews = repository.list_saved_reviews(db, media_item_id="media-legacy")
            assert {review.id for review in reviews} == {"review-legacy-001", "review-imported-002"}
            assert {review.exports.exports[ExportFormat.EDL].review_id for review in reviews} == {
                "review-legacy-001"
            }

            copy = repository.duplicate_saved_review(
                db, "review-legacy-001", ReviewDuplicate(name="Copy", revision=2)
            )
            assert (db.get(SavedReviewRow, copy.id).owner_id, copy.revision) == (1, 0)

            mcf_hash = _sha(media / "Movie.mcf")
            db.add(_personal(ExportFormat.EDL, 3))
            db.commit()
            assert repository.delete_library_item_export(
                db,
                server_id=_JELLYFIN,
                jellyfin_item_id=_ITEM,
                media_source_id=None,
                export_format=ExportFormat.EDL,
            )
            assert not (media / "Movie.edl").exists()
            review = db.get(SavedReviewRow, "review-legacy-001")
            version = get_version(db, review_version_id(db, review.id))
            assert version.content_sha256 is None
            with pytest.raises(repository.ReviewMembershipError, match="physical media version"):
                repository.export_saved_review_edl(db, review.id, review.revision)
            db.rollback()
            # The fixture owns the original media bytes, but a stored SHA alone
            # cannot establish that those bytes are still at the current path.
            media_path = media / "Movie.mkv"
            version.content_sha256 = digest = _sha(media_path)
            db.commit()
            with pytest.raises(repository.ReviewMembershipError, match="physical media version"):
                repository.export_saved_review_edl(db, review.id, review.revision)
            db.rollback()
            media_fingerprints.remember_content_digest(
                media_path, file_generation(str(media_path)), digest
            )
            exported = repository.export_saved_review_edl(db, review.id, review.revision)
            assert exported.exported_format_revision == review.edl_content_revision
            assert _sha(media / "Movie.mcf") == mcf_hash
            legacy = db.scalar(
                select(MediaItemExportRow).where(
                    MediaItemExportRow.format == ExportFormat.EDL,
                    MediaItemExportRow.scope == "legacy",
                )
            )
            assert legacy.id != 1
            assert (legacy.actor_id, legacy.policy_owner_id, legacy.original_author_id) == (1, 1, 1)
            assert legacy.policy_revision == 0
            db.add(_personal(ExportFormat.MCF, 2))
            db.commit()
            status = repository.media_item_export_status(
                db, server_id=_JELLYFIN, jellyfin_item_id=_ITEM
            )
            assert status.exports[ExportFormat.EDL].review_id == "review-legacy-001"
            assert status.exports[ExportFormat.MCF].review_id is None

            with pytest.raises(PublishedReviewDeletionBlocked):
                repository.delete_saved_review(db, "review-imported-002", 5)
            assert db.get(SavedReviewRow, "review-imported-002") is not None
            retained = db.scalars(
                select(MediaItemExportRow)
                .where(MediaItemExportRow.scope == "personal")
                .order_by(MediaItemExportRow.format)
            ).all()
            assert [(row.format, row.review_id, row.policy_owner_id) for row in retained] == [
                (ExportFormat.EDL, "review-imported-002", 1),
                (ExportFormat.MCF, "review-imported-002", 1),
            ]
            assert {(row.original_author_id, row.actor_id) for row in retained} == {(1, 1)}

            item = db.get(MediaItemRow, "media-legacy")
            scan = repository.create_scan(
                db,
                media_item=item,
                resolved_local_path=str(media / "Movie.mkv"),
                scan_audio=False,
                audio_model=None,
                audio_filter=None,
                scan_video=True,
                video_model=None,
            )
            row = db.get(ScanRow, scan.id)
            # 0030 disabled the unlinked owner, which moved its permission revision.
            assert (row.initiator_id, row.initiator_permission_revision) == (1, 2)
    finally:
        engine.dispose()


@pytest.mark.parametrize("through", ["session", "connection"])
def test_owner_assignment_leaves_archived_activity_unowned(tmp_path, through):
    path = tmp_path / "scanner.db"
    migrate_fresh_database(path, tmp_path)
    engine = create_engine(f"sqlite:///{path}")
    try:
        with sessionmaker(bind=engine)() as db, archived_reads(db):
            for item, source in (("live", None), ("archived", ArchivedSource.RADARR)):
                db.add(
                    MediaItemRow(
                        id=item,
                        server_id=f"{source or 'jellyfin'}:server",
                        jellyfin_id=item,
                        item_type="Movie",
                        name=item,
                        archived_source=source,
                    )
                )
                db.add(SavedReviewRow(id=f"{item}-review", media_item_id=item, name=item))
                db.add(
                    ScanRow(
                        id=f"{item}-scan",
                        media_item_id=item,
                        scan_number=1,
                        source_path=f"/media/{item}.mkv",
                        resolved_local_path=f"/media/{item}.mkv",
                        status="complete",
                    )
                )
                db.add(
                    MediaItemExportRow(
                        media_item_id=item,
                        media_source_id="",
                        format=ExportFormat.EDL,
                        scope=ExportScope.LEGACY,
                    )
                )
            db.commit()
        owner = ActivityOwner(user_id=1, permission_revision=1)
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "INSERT INTO users (id, role, status, username, normalized_username, "
                "local_export_id, permission_revision, created_at, updated_at) VALUES "
                "(1, 'owner', 'active', 'o', 'o', ?, 1, ?, ?)",
                (_LOCAL[1], _TIME, _TIME),
            )
            if through == "connection":
                assign_awaiting_activity(connection, owner)
        if through == "session":
            with sessionmaker(bind=engine)() as db:
                assign_awaiting_activity(db, owner)
                db.commit()
        with engine.connect() as connection:
            owners = connection.exec_driver_sql(
                "SELECT media_item_id, owner_id FROM saved_reviews UNION ALL "
                "SELECT media_item_id, initiator_id FROM scans UNION ALL "
                "SELECT media_item_id, actor_id FROM media_item_exports ORDER BY 1, 2"
            ).fetchall()
        assert owners == [("archived", None)] * 3 + [("live", 1)] * 3
    finally:
        engine.dispose()
