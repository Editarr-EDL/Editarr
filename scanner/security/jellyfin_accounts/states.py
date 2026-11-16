"""The typed transition of one linked account for one verification outcome.

Pure: callers read the account, ask `transition` what changes, and store the
answer in one transaction. Confirmed facts (a snapshot or a deletion) are the
only things that move verification forward; an outage only records the
attempt, so last-verified access lasts exactly `VERIFICATION_GRACE` after the
last success, and never longer when the clock steps back. Jellyfin never
changes a role after creation and never disables the owner. Jellyfin
enabling a user again enables the account only when Jellyfin disabled it: a
local disable and a deletion stay.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum

from scanner.integrations.jellyfin_user_snapshot import (
    JellyfinUserSnapshot,
    Unverified,
    UnverifiedReason,
    UserDeleted,
)
from scanner.security.access.identity import AccountStatus, DisableCause, ProviderLinkState
from scanner.security.access.link_state import elapsed_within
from scanner.security.access.vocabulary import Role


class UpstreamState(StrEnum):
    ENABLED = "enabled"
    DISABLED = "disabled"
    DELETED = "deleted"


class SyncOutcome(StrEnum):
    VERIFIED = "verified"
    DISABLED = "disabled"
    """Jellyfin confirmed the user is disabled."""
    DELETED = "deleted"
    """Jellyfin confirmed the user no longer exists."""
    UNVERIFIED = "unverified"
    """No new fact: an outage, a rejected key, or a malformed answer."""
    SERVER_REPLACED = "server_replaced"
    """Jellyfin is disconnected, or the configured server is another server."""


DISCONNECTED = "disconnected"
"""The failure recorded when no Jellyfin connection is configured."""


@dataclass(frozen=True)
class Disconnected:
    """No Jellyfin connection is configured, so no linked server can answer."""


Evidence = JellyfinUserSnapshot | UserDeleted | Unverified | Disconnected


@dataclass(frozen=True)
class LinkedAccount:
    """What a transition reads about one linked account."""

    user_id: int
    role: Role
    disable_cause: DisableCause | None
    """Why the account is disabled; None while it is active."""
    last_verified_at: datetime | None

    @property
    def status(self) -> AccountStatus:
        return AccountStatus.ACTIVE if self.disable_cause is None else AccountStatus.DISABLED


@dataclass(frozen=True)
class Transition:
    """What to store for one outcome. `None` fields other than `disable_cause` stay as they are."""

    outcome: SyncOutcome
    link_state: ProviderLinkState
    disable_cause: DisableCause | None
    """The account's cause afterwards. A new cause disables it and ends its sessions;
    None enables it. Its key and data stay either way."""
    upstream: UpstreamState | None = None
    snapshot: JellyfinUserSnapshot | None = None
    """A verified snapshot to store; it also moves `last_verified_at` to its time."""
    failure: str | None = None


def before_clock_step(change: Transition) -> Transition | None:
    """What a read begun before the clock was found stepped back may still store.

    Its time is on the old clock, so it never verifies: a verified answer
    changes nothing (None), and a disabled one disables without storing its
    snapshot or verification time. Deletions and outages only reduce
    access and apply as they are.
    """

    if change.outcome is SyncOutcome.VERIFIED:
        return None
    return replace(change, snapshot=None)


def starting_role(snapshot: JellyfinUserSnapshot) -> Role:
    """The role of an account created for `snapshot`; the owner may change it afterwards."""

    return Role.ADMIN if snapshot.is_administrator else Role.USER


def _cause(account: LinkedAccount, evidence: Evidence) -> DisableCause | None:
    """The cause Jellyfin's confirmed answer leaves; anything unconfirmed changes nothing."""

    current = account.disable_cause
    if account.role is Role.OWNER or current is DisableCause.LOCAL:
        return current
    if isinstance(evidence, UserDeleted):
        return DisableCause.UPSTREAM_DELETED
    if not isinstance(evidence, JellyfinUserSnapshot) or current is DisableCause.UPSTREAM_DELETED:
        return current
    return DisableCause.UPSTREAM_DISABLED if evidence.is_disabled else None


def transition(account: LinkedAccount, evidence: Evidence, now: datetime) -> Transition:
    """Decide the account's next stored state from one verification outcome at `now`."""

    cause = _cause(account, evidence)
    if isinstance(evidence, Disconnected):
        return Transition(
            SyncOutcome.SERVER_REPLACED,
            ProviderLinkState.SERVER_REPLACED,
            cause,
            failure=DISCONNECTED,
        )
    if isinstance(evidence, Unverified):
        if evidence.reason is UnverifiedReason.SERVER_MISMATCH:
            return Transition(
                SyncOutcome.SERVER_REPLACED,
                ProviderLinkState.SERVER_REPLACED,
                cause,
                failure=evidence.reason.value,
            )
        within = elapsed_within(account.last_verified_at, now)
        state = ProviderLinkState.OUTAGE_GRACE if within else ProviderLinkState.OUTAGE_EXPIRED
        return Transition(SyncOutcome.UNVERIFIED, state, cause, failure=evidence.reason.value)
    if isinstance(evidence, UserDeleted):
        return Transition(
            SyncOutcome.DELETED, ProviderLinkState.VERIFIED, cause, upstream=UpstreamState.DELETED
        )
    if evidence.is_disabled:
        return Transition(
            SyncOutcome.DISABLED,
            ProviderLinkState.VERIFIED,
            cause,
            upstream=UpstreamState.DISABLED,
            snapshot=evidence,
        )
    return Transition(
        SyncOutcome.VERIFIED,
        ProviderLinkState.VERIFIED,
        cause,
        upstream=UpstreamState.ENABLED,
        snapshot=evidence,
    )
