"""Policy-change events for review and export consumers (MU-24, MU-34)."""

from dataclasses import dataclass
from enum import StrEnum


class PolicyChangeCause(StrEnum):
    ENFORCEMENT = "enforcement"
    INSTANCE_DEFAULTS = "instance_defaults"


@dataclass(frozen=True)
class FilterPolicyChange:
    """One user's enforced filter policy moved to `policy_revision` in a committed write.

    `user_id` is a policy owner: each review it owns, and each export whose
    `policy_owner_id` it is, now has an effective view older than `policy_revision`.
    An export's target user and publishing actor are never inferred from this event.
    """

    user_id: int
    policy_revision: int
    cause: PolicyChangeCause
