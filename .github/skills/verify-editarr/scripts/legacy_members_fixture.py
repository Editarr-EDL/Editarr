"""Seed a pre-JF-06 installation whose local password accounts migration 0030 disables.

`legacy_members_fixture.py DATA ITEM_JSON < JELLYFIN_JSON` builds the migration suite's
0009 activity database, rebinds its media item to the synthetic Jellyfin item that
`ITEM_JSON` names, and upgrades it with the real migrations to 0029, the last revision
with local passwords, linking user 1 (the owner) to the run Jellyfin's administrator
as `review_repairs_fixture.py` does. It then adds two local password accounts, each
with a literal Argon2 string and no Jellyfin link: `legacy-member`, which now owns the
fixture's reviews and scans, and `legacy-linker`, which owns nothing. As
`legacy-member`, the current publication writer then publishes `review-legacy-001` as
EDL and MCF under the member's local export ID, the files a JF-05 local account held.
Real startup applies `0030_remove_passwords`, which disables both members with
cause `unlinked`. Stdin holds the Jellyfin `base`, `key`, `serverId`, and `adminId`,
so the key never reaches argv.
"""

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from review_repairs_fixture import NOW, _as_jellyfin_item, _link_owner  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from scanner.db import SavedReviewRow  # noqa: E402
from scanner.models import ExportFormat  # noqa: E402
from scanner.persistence.repository.export_publication import publish_export  # noqa: E402
from scanner.persistence.repository.export_recovery import publication_lock  # noqa: E402
from scanner.security.access.identity import CredentialKind  # noqa: E402
from scanner.security.access.load import load_access  # noqa: E402
from tests.activity_migration_support import build_activity_database  # noqa: E402

PASSWORD_HASH = (
    "$argon2id$v=19$m=19456,t=2,p=1$c2FsdHNhbHRzYWx0$aGFzaGhhc2hoYXNoaGFzaGhhc2hoYXNoaGFzaGhhc2g"
)
MEMBERS = {"legacy-member": 2, "legacy-linker": 3}
PUBLISHED = "review-legacy-001"


def _publish_as_member(path: Path, user_id: int) -> None:
    """Publish PUBLISHED as EDL and MCF to `user_id`'s own export ID with the app's writer."""
    engine = create_engine(f"sqlite:///{path}")
    try:
        for export_format in (ExportFormat.EDL, ExportFormat.MCF):
            with Session(engine) as db:
                access = load_access(db, user_id, CredentialKind.SESSION)
                revision = db.get(SavedReviewRow, PUBLISHED).revision
                db.rollback()
                with publication_lock(db):
                    assert publish_export(db, PUBLISHED, revision, export_format, access=access)
    finally:
        engine.dispose()


def seed(data: Path, item: dict, jellyfin: dict) -> dict:
    data.mkdir(parents=True, exist_ok=True)
    path = data / "scanner.db"
    media = Path(item["file"]).parent
    build_activity_database(
        path, media, "0009_readable_api_key", queued_status="awaiting_model_consent"
    )
    with sqlite3.connect(path) as database:
        database.execute("DELETE FROM community_imports")
        database.execute("DELETE FROM media_item_export_scans")
        database.execute("DELETE FROM media_item_exports")
        _as_jellyfin_item(database, item)
    _link_owner(path, jellyfin)
    member = MEMBERS["legacy-member"]
    with sqlite3.connect(path) as database:
        for name, user_id in MEMBERS.items():
            database.execute(
                "INSERT INTO users (id, role, status, username, normalized_username, "
                "password_hash, local_credential, local_export_id, permission_revision, "
                "created_at, updated_at, password_changed_at) "
                "VALUES (?, 'user', 'active', ?, ?, ?, 'set', ?, 1, ?, ?, ?)",
                (user_id, name, name, PASSWORD_HASH, f"local-{user_id:032x}", NOW, NOW, NOW),
            )
        database.execute(
            "UPDATE saved_reviews SET owner_id = ?, original_author_id = ?", (member, member)
        )
        database.execute(
            "UPDATE scans SET initiator_id = ?, initiator_permission_revision = 1", (member,)
        )
        database.execute(
            "INSERT INTO review_mcf_documents (review_id, title, work_type, "
            "material_start_seconds, material_end_seconds) VALUES (?, ?, 'movie', 0, 120)",
            (PUBLISHED, item["title"]),
        )
    _publish_as_member(path, member)
    with sqlite3.connect(path) as database:
        users = database.execute(
            "SELECT id, role, status, local_credential, password_hash IS NOT NULL "
            "FROM users ORDER BY id"
        ).fetchall()
        reviews = database.execute(
            "SELECT id, name, owner_id FROM saved_reviews ORDER BY id"
        ).fetchall()
        exports = database.execute(
            "SELECT format, scope, target_user_id, target_export_id, destination_path "
            "FROM media_item_exports ORDER BY id"
        ).fetchall()
        identities = database.execute(
            "SELECT user_id FROM user_provider_identities ORDER BY user_id"
        ).fetchall()
        (revision,) = database.execute("SELECT version_num FROM alembic_version").fetchone()
        violations = database.execute("PRAGMA foreign_key_check").fetchall()
    return {
        "revision": revision,
        "members": MEMBERS,
        "users": users,
        "linkedUsers": [row[0] for row in identities],
        "reviews": reviews,
        "exports": exports,
        "foreignKeyViolations": violations,
    }


if __name__ == "__main__":
    print(json.dumps(seed(Path(sys.argv[1]), json.loads(sys.argv[2]), json.load(sys.stdin))))
