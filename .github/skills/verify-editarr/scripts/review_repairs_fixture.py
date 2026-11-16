"""Build a main-shaped orphan database using the migration suite's frozen schema.

`review_repairs_fixture.py DATA ITEM_JSON < JELLYFIN_JSON` seeds the 0009 orphan shape, upgrades it
with the real migrations to 0029, the last revision with local passwords, and links
its owner to the run Jellyfin's administrator as JF-03 setup would have, so startup's
0030 keeps the owner active. `ITEM_JSON` names the synthetic item; stdin holds the
Jellyfin `base`, `key`, `serverId`, and `adminId`, so the key never reaches argv.
`review_repairs_fixture.py DATA USER_ID` stages the permission specimen.
"""

import hashlib
import json
import sqlite3
import sys
from pathlib import Path

from tests.activity_migration_support import _clone, _upgrade, build_activity_database

LINKED_REVISION = "0029_jellyfin_user_blocks"
NOW = "2026-10-30 12:00:00"


def _as_jellyfin_item(database, item: dict):
    """Rebind the fixture's Other Files item to one synthetic Jellyfin item version.

    0026 archives Other Files items, so the repairs would otherwise be read-only.
    `item` names the serving `server` URL and the `library`, `item`, `version`, and
    `title` it reports.
    """
    database.execute(
        "UPDATE media_items SET server_id = ?, library_id = ?, jellyfin_id = ?, name = ?, "
        "item_type = 'Movie'",
        (item["server"], item["library"], item["item"], item["title"]),
    )
    tables = [
        row[0] for row in database.execute("SELECT name FROM sqlite_master WHERE type='table'")
    ]
    for table in tables:
        columns = {row[1] for row in database.execute(f'PRAGMA table_info("{table}")')}
        if {"media_item_id", "media_source_id"} <= columns:
            database.execute(f'UPDATE "{table}" SET media_source_id = ?', (item["version"],))


def _link_owner(path: Path, jellyfin: dict) -> None:
    """Upgrade to 0029 and link user 1 to the Jellyfin administrator with its server's key."""
    _upgrade(path, LINKED_REVISION)
    with sqlite3.connect(path) as database:
        database.execute(
            "INSERT OR REPLACE INTO jellyfin_settings (id, server_url, api_key, updated_at) "
            "VALUES (1, ?, ?, ?)",
            (jellyfin["base"], jellyfin["key"], NOW),
        )
        database.execute(
            "INSERT INTO user_provider_identities (user_id, server_id, provider_user_id, "
            "link_state, last_verified_at, created_at, updated_at) "
            "VALUES (1, ?, ?, 'verified', ?, ?, ?)",
            (jellyfin["serverId"], jellyfin["adminId"].replace("-", "").lower(), NOW, NOW, NOW),
        )


