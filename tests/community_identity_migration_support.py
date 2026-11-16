"""Populated community state at every supported source revision, in the legacy file layout.

Fixture version: mu06-community-fixture-1, extending mu03-activity-fixture-2. From 0003
the database has one enrolled connection plus a receipt, a vote, and a publish
operation; from 0007 the publish operation has its exact publication payload. Every
revision has a legacy `secrets/community` directory whose enrollment and pending key
recovery are real journal states, and a legacy authentication audit log.
"""

import json
import shutil
import socket
import sqlite3
from pathlib import Path

import pytest

from scanner.security.community_identity import (
    load_or_create_installation_identity,
    save_identity_registration,
)
from scanner.security.community_identity_homes import return_identity_to_incoming
from scanner.security.community_key_recovery import prepare_recovery
from scanner.security.private_identity_files import DIRECTORY, HOMES, IdentityHome
from tests.activity_migration_support import ACTIVITY, SUPPORTED, _upgrade, build_activity_database

PERSONAL_FILTERS = "0012_personal_filter_values"
SOURCES = [*SUPPORTED, PERSONAL_FILTERS]
SERVICE = "https://community.test"
AUDIENCE = "community.test"
CONTRIBUTOR = "contributor-0001"
COMMUNITY_TABLES = (
    "community_connections",
    "community_operations",
    "community_publications",
    "community_imports",
)
_TIME = "2026-02-01T00:00:00+00:00"
_LEGACY_AUDIT = (
    '{"schema_version":1,"event":"auth.login","outcome":"success","actor":"administrator",'
    '"admin_user_id":7,"request_id":"5f0c3f6e-8f3f-4f55-9c55-51b8f3c6f0a1"}\n'
)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Fail every socket connection, so no test can reach a community service."""

    def refuse(*_args, **_kwargs):
        raise AssertionError("MU-06 tests must not open network connections")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)


def _has(database, table):
    return database.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone()


def build_legacy_identity(data_dir: Path) -> dict[str, bytes]:
    """Write an enrolled identity with a pending recovery where pre-0013 releases kept it."""
    staging = IdentityHome(Path(data_dir), "e" * 32)
    identity = load_or_create_installation_identity(staging)
    save_identity_registration(
        staging, contributor_id=CONTRIBUTOR, service_url=SERVICE, last_known_key_state="active"
    )
    prepare_recovery(staging, AUDIENCE, identity.key_id)
    return_identity_to_incoming(staging)
    shutil.rmtree(Path(data_dir) / HOMES)
    return identity_files(Path(data_dir) / DIRECTORY)


def identity_files(root: Path) -> dict[str, bytes]:
    """Identity file names and bytes in one directory, without lock files."""
    return {
        entry.name: entry.read_bytes()
        for entry in sorted(root.iterdir())
        if not entry.name.endswith(".lock")
    }


def build_community_database(path: Path, media_dir: Path, revision, *, administrator=True):
    """Create the populated database, legacy identity, and audit log; return the files."""
    target = ACTIVITY if revision == PERSONAL_FILTERS else revision
    build_activity_database(path, media_dir, target, administrator=administrator)
    if revision == PERSONAL_FILTERS:
        _upgrade(path, PERSONAL_FILTERS)
    return add_community_state(path)


def add_community_state(path: Path) -> dict[str, bytes]:
    """Add the legacy identity, community rows, and audit log beside a built database.

    Return the identity file names and bytes.
    """
    files = build_legacy_identity(path.parent)
    manifest = json.loads(files["identity.json"])
    with sqlite3.connect(path) as database:
        if _has(database, "community_connections"):
            database.execute(
                "INSERT INTO community_connections (service_url, contributor_id, "
                "installation_id, key_id, sharing_enabled, consent_version, consented_at) "
                "VALUES (?, ?, ?, ?, 1, 'consent-1', ?)",
                (SERVICE, CONTRIBUTOR, manifest["installation_id"], manifest["key_id"], _TIME),
            )
            operations = (
                ("op-receipt", "receipt", "acknowledged", None, None, "receipt-1", None),
                ("op-vote", "vote", "submitted", None, None, None, "like"),
                ("op-publish", "publish", "pending", "legacy-scan-001", "consent-1", None, None),
            )
            for operation_id, kind, state, scan_id, consent, receipt, vote in operations:
                database.execute(
                    "INSERT INTO community_operations (operation_id, service_url, kind, state, "
                    "contributor_id, installation_id, contribution_id, revision_id, digest, "
                    "scan_id, consent_version, receipt, vote, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, 'root', 'revision', 'sha256:123', ?, ?, ?, ?, ?)",
                    (
                        operation_id,
                        SERVICE,
                        kind,
                        state,
                        CONTRIBUTOR,
                        manifest["installation_id"],
                        scan_id,
                        consent,
                        receipt,
                        vote,
                        _TIME,
                    ),
                )
        if _has(database, "community_publications"):
            database.execute(
                "INSERT INTO community_publications (operation_id, artifact_bytes, "
                "manifest_bytes, signature_bytes, manifest_digest, signature_digest, "
                "preview_digest, consent_token, author_key_id, source_kind, source_id, "
                "cancel_requested, error_code, result_json) VALUES ('op-publish', ?, ?, ?, "
                "?, ?, ?, ?, ?, 'scan', 'legacy-scan-001', 0, NULL, NULL)",
                (
                    b"\x1f\x8b artifact",
                    b'{"manifest":1}',
                    b"\x00signature",
                    "a" * 64,
                    "b" * 64,
                    "c" * 64,
                    "d" * 64,
                    manifest["key_id"],
                ),
            )
    logs = path.parent.parent / "logs"
    logs.mkdir(mode=0o700, exist_ok=True)
    (logs / "auth-audit.jsonl").write_text(_LEGACY_AUDIT)
    return files
