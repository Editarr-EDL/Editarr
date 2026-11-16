"""Capability rows for the matrix's accounts and identity section and owner powers."""

from __future__ import annotations

from .capability_ids import Capability as C
from .capability_spec import CapabilitySpec, spec
from .vocabulary import ADMINS, EVERYONE, NOBODY, Authentication, OwnerPower, Target

_FRESH = Authentication.FRESH_INTERACTIVE

_OWN_PROFILE = "View own profile"
_LIST = "List/search users and see all approved list columns"
_IMPORT = "Import selected or all Jellyfin users"
_EDIT_OTHER = "Edit another account's profile"
_STATUS_OTHER = "Disable/re-enable/delete another account"
_REVOKE_OTHER = "Revoke another account's sessions"
_REVOKE_KEY_OTHER = "Revoke another account's API key"
_LINK = "Initiate linking/relinking with ownership proof"
_ADMINS = "Manage another admin account"
_SYNC = "Manually refresh Jellyfin user sync"
_UNBLOCK = "Unblock a Jellyfin user blocked when their account was deleted"
_MEDIA_ACCESS = "Manage local-user media defaults/individual media access"
_OWN_KEY = "Own API key creation/rotation/reveal/revocation"
_REVEAL_OTHER = "Reveal another user's API key"
_OWN_EDIT = "Own profile editing"
_OWN_MODE = "Choose own filter mode when no policy is enforced"

ACCOUNT_CAPABILITIES: tuple[CapabilitySpec, ...] = (
    spec(C.PROFILE_VIEW_OWN, Target.SELF, EVERYONE, _OWN_PROFILE),
    spec(C.PROFILE_EDIT_OWN, Target.SELF, EVERYONE, _OWN_EDIT),
    spec(C.ACCOUNTS_LIST, Target.INSTANCE, ADMINS, _LIST),
    spec(C.ACCOUNTS_JELLYFIN_IMPORT, Target.INSTANCE, ADMINS, _IMPORT),
    spec(C.ACCOUNTS_JELLYFIN_SYNC, Target.INSTANCE, ADMINS, _SYNC),
    # Blocking is part of deletion (`accounts.delete.other`); letting a user back in is the
    # owner's call unless the owner grants it. Ruling JF-05 fix round, 2026-11-04.
    spec(C.ACCOUNTS_JELLYFIN_UNBLOCK, Target.INSTANCE, NOBODY, _UNBLOCK, authentication=_FRESH),
    spec(C.ACCOUNTS_PROFILE_EDIT_OTHER, Target.OTHER_ACCOUNT, ADMINS, _EDIT_OTHER),
    spec(C.ACCOUNTS_DISABLE_OTHER, Target.OTHER_ACCOUNT, ADMINS, _STATUS_OTHER),
    spec(C.ACCOUNTS_ENABLE_OTHER, Target.OTHER_ACCOUNT, ADMINS, _STATUS_OTHER),
    # Every mode (retain, transfer, permanent); ruling wave13-mu38-transfer, 2026-10-28.
    spec(
        C.ACCOUNTS_DELETE_OTHER,
        Target.OTHER_ACCOUNT,
        ADMINS,
        _STATUS_OTHER,
        authentication=_FRESH,
    ),
    spec(C.ACCOUNTS_SESSIONS_REVOKE_OTHER, Target.OTHER_ACCOUNT, ADMINS, _REVOKE_OTHER),
    spec(C.ACCOUNTS_LINK_OWN, Target.SELF, ADMINS, _LINK, authentication=_FRESH),
    spec(C.ACCOUNTS_LINK_OTHER, Target.OTHER_ACCOUNT, ADMINS, _LINK, authentication=_FRESH),
    spec(C.ACCOUNTS_ADMINS_MANAGE, Target.OTHER_ACCOUNT, NOBODY, _ADMINS),
    spec(
        C.MEDIA_ACCESS_DEFAULTS_EDIT, Target.INSTANCE, ADMINS, _MEDIA_ACCESS, authentication=_FRESH
    ),
    spec(
        C.MEDIA_ACCESS_USER_EDIT, Target.OTHER_ACCOUNT, ADMINS, _MEDIA_ACCESS, authentication=_FRESH
    ),
    # Creating a key reveals it, so creation is treated as a rotation from no key.
    spec(C.API_KEYS_CREATE_OWN, Target.SELF, EVERYONE, _OWN_KEY, authentication=_FRESH),
    spec(C.API_KEYS_ROTATE_OWN, Target.SELF, EVERYONE, _OWN_KEY, authentication=_FRESH),
    spec(C.API_KEYS_REVEAL_OWN, Target.SELF, EVERYONE, _OWN_KEY, authentication=_FRESH),
    spec(C.API_KEYS_REVOKE_OWN, Target.SELF, EVERYONE, _OWN_KEY),
    spec(C.API_KEYS_REVOKE_OTHER, Target.OTHER_ACCOUNT, ADMINS, _REVOKE_KEY_OTHER),
    spec(
        C.API_KEYS_ISSUE_OTHER,
        Target.OTHER_ACCOUNT,
        NOBODY,
        _OWN_KEY,
        authentication=_FRESH,
        owner_power=OwnerPower.REVEAL_OTHERS_API_KEYS,
    ),
    spec(
        C.API_KEYS_REVEAL_OTHER,
        Target.OTHER_ACCOUNT,
        NOBODY,
        _REVEAL_OTHER,
        authentication=_FRESH,
        owner_power=OwnerPower.REVEAL_OTHERS_API_KEYS,
    ),
    spec(C.FILTERS_MODE_CHOOSE_OWN, Target.SELF, EVERYONE, _OWN_MODE),
    spec(
        C.OWNERSHIP_TRANSFER,
        Target.OTHER_ACCOUNT,
        NOBODY,
        authentication=_FRESH,
        owner_power=OwnerPower.TRANSFER_OWNERSHIP,
    ),
    spec(
        C.ROLES_DEFAULTS_EDIT,
        Target.INSTANCE,
        NOBODY,
        authentication=_FRESH,
        owner_power=OwnerPower.EDIT_ROLE_DEFAULTS,
    ),
    spec(
        C.ROLES_ASSIGN,
        Target.OTHER_ACCOUNT,
        NOBODY,
        authentication=_FRESH,
        owner_power=OwnerPower.GRANT_ROLES_OR_OVERRIDES,
    ),
    spec(
        C.PERMISSIONS_OVERRIDES_EDIT,
        Target.OTHER_ACCOUNT,
        NOBODY,
        authentication=_FRESH,
        owner_power=OwnerPower.GRANT_ROLES_OR_OVERRIDES,
    ),
)
