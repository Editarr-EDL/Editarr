"""Seed and inventory synthetic data directories for the backup-restore journey (MU-40).

`seed-source DATA MEDIA` builds a multi-user installation through the library from
the JSON on stdin, `{"base", "admin": {"name", "password"}, "members": {name:
Jellyfin user ID}}`: Jellyfin setup makes the owner from the run Jellyfin's
administrator, and import admits `restore-admin` (made admin), `restore-user`, and
`restore-linked`. It adds a retained deleted account and `restore-unlinked`,
disabled with cause `unlinked` as migration 0030 leaves a former local account; a
key for each of the first three; two enrolled community identities with a pending
operation; personal publications with sidecars; and one settled transfer move whose
publication records an empty path while the only copy sits at its old path. It
prints the keys as JSON.
`seed-occupied DATA` builds an installation with work but no account.
`inventory DATA` prints the account state and private-file digests of a data
directory. `interrupt DATA ARCHIVE` exits the process right after the restore
journal is written.
"""

import hashlib
import json
import os
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from scanner.db import JellyfinSettingsRow
from scanner.integrations.jellyfin_setup import JellyfinSetupClient
from scanner.integrations.jellyfin_users import JellyfinUserAdapter
from scanner.persistence.migrations.runner import migrate_fresh_database
from scanner.security.access.identity import CredentialKind
from scanner.security.api_key_crypto import load_api_key_wrap_key
from scanner.security.api_keys import rotate_api_key
from scanner.security.community_identity import (
    load_or_create_installation_identity,
    save_identity_registration,
)
from scanner.security.jellyfin_accounts import admission, reads
from scanner.security.jellyfin_accounts.owner_setup import set_up_owner
from scanner.security.private_identity_files import IdentityHome

NOW = "2026-10-30 12:00:00"
SERVICE = "https://community.test"
JELLYFIN = "http://jellyfin.test"
REFERENCES = {"restore-owner": "a" * 32, "restore-user": "c" * 32}


def _accounts(data, database_path, jellyfin):
    """Set the owner up and import the linked members through the library; return their IDs."""

    engine = create_engine(f"sqlite:///{database_path}")
    clock = lambda: datetime.now(UTC)  # noqa: E731
    try:
        with Session(engine) as db, JellyfinSetupClient(jellyfin["base"]) as client:
            owner = set_up_owner(
                db,
                client,
                jellyfin["admin"]["name"],
                jellyfin["admin"]["password"],
                clock=clock,
                issue=lambda user_id: user_id,
            )
            db.commit()
        with Session(engine) as db:
            settings = db.get(JellyfinSettingsRow, 1)
            read = reads.begin_read(db, clock)
            with JellyfinUserAdapter(settings.server_url, settings.api_key) as adapter:
                listing = adapter.users()
                selected = frozenset(
                    user_id.replace("-", "").lower() for user_id in jellyfin["members"].values()
                )
                admission.import_users(
                    db,
                    adapter,
                    listing,
                    selected,
                    actor=owner,
                    credential=CredentialKind.SESSION,
                    read=read,
                )
            db.commit()
    finally:
        engine.dispose()
    with sqlite3.connect(database_path) as database:
        ids = {"restore-owner": owner}
        for name, user_id in jellyfin["members"].items():
            (ids[name],) = database.execute(
                "SELECT user_id FROM user_provider_identities WHERE provider_user_id = ?",
                (user_id.replace("-", "").lower(),),
            ).fetchone()
        database.execute("UPDATE users SET role = 'admin' WHERE id = ?", (ids["restore-admin"],))
        for user_id, name, cause in (
            (5, "restore-retained", "local"),
            (6, "restore-unlinked", "unlinked"),
        ):
            database.execute(
                "INSERT INTO users (id, role, status, local_export_id, permission_revision, "
                "created_at, updated_at, disable_cause) "
                "VALUES (?, 'user', 'disabled', ?, 1, ?, ?, ?)",
                (user_id, f"local-{user_id:032x}", NOW, NOW, cause),
            )
            database.execute(
                "INSERT INTO user_profiles (user_id, display_name, updated_at) VALUES (?, ?, ?)",
                (user_id, name, NOW),
            )
            database.execute(
                "INSERT INTO user_filter_preferences (user_id, mode, revision, policy_revision, "
                "updated_at) SELECT ?, default_mode, 0, 0, ? FROM filter_settings",
                (user_id, NOW),
            )
            ids[name] = user_id
        database.execute(
            "INSERT INTO account_dispositions (user_id, actor_id, mode, confirmation, impact_json, "
            "result_json, created_at) VALUES (5, ?, 'retain', ?, '{}', '{}', ?)",
            (owner, "f" * 64, NOW),
        )
    return ids


