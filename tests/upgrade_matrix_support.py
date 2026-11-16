"""Representative state for each upgrade-matrix source revision, and what the upgrade keeps.

``STATES`` lists every value the matrix holds at a source revision, literally, with
the first revision that holds it. Values the shared fixture builders write have no
seed; state that later revisions introduced is seeded here, directly at each source
revision that holds it. ``after`` is the value at head when the upgrade transforms
it. ``content`` and ``unchanged`` compare every other column the source and head
schemas share, so a value no state names is still compared.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from tests.activity_migration_support import _clone

_TIME = "2026-10-31 12:00:00.000000"
_AVATAR = b"\x89PNG synthetic owner avatar"
_RETAINED = b"retained personal export bytes\n"
_PERSONAL_PATH = "/synthetic/exports/personal.edl"
_LINKED_PATH = "/synthetic/exports/linked/personal.edl"
_LINKED_TO = "a" * 32
_BLOCKED = "b" * 32
_JOB = {"state": "queued", "policy_owner_id": 1, "policy_revision": 1, "attempts": 0}
_SNAPSHOT = {"version": "editarr-export-snapshot-1", "publication_id": "mu45-personal"}
_REGENERATION = ("queued", 0.0, 0, None, 1, None, None, 1)

REVIEWS = (
    "SELECT id, name, revision, content_revision, edl_content_revision, mcf_content_revision "
    "FROM saved_reviews ORDER BY id"
)
REVIEW_CONTENT = (
    "SELECT review_id, id, source_scan_id, source_finding_id, label, start_seconds, "
    "end_seconds, confidence, edit_action FROM review_filters ORDER BY review_id, id"
)
SCANS = "SELECT id, name, origin, status FROM scans ORDER BY id"
QUEUE = "SELECT scan_id, sort_order FROM scan_queue ORDER BY scan_id"
EXPORTS = (
    "SELECT id, media_item_id, media_source_id, format, exported_at, review_id, "
    "exported_format_revision FROM media_item_exports WHERE id < 3 ORDER BY id"
)
EXPORT_SCANS = "SELECT media_item_export_id, scan_id, role FROM media_item_export_scans ORDER BY 1"
OPERATIONS = (
    "SELECT operation_id, kind, state, scan_id, consent_version, receipt, vote "
    "FROM community_operations ORDER BY operation_id"
)
PUBLICATIONS = (
    "SELECT operation_id, artifact_bytes, manifest_bytes, signature_bytes, source_kind, "
    "source_id, cancel_requested FROM community_publications"
)
PROFILES = "SELECT user_id, display_name, email, timezone, language FROM user_profiles"
AVATARS = "SELECT user_id, digest, image FROM user_avatars"
SYNC = (
    "SELECT user_id, upstream_state, jellyfin_name, is_administrator, media_policy, "
    "last_outcome FROM jellyfin_account_sync"
)
REPAIRS = "SELECT review_id, missing_scan_id, preserved_json FROM review_scan_repairs"
PERSONAL = (
    "SELECT id, scope, target_user_id, actor_id, policy_owner_id, content_bytes, "
    "destination_path, json(snapshot_json) FROM media_item_exports WHERE scope = 'personal'"
)
REGENERATIONS = (
    "SELECT state, due_at, attempts, last_attempt_at, policy_revision, claim_token, "
    "failure_reason, policy_owner_id FROM media_item_export_regenerations WHERE export_id = 3"
)
TRANSITIONS = (
    "SELECT user_id, kind, state, to_server_id, to_provider_user_id, failure_reason "
    "FROM account_link_transitions"
)
MOVES = (
    "SELECT export_id, old_path, new_path, old_sha256, new_sha256, outcome, requeued "
    "FROM account_link_export_moves"
)
BLOCKS = (
    "SELECT server_id, provider_user_id, name, blocked_by, blocked_at FROM jellyfin_user_blocks"
)


def _document(**extra):
    return json.dumps({**_SNAPSHOT, **extra}, sort_keys=True, separators=(",", ":"))


def _personal(document):
    return [(3, "personal", 1, 1, 1, _RETAINED, _PERSONAL_PATH, document)]


def _seed_profile(database):
    """Give the owner a profile, replacing the one 0030 makes from an unlinked owner's name."""
    database.execute(
        "INSERT OR REPLACE INTO user_profiles (user_id, display_name, email, timezone, "
        "language, last_login_at, updated_at) VALUES (1, 'Fixture Owner', 'owner@example.test', "
        "'Europe/London', 'en-GB', ?, ?)",
        (_TIME, _TIME),
    )
    database.execute(
        "INSERT INTO user_avatars (user_id, digest, image, updated_at) VALUES (1, ?, ?, ?)",
        (hashlib.sha256(_AVATAR).hexdigest(), _AVATAR, _TIME),
    )


