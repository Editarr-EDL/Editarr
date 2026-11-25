"""The complete capability inventory, invariants, account protection and contract digest."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from .capability_ids import Capability
from .capability_spec import CapabilitySpec
from .catalog_accounts import ACCOUNT_CAPABILITIES
from .catalog_media import MEDIA_CAPABILITIES
from .catalog_operations import OPERATION_CAPABILITIES
from .catalog_sharing import COMMUNITY_CAPABILITIES, EXPORT_CAPABILITIES
from .vocabulary import TARGET_RULES, AccountClass, OwnerPower

CAPABILITY_CONTRACT_VERSION = "editarr-capabilities-10"


def _index(rows: tuple[CapabilitySpec, ...]) -> Mapping[Capability, CapabilitySpec]:
    indexed: dict[Capability, CapabilitySpec] = {}
    for row in rows:
        if row.capability in indexed:
            raise ValueError(f"Duplicate capability row {row.capability}")
        indexed[row.capability] = row
    missing = set(Capability) - indexed.keys()
    if missing:
        raise ValueError(f"Capabilities without a row: {sorted(missing)}")
    return MappingProxyType(indexed)


CAPABILITIES: Mapping[Capability, CapabilitySpec] = _index(
    ACCOUNT_CAPABILITIES
    + MEDIA_CAPABILITIES
    + EXPORT_CAPABILITIES
    + COMMUNITY_CAPABILITIES
    + OPERATION_CAPABILITIES
)

DELEGABLE: frozenset[Capability] = frozenset(
    capability for capability, row in CAPABILITIES.items() if row.delegable
)
"""Capabilities whose role defaults are configurable and which accept overrides."""

ROLE_FIXED: frozenset[Capability] = frozenset(
    capability for capability, row in CAPABILITIES.items() if row.role_fixed
)
"""Capabilities held by exactly their default roles, whatever is stored."""


class Invariant(StrEnum):
    """Matrix rows enforced by absence of the feature rather than by a capability."""

    PERSONAL_SESSION_UI_EXCLUDED = "personal_session_ui_excluded"
    ORDINARY_REVIEW_SHARING_EXCLUDED = "ordinary_review_sharing_excluded"
    OPERATOR_DASHBOARD_OUT_OF_SCOPE = "operator_dashboard_out_of_scope"


INVARIANT_ROWS: Mapping[str, Invariant] = MappingProxyType(
    {
        "Personal active-session list/revocation UI": Invariant.PERSONAL_SESSION_UI_EXCLUDED,
        "Share a review with another ordinary user": Invariant.ORDINARY_REVIEW_SHARING_EXCLUDED,
        "Separate operator/moderation dashboard": Invariant.OPERATOR_DASHBOARD_OUT_OF_SCOPE,
    }
)
"""Matrix rows that map to an explicit invariant instead of a capability."""

ACCOUNT_PROTECTION: Mapping[AccountClass, Capability | OwnerPower | None] = MappingProxyType(
    {
        AccountClass.ORDINARY: None,
        AccountClass.ADMIN: Capability.ACCOUNTS_ADMINS_MANAGE,
        AccountClass.OWNER: OwnerPower.MANAGE_OWNER_ACCOUNT,
    }
)
"""The extra requirement for acting on another account of each class.

Applies to account-protected targets. `None` adds nothing, a capability must
also be held, and an owner power means only the owner may act, which can never
be another account.
"""


def contract_document() -> dict[str, Any]:
    """Return the canonical, JSON-serializable form that the digest covers."""

    return {
        "version": CAPABILITY_CONTRACT_VERSION,
        "capabilities": [
            {
                "id": row.capability.value,
                "target": row.target.value,
                "granted": sorted(role.value for role in row.granted),
                "authentication": row.authentication.value,
                "ownerPower": row.owner_power.value if row.owner_power else None,
                "roleFixed": row.role_fixed,
                "matrixRows": list(row.matrix_rows),
            }
            for row in sorted(CAPABILITIES.values(), key=lambda row: row.capability.value)
        ],
        "targets": {
            target.value: {
                "facet": rule.facet.value,
                "mediaScoped": rule.media_scoped,
                "accountProtected": rule.account_protected,
            }
            for target, rule in sorted(TARGET_RULES.items())
        },
        "accountProtection": {
            account.value: requirement.value if requirement else None
            for account, requirement in sorted(ACCOUNT_PROTECTION.items())
        },
        "invariantRows": {
            row: invariant.value for row, invariant in sorted(INVARIANT_ROWS.items())
        },
    }


def contract_digest() -> str:
    """SHA-256 of the canonical contract document; a change requires a version bump."""

    canonical = json.dumps(contract_document(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
