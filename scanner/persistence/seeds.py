"""Idempotent defaults for the current application schema."""

import secrets

from sqlalchemy import exists, select
from sqlalchemy.engine import Connection

from scanner.models import MCF_1_1_1_CATEGORIES, MCF_CATEGORY_TO_FAMILY, Severity

from .rows.db_catalog import FilterSettingsRow, McfSeverityDefaultRow
from .rows.db_community import CommunityIdentityRow
from .rows.db_policies import LOCAL_MEDIA_DEFAULT_SCOPE_ID, MediaAccessScopeRow


def seed_community_identity(connection: Connection) -> None:
    """Reserve the first identity directory, awaiting an owner, unless one exists."""
    if not connection.execute(select(exists().select_from(CommunityIdentityRow))).scalar():
        connection.execute(
            CommunityIdentityRow.__table__.insert(),
            {"credential_ref": secrets.token_hex(16), "user_id": None},
        )


def seed_media_access_default(connection: Connection) -> None:
    """Install the all-sources local media default unless a default already exists."""
    connection.execute(
        MediaAccessScopeRow.__table__.insert().prefix_with("OR IGNORE"),
        {"id": LOCAL_MEDIA_DEFAULT_SCOPE_ID, "user_id": None, "all_sources": True},
    )


def seed_filter_defaults(connection: Connection) -> None:
    """Install missing singleton and taxonomy defaults without changing existing values."""
    connection.execute(
        FilterSettingsRow.__table__.insert().prefix_with("OR IGNORE"),
        {"id": 1, "revision": 0},
    )
    # Persisted default positions are stable across taxonomy extensions.
    subcategories = sorted(
        MCF_CATEGORY_TO_FAMILY, key=lambda subcategory: subcategory in MCF_1_1_1_CATEGORIES
    )
    defaults = [
        {
            "settings_id": 1,
            "subcategory": subcategory,
            "position": position,
            "severity": Severity.MEDIUM,
        }
        for position, subcategory in enumerate(subcategories)
    ]
    connection.execute(McfSeverityDefaultRow.__table__.insert().prefix_with("OR IGNORE"), defaults)