def _identities(data, database_path, ids):
    owner, user = ids["restore-owner"], ids["restore-user"]
    with sqlite3.connect(database_path) as database:
        database.execute(
            "UPDATE community_identities SET credential_ref = ?, user_id = ?",
            (REFERENCES["restore-owner"], owner),
        )
        database.execute(
            "INSERT INTO community_identities (credential_ref, user_id, created_at) "
            "VALUES (?, ?, ?)",
            (REFERENCES["restore-user"], user, NOW),
        )
    for name, reference in REFERENCES.items():
        user_id = ids[name]
        home = IdentityHome(data, reference)
        load_or_create_installation_identity(home)
        identity = save_identity_registration(
            home,
            contributor_id=f"contributor-{user_id}",
            service_url=SERVICE,
            last_known_key_state="active",
        )
        with sqlite3.connect(database_path) as database:
            database.execute(
                "INSERT INTO community_connections (credential_ref, service_url, contributor_id, "
                "installation_id, key_id) VALUES (?, ?, ?, ?, ?)",
                (
                    reference,
                    SERVICE,
                    identity.contributor_id,
                    identity.installation_id,
                    identity.key_id,
                ),
            )
            if user_id == user:
                database.execute(
                    "INSERT INTO community_operations (operation_id, service_url, kind, state, "
                    "contributor_id, installation_id, owner_id, actor_id, receipt, updated_at) "
                    "VALUES ('pending-receipt', ?, 'receipt', 'pending', ?, ?, ?, ?, "
                    "'signed-receipt', ?)",
                    (SERVICE, identity.contributor_id, identity.installation_id, user, user, NOW),
                )


def _publications(database, media, ids):
    media.mkdir(parents=True, exist_ok=True)
    database.execute(
        "INSERT INTO media_items (id, server_id, jellyfin_id, item_type, name, created_at, "
        "updated_at) VALUES ('item', ?, ?, 'Movie', 'Restore', ?, ?)",
        (JELLYFIN, "1" * 32, NOW, NOW),
    )
    # Publication 3 records the path its settled move left empty; the only copy sits at
    # the move's old path (MU-38 review case R6-C).
    moved = media / "moved" / "publication-3.edl"
    moved.parent.mkdir(exist_ok=True)
    old = media / "publication-3.edl"
    old.write_bytes(b"0\t10\t0\n# the only copy, left at the old path\n")
    user = ids["restore-user"]
    exports = {
        user: database.execute(
            "SELECT provider_user_id FROM user_provider_identities WHERE user_id = ?", (user,)
        ).fetchone()[0],
        5: f"local-{5:032x}",
    }
    for export_id, user_id in ((1, user), (2, 5), (3, user)):
        path = moved if export_id == 3 else media / f"publication-{export_id}.edl"
        if export_id != 3:
            path.write_bytes(f"0\t10\t0\n# publication {export_id}\n".encode())
        database.execute(
            "INSERT INTO media_item_exports (id, media_item_id, media_source_id, format, "
            "exported_at, scope, target_user_id, target_export_id, actor_id, policy_owner_id, "
            "original_author_id, policy_revision, destination_path) "
            "VALUES (?, 'item', ?, 'edl', ?, 'personal', ?, ?, ?, ?, ?, 0, ?)",
            (
                export_id,
                f"source-{export_id}",
                NOW,
                user_id,
                exports[user_id],
                user_id,
                user_id,
                user_id,
                str(path),
            ),
        )
    database.execute(
        "INSERT INTO account_transfer_moves (user_id, confirmation, export_id, old_path, "
        "new_path, outcome) VALUES (?, ?, 3, ?, ?, 'removed')",
        (user, "e" * 64, str(old), str(moved)),
    )


