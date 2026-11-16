"""Generate a synthetic, disposable unversioned SQLite migration specimen."""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine, event

from scanner.db import (
    ArchivedConnectionRow,
    AuthSettingsRow,
    FilterSelectionRuleRow,
    JellyfinSettingsRow,
    MediaItemRow,
    ReviewFilterRow,
    ReviewScanMembershipRow,
    ReviewSynchronizationRow,
    SavedReviewRow,
    ScanFindingRow,
    ScanRow,
)
from scanner.persistence.migrations.schema import frozen_baseline
from tests.community_schema_support import baseline_specification

SYNTHETIC_PASSWORD_HASH = (
    "$argon2id$v=19$m=19456,t=2,p=1$c2FsdHNhbHRzYWx0$aGFzaGhhc2hoYXNoaGFzaGhhc2hoYXNoaGFzaGhhc2g"
)
"""A pre-0030 password hash string; nothing verifies it."""


def create_unversioned_fixture(path: Path):
    """Create one known-shape specimen at a new path; never replace an existing file."""
    if path.exists():
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{path}")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, record):
        connection.execute("PRAGMA foreign_keys=ON")

    try:
        baseline = frozen_baseline()
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        with engine.begin() as connection:
            for statement in baseline["statements"]:
                connection.exec_driver_sql(statement)
            connection.exec_driver_sql(
                "INSERT INTO filter_settings (id, revision, updated_at) "
                "VALUES (?, ?, CURRENT_TIMESTAMP)",
                tuple(baseline["required_seeds"]["filter_settings"][0]),
            )
            connection.exec_driver_sql(
                "INSERT INTO mcf_severity_defaults (settings_id, subcategory, position, severity) "
                "VALUES (?, ?, ?, ?)",
                [tuple(row) for row in baseline["required_seeds"]["mcf_severity_defaults"]],
            )

            def add_rows(rows):
                for row in rows:
                    columns = {
                        column[1]
                        for column in connection.exec_driver_sql(
                            f"PRAGMA table_info({row.__table__.name})"
                        )
                    }
                    values = {key: value for key, value in vars(row).items() if key in columns}
                    connection.execute(row.__table__.insert().values(**values))

            add_rows(
                [
                    MediaItemRow(
                        id="media-legacy",
                        server_id="fixture",
                        jellyfin_id="movie-1",
                        item_type="Movie",
                        name="Synthetic Movie",
                    ),
                    AuthSettingsRow(method="forms", authentication_required="enabled"),
                    JellyfinSettingsRow(server_url="https://example.invalid", api_key=None),
                    ArchivedConnectionRow(service="radarr", server_url="https://example.invalid"),
                ]
            )
            # Historical singleton auth rows; current ORM rows have a different shape.
            connection.exec_driver_sql(
                "INSERT INTO admin_users (id, username, normalized_username, password_hash, "
                "created_at, updated_at, password_changed_at) VALUES (1, 'fixture-admin', "
                "'fixture-admin', ?, ?, ?, ?)",
                (SYNTHETIC_PASSWORD_HASH, *(now.strftime("%Y-%m-%d %H:%M:%S.%f"),) * 3),
            )
            connection.exec_driver_sql(
                "INSERT INTO auth_sessions (admin_user_id, token_digest, csrf_token_digest, "
                "csrf_token_ciphertext, created_at, last_used_at, absolute_expires_at, "
                "remember_me) VALUES (1, 'synthetic-digest', 'synthetic-csrf-digest', "
                "'synthetic-not-encrypted', ?, ?, ?, 0)",
                (now.strftime("%Y-%m-%d %H:%M:%S.%f"),) * 3,
            )
            add_rows(
                [
                    FilterSelectionRuleRow(
                        id="f1c7e2a0-5b3d-4e8f-9a6c-0d2b4f6a8c10",
                        settings_id=1,
                        position=0,
                        disposition="deny",
                        evidence_source="audio",
                    ),
                    ScanRow(
                        id="legacy-scan-001",
                        media_item_id="media-legacy",
                        scan_number=7,
                        name="Legacy specimen",
                        source_path="/synthetic/movie.mkv",
                        resolved_local_path="/synthetic/movie.mkv",
                        status="completed",
                        duration_seconds=120.0,
                    ),
                ]
            )
            add_rows(
                [
                    ScanFindingRow(
                        id=101,
                        scan_id="legacy-scan-001",
                        category=None,
                        subcategory=None,
                        start_seconds=10.0,
                        end_seconds=12.0,
                        confidence=0.9,
                        source="audio",
                        edit_action="mute",
                        label="Synthetic finding",
                    ),
                    SavedReviewRow(
                        id="review-legacy-001",
                        media_item_id="media-legacy",
                        name="Synthetic review",
                        revision=2,
                        content_revision=2,
                        edl_content_revision=1,
                        mcf_content_revision=0,
                    ),
                ]
            )
            add_rows(
                [
                    ReviewScanMembershipRow(
                        review_id="review-legacy-001",
                        scan_id="legacy-scan-001",
                        role="audio",
                        position=0,
                    ),
                    ReviewFilterRow(
                        review_id="review-legacy-001",
                        id="review-filter-001",
                        source_scan_id="legacy-scan-001",
                        source_finding_id=101,
                        position=0,
                        source="audio",
                        category=None,
                        subcategory=None,
                        label="Synthetic finding",
                        start_seconds=15.0,
                        end_seconds=17.0,
                        confidence=0.9,
                        edit_action="mute",
                        selected=False,
                    ),
                    ReviewSynchronizationRow(
                        review_id="review-legacy-001",
                        start_seconds=5.0,
                        end_seconds=120.0,
                        end_mode="file_end",
                    ),
                ]
            )
        return engine
    except BaseException:
        engine.dispose()
        path.unlink(missing_ok=True)
        raise


def main():
    parser = argparse.ArgumentParser(description="Create a synthetic unversioned schema specimen")
    parser.add_argument("database", type=Path, help="new disposable SQLite file")
    parser.add_argument("--spec", type=Path, help="new JSON baseline specification")
    args = parser.parse_args()
    if args.spec is not None and args.spec.exists():
        parser.error("specification already exists")
    engine = create_unversioned_fixture(args.database)
    try:
        if args.spec is not None:
            args.spec.parent.mkdir(parents=True, exist_ok=True)
            args.spec.write_text(
                json.dumps(baseline_specification(engine), indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