def seed(data: Path, item: dict, jellyfin: dict):
    """Seed three reviews and two unused imports before the real startup upgrade."""
    data.mkdir(parents=True)
    path = data / "scanner.db"
    media = data.parent / "media"
    build_activity_database(
        path, media, "0009_readable_api_key", queued_status="awaiting_model_consent"
    )
    with sqlite3.connect(path) as database:
        database.execute("DELETE FROM community_imports")
        database.execute("DELETE FROM media_item_export_scans")
        database.execute("DELETE FROM media_item_exports")
        _clone(database, "saved_reviews", "review-legacy-001", id="healthy", name="Healthy review")
        database.execute(
            "INSERT INTO review_scan_memberships (review_id, scan_id, role, position) "
            "VALUES ('healthy', 'legacy-scan-001', 'both', 0)"
        )
        database.execute(
            "INSERT INTO review_synchronizations "
            "(review_id, start_seconds, end_seconds, end_mode) "
            "VALUES ('healthy', 0, 120, 'file_end')"
        )
        database.execute("DELETE FROM review_scan_memberships WHERE review_id='review-legacy-001'")
        database.execute(
            "UPDATE saved_reviews SET name='Local repair' WHERE id='review-legacy-001'"
        )
        database.execute(
            "UPDATE saved_reviews SET name='Community repair' WHERE id='review-imported-002'"
        )
        _clone(database, "scans", "legacy-scan-001", id="deleted-local", scan_number=91)
        database.execute(
            "UPDATE review_filters SET source_scan_id='deleted-local', source_finding_id=NULL "
            "WHERE review_id='review-legacy-001'"
        )
        source_filter = database.execute("SELECT id FROM review_filters LIMIT 1").fetchone()[0]
        _clone(
            database,
            "review_filters",
            source_filter,
            review_id="review-imported-002",
            id="kept-community-cue",
            source_scan_id="imported-scan-003",
            source_finding_id=None,
            start_seconds=5,
            end_seconds=7,
            label="Preserved community cue",
        )
        for scan_id, number, name, revision in [
            ("imported-scan-003", 9, "Deleted community", "deleted-revision"),
            ("unused-community", 92, "Unused community", "unused-revision"),
            ("pending-community", 93, "Pending community", "pending-revision"),
        ]:
            if scan_id != "imported-scan-003":
                _clone(
                    database,
                    "scans",
                    "imported-scan-003",
                    id=scan_id,
                    scan_number=number,
                    name=name,
                )
            database.execute(
                "INSERT INTO community_imports "
                "(id,scan_id,service_url,contribution_id,revision_id,"
                "digest,author_id,artifact_type) "
                "VALUES (?,?, 'https://community.test','synthetic',?,?,'synthetic-author','scan')",
                (f"import-{scan_id}", scan_id, revision, "a" * 64),
            )
        database.execute(
            "INSERT INTO community_import_targets VALUES "
            "('import-imported-scan-003','media-legacy','',?,NULL,'{}',0)",
            (str(media / "Movie.mkv"),),
        )
        database.executemany(
            "INSERT INTO community_operations "
            "(operation_id,service_url,kind,state,scan_id,updated_at) "
            "VALUES (?,'https://community.test','receipt',?,?, '2026-10-25')",
            [
                ("deleted-operation", "acknowledged", "imported-scan-003"),
                ("unused-operation", "acknowledged", "unused-community"),
                ("pending-operation", "pending", "pending-community"),
            ],
        )
        database.execute(
            "INSERT INTO scan_stage_coverage "
            "(scan_id,stage,execution_id,status,source) "
            "VALUES ('imported-scan-003','audio','orphan-attempt','complete','imported')"
        )
        database.execute(
            "INSERT INTO credits_detection_runs (scan_id,configuration_json,detector_version) "
            "VALUES ('deleted-local','{}','synthetic-v1')"
        )
        database.execute(
            "INSERT INTO scan_mcf_documents "
            "(scan_id,source_version,material_start_seconds,material_end_seconds) "
            "VALUES ('imported-scan-003','1.1.0',0,120)"
        )
        database.execute("DELETE FROM scans WHERE id IN ('deleted-local','imported-scan-003')")
        _as_jellyfin_item(database, item)
        assert database.execute("SELECT version_num FROM alembic_version").fetchall() == [
            ("0009_readable_api_key",)
        ]
        legacy = database.execute("PRAGMA foreign_key_check").fetchall()
        assert {row[0] for row in legacy} >= {
            "review_filters",
            "review_scan_memberships",
            "community_imports",
            "community_operations",
            "scan_stage_coverage",
            "credits_detection_runs",
            "scan_mcf_documents",
        }
        legacy_foreign_keys = sorted({row[0] for row in legacy})
    _link_owner(path, jellyfin)
    with sqlite3.connect(path) as database:
        violations = database.execute("PRAGMA foreign_key_check").fetchall()
        tables = [
            row[0]
            for row in database.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        counts = {
            table: database.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
            for table in tables
        }
    return {
        "legacyForeignKeyTables": legacy_foreign_keys,
        "media": str(media / "Movie.mkv"),
        "seededRevision": "0009_readable_api_key",
        "revision": LINKED_REVISION,
        "foreignKeyViolations": violations,
        "counts": counts,
        "sourceSha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def permission_copy(data: Path, owner_id: int):
    """Stage an ordinary-owned repair specimen in this verifier's scratch only."""
    assert data.parent.name.startswith("editarr-verify-")
    with sqlite3.connect(data / "scanner.db", timeout=10) as database:
        database.execute("PRAGMA foreign_keys=ON")
        database.execute("BEGIN IMMEDIATE")
        assert database.execute("SELECT role FROM users WHERE id=?", (owner_id,)).fetchone() == (
            "user",
        )
        _clone(
            database,
            "saved_reviews",
            "review-imported-002",
            id="permission-probe",
            name="Permission probe",
            owner_id=owner_id,
            original_author_id=owner_id,
        )
        source_id = database.execute(
            "SELECT id FROM review_scan_repairs WHERE review_id='review-imported-002'"
        ).fetchone()[0]
        _clone(database, "review_scan_repairs", source_id, id=9000, review_id="permission-probe")
        revision = database.execute(
            "SELECT revision FROM saved_reviews WHERE id='permission-probe'"
        ).fetchone()[0]
    return {"reviewId": "permission-probe", "ownerId": owner_id, "revision": revision}


if __name__ == "__main__":
    if sys.argv[2].isdigit():
        print(json.dumps(permission_copy(Path(sys.argv[1]), int(sys.argv[2]))))
    else:
        print(json.dumps(seed(Path(sys.argv[1]), json.loads(sys.argv[2]), json.load(sys.stdin))))