def seed_source(data, media, jellyfin):
    data.mkdir(mode=0o700, parents=True, exist_ok=True)
    database_path = data / "scanner.db"
    migrate_fresh_database(database_path, data)
    ids = _accounts(data, database_path, jellyfin)
    with sqlite3.connect(database_path) as database:
        _publications(database, media, ids)
    _identities(data, database_path, ids)
    wrap = load_api_key_wrap_key(data)
    engine = create_engine(f"sqlite:///{database_path}")
    try:
        with Session(engine) as db:
            keys = {
                name: rotate_api_key(db, ids[name], wrap)
                for name in ("restore-owner", "restore-admin", "restore-user")
            }
    finally:
        engine.dispose()
    return {"keys": keys}


def seed_occupied(data):
    data.mkdir(mode=0o700, parents=True, exist_ok=True)
    migrate_fresh_database(data / "scanner.db", data)
    with sqlite3.connect(data / "scanner.db") as database:
        database.execute(
            "INSERT INTO media_items (id, server_id, jellyfin_id, item_type, name, created_at, "
            "updated_at) VALUES ('kept', ?, ?, 'Movie', 'Kept', ?, ?)",
            (JELLYFIN, "2" * 32, NOW, NOW),
        )
    return {"seeded": True}


def _digests(root):
    return (
        {
            path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(root.rglob("*"))
            if path.is_file() and not path.name.endswith(".lock")
        }
        if root.is_dir()
        else {}
    )


def inventory(data):
    uri = f"{(data / 'scanner.db').resolve().as_uri()}?mode=ro"
    database = sqlite3.connect(uri, uri=True)
    try:
        query = lambda sql: [list(row) for row in database.execute(sql)]  # noqa: E731
        return {
            "users": query(
                "SELECT id, role, status, display_name, local_export_id, disable_cause "
                "FROM users LEFT JOIN user_profiles ON user_id = id ORDER BY id"
            ),
            "providerIdentities": query(
                "SELECT user_id, server_id, provider_user_id, link_state "
                "FROM user_provider_identities"
            ),
            "dispositions": query("SELECT user_id, actor_id, mode FROM account_dispositions"),
            "apiKeys": query(
                "SELECT user_id, token_digest, secret_envelope FROM api_keys ORDER BY user_id"
            ),
            "sessions": query("SELECT count(*) FROM auth_sessions"),
            "communityIdentities": query(
                "SELECT credential_ref, user_id FROM community_identities ORDER BY 1"
            ),
            "communityConnections": query(
                "SELECT credential_ref, contributor_id, installation_id, key_id "
                "FROM community_connections ORDER BY 1"
            ),
            "communityOperations": query(
                "SELECT operation_id, state, owner_id, actor_id "
                "FROM community_operations ORDER BY 1"
            ),
            "exports": query(
                "SELECT id, target_user_id, policy_owner_id, destination_path "
                "FROM media_item_exports ORDER BY id"
            ),
            "transferMoves": query(
                "SELECT user_id, export_id, old_path, new_path FROM account_transfer_moves"
            ),
            "restoreEvents": query(
                "SELECT actor_kind, outcome, details_json FROM audit_events "
                "WHERE action = 'backup.restore'"
            ),
            "foreignKeyViolations": query("PRAGMA foreign_key_check"),
            "files": {
                **{
                    f"homes/{key}": value
                    for key, value in _digests(data / "secrets" / "community-identities").items()
                },
                **{
                    f"incoming/{key}": value
                    for key, value in _digests(data / "secrets" / "community").items()
                },
                **{
                    name: hashlib.sha256((data / name).read_bytes()).hexdigest()
                    for name in ("secrets/api-key-wrap.key",)
                    if (data / name).is_file()
                },
            },
        }
    finally:
        database.close()


def interrupt(data, archive):
    from scanner.backup import activation
    from scanner.backup.restore import preview_restore

    original = activation._record

    def record_then_exit(path, value):
        original(path, value)
        if path.name == activation.JOURNAL:
            os._exit(47)

    activation._record = record_then_exit
    with preview_restore(archive, data) as preview:
        activation.activate_restore(preview, data / "scanner.db", data, confirmed=True)


if __name__ == "__main__":
    command, *arguments = sys.argv[1:]
    if command == "seed-source":
        print(json.dumps(seed_source(Path(arguments[0]), Path(arguments[1]), json.load(sys.stdin))))
    elif command == "seed-occupied":
        print(json.dumps(seed_occupied(Path(arguments[0]))))
    elif command == "inventory":
        print(json.dumps(inventory(Path(arguments[0]))))
    elif command == "interrupt":
        interrupt(Path(arguments[0]), Path(arguments[1]))
    else:
        raise SystemExit(f"unknown command {command}")