def _seed_sync(database):
    database.execute(
        "INSERT INTO jellyfin_account_sync (user_id, upstream_state, jellyfin_name, avatar_tag, "
        "is_administrator, media_policy, verified_at, last_attempt_at, last_outcome, "
        "last_failure, updated_at) VALUES (1, 'enabled', 'Owner', NULL, 1, "
        "'{\"all_libraries\":true}', ?, ?, 'verified', NULL, ?)",
        (_TIME, _TIME, _TIME),
    )


def _seed_repair(database):
    database.execute(
        "INSERT INTO review_scan_repairs (review_id, missing_scan_id, detected_at, "
        "preserved_json, community_json, community_unavailable_reason) VALUES "
        "('review-legacy-001', 'missing-scan-404', ?, '{\"scan_findings\":[]}', NULL, NULL)",
        (_TIME,),
    )


def _seed_personal(database, document):
    _clone(
        database,
        "media_item_exports",
        1,
        id=3,
        media_source_id="mu45-personal",
        scope="personal",
        target_user_id=1,
        target_export_id="local-" + "e" * 32,
        actor_id=1,
        policy_owner_id=1,
        original_author_id=1,
        policy_revision=0,
        content_bytes=_RETAINED,
        destination_path=_PERSONAL_PATH,
        snapshot_json=document,
    )


def _seed_json_job(database):
    _seed_personal(database, _document(regeneration=_JOB))


def _seed_queued_job(database):
    _seed_personal(database, _document())
    database.execute(
        "INSERT INTO media_item_export_regenerations (export_id, state, due_at, attempts, "
        "last_attempt_at, policy_revision, claim_token, failure_reason, policy_owner_id) "
        "VALUES (3, 'queued', 0, 0, NULL, 1, NULL, NULL, 1)"
    )


def _seed_block(database):
    database.execute(
        "INSERT INTO jellyfin_user_blocks (server_id, provider_user_id, name, blocked_by, "
        "blocked_at) VALUES ('server', ?, 'Blocked Member', 1, ?)",
        (_BLOCKED, _TIME),
    )


def _seed_link(database):
    transition = database.execute(
        "INSERT INTO account_link_transitions (user_id, actor_id, kind, state, to_server_id, "
        "to_provider_user_id, from_export_id, created_at, updated_at) VALUES "
        "(1, 1, 'link', 'writing', 'server', ?, ?, ?, ?)",
        (_LINKED_TO, "local-" + "e" * 32, _TIME, _TIME),
    ).lastrowid
    database.execute(
        "INSERT INTO account_link_export_moves (transition_id, export_id, old_path, new_path, "
        "old_sha256, new_sha256) VALUES (?, 3, ?, ?, ?, NULL)",
        (transition, _PERSONAL_PATH, _LINKED_PATH, hashlib.sha256(_RETAINED).hexdigest()),
    )


@dataclass(frozen=True)
class State:
    """Rows held from ``since`` (until ``until``), seeded here or by the fixture builders."""

    since: str | None
    before: dict[str, list]
    after: dict[str, list] = field(default_factory=dict)
    seed: Callable[[sqlite3.Connection], None] | None = None
    until: str | None = None

    def holds(self, source):
        reached = self.since is None or (source is not None and source >= self.since)
        return reached and (self.until is None or source is None or source < self.until)


