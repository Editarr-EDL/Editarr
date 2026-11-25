"""Capability rows for the matrix's exports, filters, and community sections."""

from __future__ import annotations

from .capability_ids import Capability as C
from .capability_spec import CapabilitySpec, spec
from .vocabulary import ADMINS, EVERYONE, NOBODY, Authentication, OwnerPower, Target

_OWN = Target.OWN_RESOURCE
_OTHER = Target.OTHER_RESOURCE

_OWN_EXPORTS = "Download/publish/delete own personal EDL/MCF"
_OTHER_EXPORTS = "Delete another user's personal exports"
_DEFAULTS = "Publish/replace/delete item-default EDL/MCF"
_OWN_PREFERENCES = "Edit own non-enforced filter preferences"
_INSTANCE_RULES = "Edit instance selection rules/severity defaults"
_INSTANCE_MODE = "Change instance default filter mode"
_ASSIGN_MODE = "Assign an individual user's filter mode"
_ENFORCE = "Enforce individual rules/subcategory severities"
_FRESH = Authentication.FRESH_INTERACTIVE

_FIND = "Search/find and download community master filter sets"
_VOTE = "Vote/submit feedback"
_PUBLISH_OWN = "Publish own review or shared scan evidence"
_PUBLISH_OTHER = "Publish another user's review"
_PUBLICATIONS = "Check/retry/retract publications"
_IDENTITY = "Manage connection/profile/enrollment/disconnection/key recovery"

EXPORT_CAPABILITIES: tuple[CapabilitySpec, ...] = (
    spec(C.EXPORTS_PERSONAL_DOWNLOAD_OWN, _OWN, EVERYONE, _OWN_EXPORTS),
    spec(C.EXPORTS_PERSONAL_PUBLISH_OWN, _OWN, EVERYONE, _OWN_EXPORTS),
    spec(C.EXPORTS_PERSONAL_DELETE_OWN, _OWN, EVERYONE, _OWN_EXPORTS),
    spec(C.EXPORTS_PERSONAL_DELETE_OTHER, _OTHER, ADMINS, _OTHER_EXPORTS),
    spec(C.EXPORTS_DEFAULT_PUBLISH, Target.MEDIA, ADMINS, _DEFAULTS),
    spec(C.EXPORTS_DEFAULT_DELETE, Target.MEDIA, ADMINS, _DEFAULTS),
    spec(C.FILTERS_PREFERENCES_EDIT_OWN, Target.SELF, EVERYONE, _OWN_PREFERENCES),
    spec(C.FILTERS_PREFERENCES_EDIT_OTHER, Target.OTHER_ACCOUNT, NOBODY, _OWN_PREFERENCES),
    spec(C.FILTERS_INSTANCE_DEFAULTS_EDIT, Target.INSTANCE, NOBODY, _INSTANCE_RULES),
    spec(C.FILTERS_INSTANCE_MODE_EDIT, Target.INSTANCE, ADMINS, _INSTANCE_MODE),
    spec(C.FILTERS_MODE_ASSIGN_OTHER, Target.OTHER_ACCOUNT, ADMINS, _ASSIGN_MODE),
    spec(
        C.FILTERS_POLICY_ENFORCE,
        Target.OTHER_ACCOUNT,
        NOBODY,
        _ENFORCE,
        authentication=_FRESH,
        owner_power=OwnerPower.MANAGE_ENFORCED_FILTER_POLICY,
    ),
)

COMMUNITY_CAPABILITIES: tuple[CapabilitySpec, ...] = (
    spec(C.COMMUNITY_SEARCH, Target.MEDIA, EVERYONE, _FIND),
    spec(C.COMMUNITY_DOWNLOAD, Target.MEDIA, EVERYONE, _FIND),
    spec(C.COMMUNITY_FEEDBACK_SUBMIT, Target.SELF, EVERYONE, _VOTE),
    spec(C.COMMUNITY_PUBLISH_OWN_REVIEW, _OWN, EVERYONE, _PUBLISH_OWN),
    spec(C.COMMUNITY_PUBLISH_SCAN_EVIDENCE, Target.MEDIA, EVERYONE, _PUBLISH_OWN),
    spec(C.COMMUNITY_PUBLISH_OTHER_REVIEW, _OTHER, ADMINS, _PUBLISH_OTHER),
    spec(C.COMMUNITY_PUBLICATIONS_CHECK_OWN, _OWN, EVERYONE, _PUBLICATIONS),
    spec(C.COMMUNITY_PUBLICATIONS_CHECK_OTHER, _OTHER, ADMINS, _PUBLICATIONS),
    spec(C.COMMUNITY_PUBLICATIONS_RETRY_OWN, _OWN, EVERYONE, _PUBLICATIONS),
    spec(C.COMMUNITY_PUBLICATIONS_RETRY_OTHER, _OTHER, ADMINS, _PUBLICATIONS),
    spec(C.COMMUNITY_PUBLICATIONS_RETRACT_OWN, _OWN, EVERYONE, _PUBLICATIONS),
    spec(C.COMMUNITY_PUBLICATIONS_RETRACT_OTHER, _OTHER, ADMINS, _PUBLICATIONS),
    spec(C.COMMUNITY_IDENTITY_VIEW_OWN, Target.SELF, EVERYONE, _IDENTITY),
    spec(C.COMMUNITY_IDENTITY_VIEW_OTHER, Target.OTHER_ACCOUNT, ADMINS, _IDENTITY),
    spec(C.COMMUNITY_IDENTITY_ENROLL_OWN, Target.SELF, EVERYONE, _IDENTITY),
    spec(C.COMMUNITY_IDENTITY_ENROLL_OTHER, Target.OTHER_ACCOUNT, ADMINS, _IDENTITY),
    spec(C.COMMUNITY_IDENTITY_PROFILE_OWN, Target.SELF, EVERYONE, _IDENTITY),
    spec(C.COMMUNITY_IDENTITY_PROFILE_OTHER, Target.OTHER_ACCOUNT, ADMINS, _IDENTITY),
    spec(C.COMMUNITY_IDENTITY_DISCONNECT_OWN, Target.SELF, EVERYONE, _IDENTITY),
    spec(C.COMMUNITY_IDENTITY_DISCONNECT_OTHER, Target.OTHER_ACCOUNT, ADMINS, _IDENTITY),
    spec(C.COMMUNITY_IDENTITY_KEYS_OWN, Target.SELF, EVERYONE, _IDENTITY),
    spec(C.COMMUNITY_IDENTITY_KEYS_OTHER, Target.OTHER_ACCOUNT, ADMINS, _IDENTITY),
)
