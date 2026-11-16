"""Account checks that an archive must pass on its own before it is restored.

`USER_REFERENCES` names every column that holds a `users.id`. Two have no
foreign key, so only this registry keeps them inside the archive's accounts.
"""

from __future__ import annotations

import sqlite3

from .restore_target import RestoreRefused

USER_REFERENCES = (
    ("account_dispositions", "user_id"),
    ("account_dispositions", "actor_id"),
    ("account_dispositions", "destination_id"),
    ("account_link_transitions", "user_id"),
    ("account_link_transitions", "actor_id"),
    ("account_transfer_moves", "user_id"),
    ("api_keys", "user_id"),
    ("audit_events", "actor_id"),
    ("audit_events", "target_user_id"),
    ("auth_sessions", "user_id"),
    ("community_identities", "user_id"),
    ("community_operations", "owner_id"),
    ("community_operations", "actor_id"),
    ("jellyfin_account_sync", "user_id"),
    ("jellyfin_user_blocks", "blocked_by"),
    ("media_access_scopes", "user_id"),
    ("media_item_exports", "target_user_id"),
    ("media_item_exports", "actor_id"),
    ("media_item_exports", "policy_owner_id"),
    ("media_item_exports", "original_author_id"),
    ("saved_reviews", "owner_id"),
    ("saved_reviews", "original_author_id"),
    ("scans", "initiator_id"),
    ("user_avatars", "user_id"),
    ("user_capability_overrides", "user_id"),
    ("user_filter_preferences", "user_id"),
    ("user_profiles", "user_id"),
    ("user_provider_identities", "user_id"),
    ("media_item_exports", "intended_policy_owner_id"),
    ("media_item_export_regenerations", "policy_owner_id"),
)

OWNER_COUNT = "restore_owner_count"
OWNER_CREDENTIAL = "restore_owner_credential"
UNKNOWN_ACCOUNT = "restore_unknown_account_reference"


def check_accounts(database: sqlite3.Connection) -> bool:
    """Refuse an archive database whose accounts cannot be restored as they are.

    It needs exactly one owner, or no accounts at all (work awaiting owner setup).
    The owner needs a Jellyfin link, the only way to sign in. Every user reference
    must name one of its `users` rows. Returns whether it holds an owner.
    """
    owners = database.execute(
        "SELECT EXISTS (SELECT 1 FROM user_provider_identities "
        "WHERE user_provider_identities.user_id = users.id) FROM users WHERE role = 'owner'"
    ).fetchall()
    accounts = database.execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None
    if len(owners) != 1 and (owners or accounts):
        raise RestoreRefused(OWNER_COUNT, "The backup's accounts need exactly one owner")
    if owners and not owners[0][0]:
        raise RestoreRefused(OWNER_CREDENTIAL, "The backup's owner has no Jellyfin link")
    for table, column in USER_REFERENCES:
        missing = database.execute(
            f'SELECT 1 FROM "{table}" WHERE "{column}" IS NOT NULL '
            f'AND "{column}" NOT IN (SELECT id FROM users) LIMIT 1'
        ).fetchone()
        if missing is not None:
            raise RestoreRefused(
                UNKNOWN_ACCOUNT, f"{table}.{column} names an account the backup does not hold"
            )
    return bool(owners)