STATES = (
    State(
        None,
        {
            REVIEWS: [
                ("review-imported-002", "Imported review", 5, 4, 3, 2),
                ("review-legacy-001", "Synthetic review", 2, 2, 1, 0),
            ],
            REVIEW_CONTENT: [
                (
                    "review-legacy-001",
                    "review-filter-001",
                    "legacy-scan-001",
                    101,
                    "Synthetic finding",
                    15.0,
                    17.0,
                    0.9,
                    "mute",
                )
            ],
            SCANS: [
                ("imported-scan-003", "Imported specimen", "imported", "complete"),
                ("legacy-scan-001", "Legacy specimen", "generated", "complete"),
                ("queued-scan-002", "Queued specimen", "generated", "queued"),
            ],
            QUEUE: [("queued-scan-002", 0)],
            EXPORTS: [
                (
                    1,
                    "media-legacy",
                    "",
                    "edl",
                    "2026-02-01 00:00:00.000000",
                    "review-legacy-001",
                    1,
                ),
                (2, "media-legacy", "", "mcf", "2026-02-01 00:00:00.000000", None, None),
            ],
            EXPORT_SCANS: [(1, "legacy-scan-001", "audio"), (2, "imported-scan-003", "both")],
        },
    ),
    State(
        "0003_local_community",
        {
            OPERATIONS: [
                ("op-publish", "publish", "pending", "legacy-scan-001", "consent-1", None, None),
                ("op-receipt", "receipt", "acknowledged", None, None, "receipt-1", None),
                ("op-vote", "vote", "submitted", None, None, None, "like"),
            ]
        },
    ),
    State(
        "0007_community_publications",
        {
            PUBLICATIONS: [
                (
                    "op-publish",
                    b"\x1f\x8b artifact",
                    b'{"manifest":1}',
                    b"\x00signature",
                    "scan",
                    "legacy-scan-001",
                    0,
                )
            ]
        },
    ),
    State(
        "0017_user_profiles",
        {
            PROFILES: [(1, "Fixture Owner", "owner@example.test", "Europe/London", "en-GB")],
            AVATARS: [(1, hashlib.sha256(_AVATAR).hexdigest(), _AVATAR)],
        },
        seed=_seed_profile,
    ),
    State(
        "0018_jellyfin_account_sync",
        {SYNC: [(1, "enabled", "Owner", 1, '{"all_libraries":true}', "verified")]},
        seed=_seed_sync,
    ),
    State(
        "0020_review_scan_repairs",
        {REPAIRS: [("review-legacy-001", "missing-scan-404", '{"scan_findings":[]}')]},
        seed=_seed_repair,
    ),
    # 0022 moves a regeneration job out of the snapshot JSON into its own table.
    State(
        "0021_export_snapshots",
        {PERSONAL: _personal(_document(regeneration=_JOB))},
        after={PERSONAL: _personal(_document()), REGENERATIONS: [_REGENERATION]},
        seed=_seed_json_job,
        until="0022_export_regenerations",
    ),
    State(
        "0022_export_regenerations",
        {PERSONAL: _personal(_document()), REGENERATIONS: [_REGENERATION]},
        seed=_seed_queued_job,
    ),
    State(
        "0023_account_link_transitions",
        {
            TRANSITIONS: [(1, "link", "writing", "server", _LINKED_TO, None)],
            MOVES: [
                (
                    3,
                    _PERSONAL_PATH,
                    _LINKED_PATH,
                    hashlib.sha256(_RETAINED).hexdigest(),
                    None,
                    None,
                    0,
                )
            ],
        },
        seed=_seed_link,
    ),
    State(
        "0029_jellyfin_user_blocks",
        {BLOCKS: [("server", _BLOCKED, "Blocked Member", 1, _TIME)]},
        seed=_seed_block,
    ),
)


def held(source):
    return [state for state in STATES if state.holds(source)]


