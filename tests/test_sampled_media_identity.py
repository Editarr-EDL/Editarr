"""Preserve legacy version IDs and resolve sampled media without merging collisions."""

import sqlite3
from dataclasses import replace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from scanner.backup.snapshots import validate_safety_snapshot
from scanner.db import Base, MediaItemRow, MediaVersionRow
from scanner.persistence.migrations import adoption
from scanner.persistence.migrations.runner import upgrade_connection
from scanner.persistence.repository import media_identity_store as identity
from scanner.runtime.media_fingerprints import sampled_media_fingerprint
from tests.stable_media_identity_migration_support import STABLE_MEDIA_IDENTITY_TABLES
from tests.test_stable_media_identity_migration import _ORIGINAL_TABLES, _build, _rows


def test_moves_reuse_unique_sample_and_never_promote_an_old_weak_hash(tmp_path):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    original = tmp_path / "original.mkv"
    original.write_bytes(b"same sample data")
    sample = sampled_media_fingerprint(original)
    with Session(engine) as db:
        media = _media(db)
        title = identity.observe_title(db, media)
        db.add(MediaVersionRow(id="legacy", title_id=title, video_hash=sample.digest))
        db.flush()
        arguments = {"media": media, "media_source_id": None, "source_path": str(original)}
        assert identity.find_sampled_version(db, title_id=title, fingerprint=sample) is None
        pinned = identity.observe_sampled_version(db, **arguments, fingerprint=sample)
        assert pinned != "legacy"
        assert db.get(MediaVersionRow, "legacy").sampled_md5 is None

        renamed = tmp_path / "renamed.mkv"
        original.rename(renamed)
        moved = sampled_media_fingerprint(renamed)
        assert identity.find_sampled_version(db, title_id=title, fingerprint=moved) == pinned
        assert (
            identity.find_sampled_version(
                db, title_id=title, fingerprint=replace(moved, size_bytes=moved.size_bytes + 1)
            )
            is None
        )
        assert identity.observe_sampled_version(db, **arguments, fingerprint=moved) == pinned
        assert (
            identity.find_observed_version(
                db,
                title_id=title,
                path=moved.path,
                generation=moved.generation,
            )
            == pinned
        )


def test_unchanged_full_witnesses_preserve_distinct_ids_on_sample_collisions(tmp_path):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    old_paths = [tmp_path / name for name in ("first.mkv", "second.mkv", "ambiguous.mkv")]
    for path in old_paths:
        path.write_bytes(b"identical sampled ranges")
    with Session(engine) as db:
        media = _media(db)
        title = identity.observe_title(db, media)
        versions = []
        for full, path in zip(("a" * 64, "b" * 64), old_paths, strict=False):
            arguments = {"media": media, "media_source_id": None, "source_path": str(path)}
            version_id = identity.observe_version(db, **arguments, content_sha256=full)
            sample = sampled_media_fingerprint(path)
            assert (
                identity.find_sampled_version(db, title_id=title, fingerprint=sample) != version_id
            )
            assert (
                identity.observe_sampled_version(
                    db,
                    **arguments,
                    fingerprint=sample,
                    known_content_sha256=full,
                )
                == version_id
            )
            versions.append(version_id)

        ambiguous = sampled_media_fingerprint(old_paths[-1])
        assert identity.find_sampled_version(db, title_id=title, fingerprint=ambiguous) is None
        for version_id in versions:
            assert not identity.matches_sampled_version(
                db, identity.get_version(db, version_id), ambiguous
            )
        first = sampled_media_fingerprint(old_paths[0])
        assert identity.find_sampled_version(db, title_id=title, fingerprint=first) == versions[0]


def _media(db):
    row = MediaItemRow(
        id="film", server_id="server", jellyfin_id="film", name="Film", item_type="Movie"
    )
    db.add(row)
    db.flush()
    return row


def test_upgrade_from_stable_ids_keeps_original_versions_and_recoverable_backup(tmp_path):
    path = tmp_path / "scanner.db"
    _build(path, "0034_scan_read_indexes")
    engine = create_engine(f"sqlite:///{path}")
    try:
        with engine.begin() as db:
            upgrade_connection(db, adoption.STABLE_MEDIA_IDENTITY_REVISION)
            db.exec_driver_sql(
                "UPDATE media_versions SET content_sha256 = ? WHERE id IN "
                "(SELECT version_id FROM scan_media_versions WHERE scan_id = 'original')",
                ("a" * 64,),
            )
            db.exec_driver_sql("UPDATE media_item_exports SET content_bytes = ?", (b"retained",))
    finally:
        engine.dispose()
    with sqlite3.connect(path) as db:
        columns = {
            table: ", ".join(row[1] for row in db.execute(f"PRAGMA table_info({table})"))
            for table in (*_ORIGINAL_TABLES, *STABLE_MEDIA_IDENTITY_TABLES)
        }
        original = _rows(db, columns)

    backup = adoption.upgrade_existing_database(path, tmp_path)
    validate_safety_snapshot(backup)
    restored = adoption.export_upgrade_recovery(path, tmp_path, tmp_path / "recovered.db")
    assert adoption._inspect_database(restored) == adoption.STABLE_MEDIA_IDENTITY_REVISION
    assert adoption._inspect_database(path) == adoption.SAMPLED_MEDIA_IDENTITY_REVISION
    with sqlite3.connect(restored) as db:
        assert _rows(db, columns) == original
    with sqlite3.connect(path) as db:
        assert _rows(db, columns) == original
        assert db.execute("SELECT count(sampled_md5) FROM media_versions").fetchone() == (0,)
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
