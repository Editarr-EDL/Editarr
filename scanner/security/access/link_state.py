"""How a linked account's Jellyfin verification age limits what it may do.

A linked account keeps last-verified access for `VERIFICATION_GRACE` after its
last successful verification, whatever the stored state says, so a stopped
scheduler cannot extend it. A clock that steps back never lengthens it either.
Once that runs out, or while the linked server is
replaced or disconnected, the account keeps only its own profile and key.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from .capability_ids import Capability
from .identity import ProviderLinkState

VERIFICATION_GRACE = timedelta(hours=24)
"""Last-verified access lasts this long after the last successful verification."""

BLOCKED_LINK_STATES = frozenset(
    {ProviderLinkState.OUTAGE_EXPIRED, ProviderLinkState.SERVER_REPLACED}
)

PROFILE_ONLY = frozenset(
    {
        Capability.PROFILE_VIEW_OWN,
        Capability.PROFILE_EDIT_OWN,
        Capability.API_KEYS_CREATE_OWN,
        Capability.API_KEYS_ROTATE_OWN,
        Capability.API_KEYS_REVEAL_OWN,
        Capability.API_KEYS_REVOKE_OWN,
    }
)
"""What a nonowner linked account keeps in a blocked link state: its own profile and key."""


def link_clock() -> datetime:
    """The time account loads judge verification age by; tests replace it."""

    return datetime.now(UTC)


def elapsed_within(
    since: datetime | None, now: datetime, span: timedelta = VERIFICATION_GRACE
) -> bool:
    """Whether `now` is at most `span` after `since`, boundary included.

    A `since` later than `now` means the clock stepped back after it, so the
    elapsed time is unknown and the answer is no.
    """

    return since is not None and since <= now and now - since <= span


def effective_link_state(
    stored: ProviderLinkState,
    last_verified_at: datetime | None,
    now: datetime,
    *,
    last_read_at: datetime | None,
) -> ProviderLinkState:
    """The stored state, expired once the last success is more than the grace period old.

    An identity never verified has no verified access to fall back on. The
    boundary itself is still inside the grace period, as in `decide_media`.
    `last_read_at` is when the latest Jellyfin read began; a `now` before it
    means the clock stepped back and no read has cleared the stored
    verifications yet, so last-verified access stops until one has.
    """

    if stored not in (ProviderLinkState.VERIFIED, ProviderLinkState.OUTAGE_GRACE):
        return stored
    if not elapsed_within(last_verified_at, now) or (
        last_read_at is not None and now < last_read_at
    ):
        return ProviderLinkState.OUTAGE_EXPIRED
    return stored