def seed(path: Path, source):
    with sqlite3.connect(path) as database:
        for state in held(source):
            if state.seed:
                state.seed(database)


def read(path: Path, queries):
    with sqlite3.connect(path) as database:
        return {query: database.execute(query).fetchall() for query in queries}


def expected(source, *, upgraded):
    rows = {}
    for state in held(source):
        rows |= state.before
        if upgraded:
            rows |= state.after
    return rows


def _without_jobs(value):
    if value is None:
        return None
    document = json.loads(value)
    document.pop("regeneration", None)
    document.pop("recovery_required", None)
    return json.dumps(document, sort_keys=True)


def _canonical(value):
    return None if value is None else json.dumps(json.loads(value), sort_keys=True)


# Column values an upgrade may change, as (source-side, head-side) normalizers; None
# skips the column. Upgrades from released revisions revoke every session (asserted
# by the matrix); later development revisions need not (ruling
# wave16-upgrade-session-revocation), so the matrix pins neither outcome here.
PERMITTED = {
    ("auth_sessions", "revoked_at"): None,
    # 0030 disables the unlinked owner and moves its username to its display name,
    # adding a profile row if it had none; the matrix pins its new state on its own.
    **{
        ("users", name): None
        for name in (
            "status",
            "disable_cause",
            "permission_revision",
            "updated_at",
            "username",
            "normalized_username",
        )
    },
    **{("admin_users", name): None for name in ("updated_at", "username", "normalized_username")},
    **{("user_profiles", name): None for name in ("display_name", "updated_at")},
    ("media_item_exports", "snapshot_json"): (_without_jobs, _canonical),
}
# 0010 keeps the administrator's row as the owner in a renamed table.
RENAMED = {"admin_users": "users"}
_EXCEPTION_COLUMNS = ("user_id", "source_kind", "server_key", "item_key", "granted_at")


def content(path: Path):
    """Every table's column names and rows."""
    with sqlite3.connect(path) as database:
        tables = [
            name
            for (name,) in database.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name != 'alembic_version'"
            )
        ]
        result = {
            table: (
                [row[1] for row in database.execute(f'PRAGMA table_info("{table}")')],
                database.execute(f'SELECT * FROM "{table}"').fetchall(),
            )
            for table in tables
        }
    # 0025 and 0026 move access rows here; put them back in their source tables, including
    # the exception table that 0026 drops.
    if "user_profiles" in result:
        columns, rows = result["user_profiles"]
        kept = [
            at
            for at, name in enumerate(columns)
            if name != "user_id" and ("user_profiles", name) not in PERMITTED
        ]
        result["user_profiles"] = (columns, [row for row in rows if any(row[at] for at in kept)])
    archived = result.pop("archived_media_access_rows", None)
    if archived is not None:
        result.setdefault("media_access_exceptions", (list(_EXCEPTION_COLUMNS), []))
    for _id, source_table, payload in (archived or ([], []))[1]:
        columns, rows = result[source_table]
        row = json.loads(payload)
        rows.append(tuple(row[column] for column in columns))
    return result


def _projected(table, columns, rows, shared, side):
    normalizers = [PERMITTED.get((table, name), (None, None)) for name in shared]
    positions = [columns.index(name) for name in shared]
    return sorted(
        (
            tuple(
                value if normalize[side] is None else normalize[side](value)
                for value, normalize in zip((row[at] for at in positions), normalizers)
            )
            for row in rows
        ),
        key=repr,
    )


def unchanged(before, after):
    """Tables whose shared, non-skipped columns differ between source and head."""
    changed = []
    for table, (columns, rows) in before.items():
        head = RENAMED.get(table, table)
        if head not in after:
            changed.append(table)
            continue
        head_columns, head_rows = after[head]
        shared = [
            name
            for name in columns
            if name in head_columns and PERMITTED.get((table, name), ()) is not None
        ]
        if _projected(table, columns, rows, shared, 0) != _projected(
            head, head_columns, head_rows, shared, 1
        ):
            changed.append(table)
    return changed
