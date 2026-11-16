"""Safe operation identities known from a producer's authorization decision."""

from scanner.security.access.capability_ids import Capability as C

from .actions import AuditAction as A

_VARIANTS: dict[tuple[A, C], tuple[str, str | bool]] = {
    (A.ACCOUNT_CREATE, C.ACCOUNTS_JELLYFIN_IMPORT): ("method", "jellyfin_import"),
    (A.ACCOUNT_STATUS, C.ACCOUNTS_ENABLE_OTHER): ("status", "active"),
    (A.ACCOUNT_STATUS, C.ACCOUNTS_DISABLE_OTHER): ("status", "disabled"),
    (A.API_KEY_ISSUE, C.API_KEYS_CREATE_OWN): ("replaced", False),
    (A.API_KEY_ISSUE, C.API_KEYS_ROTATE_OWN): ("replaced", True),
    (A.MEDIA_ACCESS_CHANGE, C.MEDIA_ACCESS_DEFAULTS_EDIT): ("change", "default_scope"),
    (A.MEDIA_ACCESS_CHANGE, C.MEDIA_ACCESS_USER_EDIT): ("change", "user_scope"),
    (A.PERMISSIONS_CHANGE, C.ROLES_ASSIGN): ("change", "role"),
    (A.PERMISSIONS_CHANGE, C.PERMISSIONS_OVERRIDES_EDIT): ("change", "overrides"),
    (A.PERMISSIONS_CHANGE, C.ROLES_DEFAULTS_EDIT): ("change", "role_defaults"),
    (A.CONNECTION_CHANGE, C.CONNECTIONS_EDIT): ("change", "saved"),
    (A.CONNECTION_CHANGE, C.CONNECTIONS_DISCONNECT): ("change", "disconnected"),
    (A.PATH_MAPPING_CHANGE, C.PATH_MAPPINGS_CREATE): ("change", "created"),
    (A.PATH_MAPPING_CHANGE, C.PATH_MAPPINGS_UPDATE): ("change", "updated"),
    (A.PATH_MAPPING_CHANGE, C.PATH_MAPPINGS_DELETE): ("change", "deleted"),
    (A.PATH_MAPPING_CHANGE, C.PATH_MAPPINGS_REORDER): ("change", "reordered"),
}


def operation_variant(action: A | None, capability: C | None) -> dict[str, str | bool]:
    """Return only unambiguous closed codes; missing knowledge is never inferred on read."""
    variant = _VARIANTS.get((action, capability))
    return {variant[0]: variant[1]} if variant is not None else {}
