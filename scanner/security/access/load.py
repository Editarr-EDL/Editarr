"""Read one account's live permission state for each request or job load.

Nothing here caches. Each call selects columns, not ORM entities, so a
long-lived session or worker never sees a frozen identity-map copy. The
account and its grants come from one statement, so a decision never mixes two
committed states. Stored rows are parsed at this boundary: an unknown
capability or an owner power grants nothing, and an unreadable value for a
delegable capability denies it.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import Integer, Row, Select, literal, select, true, type_coerce, union_all
from sqlalchemy.orm import Session

from scanner.db import (
    RoleCapabilityDefaultRow,
    UserCapabilityOverrideRow,
    UserProviderIdentityRow,
    UserRow,
)
from scanner.formats.export_layout import JellyfinUserId, LocalExportId
from scanner.persistence.rows.db_auth import AccountSettingsRow

from . import link_state as links
from .capability_ids import Capability
from .catalog import DELEGABLE
from .grants import GrantInputs, PrincipalAccess, effective_capabilities
from .identity import (
    Account,
    AccountStatus,
    CredentialKind,
    JellyfinServerId,
    PermissionRevision,
    Principal,
    ProviderIdentity,
    ProviderLinkState,
    UserId,
)
from .overrides import OverrideEffect
from .vocabulary import DELEGATED_ROLES, Role

_STORED_EFFECTS = {
    OverrideEffect.ALLOW.value: OverrideEffect.ALLOW,
    OverrideEffect.DENY.value: OverrideEffect.DENY,
}
_DEFAULT, _OVERRIDE = "default", "override"


def _delegable(raw: object) -> Capability | None:
    try:
        capability = Capability(raw)
    except ValueError:
        return None
    return capability if capability in DELEGABLE else None


def _grant_inputs(role: Role, rows: Sequence[Row]) -> GrantInputs:
    """Parse the role's changed defaults and the account's overrides, failing closed per row."""

    defaults: dict[Capability, bool] = {}
    overrides: dict[Capability, OverrideEffect] = {}
    unreadable: set[Capability] = set()
    if role in DELEGATED_ROLES:
        for row in rows:
            capability = _delegable(row.capability)
            if capability is None:
                continue
            if row.source == _DEFAULT:
                if type(row.value) is int and row.value in (0, 1):
                    defaults[capability] = bool(row.value)
                else:
                    unreadable.add(capability)
            else:
                effect = _STORED_EFFECTS.get(row.value) if isinstance(row.value, str) else None
                if effect is None:
                    unreadable.add(capability)
                else:
                    overrides[capability] = effect
    return GrantInputs(role, defaults, overrides, frozenset(unreadable))


def _snapshot(user_id: UserId) -> Select:
    """One statement for the account and its grants, so SQLite reads them from one snapshot.

    Separate statements outside a transaction each see the latest commit, which
    could pair an old role with a newer default. One row per grant; the account
    columns repeat, and an account without grants yields one row with a null source.
    """

    role = select(UserRow.role).where(UserRow.id == user_id).scalar_subquery()
    grants = union_all(
        select(
            literal(_DEFAULT).label("source"),
            RoleCapabilityDefaultRow.capability.label("capability"),
            type_coerce(RoleCapabilityDefaultRow.granted, Integer).label("value"),
        ).where(RoleCapabilityDefaultRow.role == role),
        select(
            literal(_OVERRIDE),
            UserCapabilityOverrideRow.capability,
            UserCapabilityOverrideRow.effect,
        ).where(UserCapabilityOverrideRow.user_id == user_id),
    ).subquery()
    return (
        select(
            UserRow.role,
            UserRow.status,
            UserRow.local_export_id,
            UserRow.permission_revision,
            UserProviderIdentityRow.server_id,
            UserProviderIdentityRow.provider_user_id,
            UserProviderIdentityRow.link_state,
            UserProviderIdentityRow.last_verified_at,
            select(AccountSettingsRow.jellyfin_read_at).scalar_subquery().label("last_read_at"),
            grants.c.source,
            grants.c.capability,
            grants.c.value,
        )
        .outerjoin(UserProviderIdentityRow, UserProviderIdentityRow.user_id == UserRow.id)
        .outerjoin(grants, true())
        .where(UserRow.id == user_id)
    )


def load_access(
    db: Session,
    user_id: UserId,
    credential: CredentialKind,
    session_id: int | None = None,
    *,
    now: datetime | None = None,
) -> PrincipalAccess | None:
    """Build the live principal for one request or job, or `None` when the account is gone.

    Reads with one SELECT in the caller's session without explicit transaction
    management. The session's autobegin and autoflush settings still apply.
    A linked account's link state is judged at `now` (default `link_clock()`).
    """

    rows = db.execute(_snapshot(user_id)).all()
    if not rows:
        return None
    found = rows[0]
    role = Role(found.role)
    linked = found.server_id is not None
    link_state = ProviderLinkState.UNLINKED
    if linked:
        verified, last_read = found.last_verified_at, found.last_read_at
        link_state = links.effective_link_state(
            ProviderLinkState(found.link_state),
            verified.replace(tzinfo=UTC) if verified is not None else None,
            now or links.link_clock(),
            last_read_at=last_read.replace(tzinfo=UTC) if last_read is not None else None,
        )
    account = Account(
        user_id=user_id,
        role=role,
        status=AccountStatus(found.status),
        local_export_id=LocalExportId(found.local_export_id),
        provider=(
            ProviderIdentity(
                JellyfinServerId(found.server_id), JellyfinUserId(found.provider_user_id)
            )
            if linked
            else None
        ),
        link_state=link_state,
    )
    capabilities = effective_capabilities(_grant_inputs(role, rows))
    if role is not Role.OWNER and link_state in links.BLOCKED_LINK_STATES:
        capabilities &= links.PROFILE_ONLY
    return PrincipalAccess(
        principal=Principal(account, credential, PermissionRevision(found.permission_revision)),
        capabilities=capabilities,
        session_id=session_id,
    )
