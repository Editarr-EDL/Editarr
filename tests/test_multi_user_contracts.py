"""Identity, provenance, and API-shape contracts."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from scanner.formats.export_layout import LocalExportId, parse_jellyfin_user_id
from scanner.models import ExportFormat
from scanner.security.access.api_shapes import (
    ACCESS_ERROR_STATUS,
    AccessError,
    AccessErrorCode,
    CapabilityPayload,
)
from scanner.security.access.capability_ids import Capability
from scanner.security.access.identity import (
    Account,
    AccountStatus,
    CredentialKind,
    JellyfinServerId,
    ProviderIdentity,
    ProviderLinkState,
    UserId,
)
from scanner.security.access.records import (
    AuditEvent,
    AuditOutcome,
    ExportProvenance,
    ItemDefaultTarget,
    PersonalTarget,
    PolicyRevision,
    ReviewOwnership,
)
from scanner.security.access.vocabulary import AccountClass, Role

LOCAL_ID = LocalExportId("local-9c2b7d4e1f6a48b3a5c0d9e8f7a6b5c4")
JELLYFIN = ProviderIdentity(
    JellyfinServerId("server-1"), parse_jellyfin_user_id("5A4DE0C2-9B1E-4F3A-8C6D-0E1F2A3B4C5D")
)


def _account(**overrides) -> Account:
    values = {
        "user_id": UserId(3),
        "role": Role.USER,
        "status": AccountStatus.ACTIVE,
        "local_export_id": LOCAL_ID,
        "provider": None,
        "link_state": ProviderLinkState.UNLINKED,
    } | overrides
    return Account(**values)


def test_export_id_follows_the_provider_link():
    assert _account().export_id == LOCAL_ID
    linked = _account(provider=JELLYFIN, link_state=ProviderLinkState.VERIFIED)
    assert linked.export_id == JELLYFIN.user_id
    assert linked.account_class is AccountClass.ORDINARY


@pytest.mark.parametrize(
    "overrides",
    [
        {"role": Role.OWNER},
        {
            "role": Role.OWNER,
            "status": AccountStatus.DISABLED,
            "provider": JELLYFIN,
            "link_state": ProviderLinkState.VERIFIED,
        },
        {"provider": JELLYFIN},
        {"link_state": ProviderLinkState.VERIFIED},
    ],
)
def test_illegal_account_states_are_unrepresentable(overrides):
    with pytest.raises(ValueError):
        _account(**overrides)


def test_the_owner_is_active_exactly_while_it_has_a_jellyfin_link():
    owner = _account(role=Role.OWNER, provider=JELLYFIN, link_state=ProviderLinkState.VERIFIED)
    assert owner.account_class is AccountClass.OWNER
    unlinked = _account(role=Role.OWNER, status=AccountStatus.DISABLED)
    assert unlinked.provider is None


def test_actor_owner_author_target_and_policy_owner_stay_distinct():
    ownership = ReviewOwnership(owner=UserId(3), original_author=UserId(4))
    transferred = ReviewOwnership(owner=UserId(5), original_author=ownership.original_author)
    assert transferred.policy_owner == UserId(5)
    assert transferred.original_author == UserId(4)
    personal = ExportProvenance(
        export_format=ExportFormat.EDL,
        target=PersonalTarget(user_id=UserId(2), export_id=LOCAL_ID),
        actor=UserId(2),
        source_review_id="review-1",
        review_revision=7,
        policy_owner=ownership.policy_owner,
        original_author=ownership.original_author,
        policy_revision=PolicyRevision(11),
    )
    assert {personal.actor, personal.policy_owner, personal.original_author} == {2, 3, 4}
    assert personal.target.export_id == LOCAL_ID
    default = ExportProvenance(**{**personal.__dict__, "target": ItemDefaultTarget()})
    assert not hasattr(default.target, "export_id")


def test_audit_events_refuse_secret_shaped_details_and_naive_times():
    when = datetime(2026, 1, 1, 12, tzinfo=UTC)
    event = AuditEvent(
        when,
        UserId(2),
        CredentialKind.SESSION,
        Capability.ACCOUNTS_SESSIONS_REVOKE_OTHER,
        AuditOutcome.SUCCEEDED,
        target_user=UserId(3),
        details={"sessions_revoked": 2},
    )
    assert event.details == {"sessions_revoked": 2}
    for key in ("new_password", "api_key", "Token", "community_private_key"):
        with pytest.raises(ValueError):
            AuditEvent(when, None, None, "sync", AuditOutcome.FAILED, details={key: "x"})
    with pytest.raises(ValueError):
        AuditEvent(datetime(2026, 1, 1), None, None, "sync", AuditOutcome.FAILED)


def test_audit_details_are_a_frozen_snapshot_that_stays_json_serializable():
    when = datetime(2026, 1, 1, 12, tzinfo=UTC)
    supplied = {"sessions_revoked": 2, "remember_me": True, "reason": "reset", "note": None}
    event = AuditEvent(
        when, UserId(2), CredentialKind.SESSION, "sync", AuditOutcome.SUCCEEDED, details=supplied
    )
    supplied["api_key"] = "sentinel"
    assert "api_key" not in event.details
    with pytest.raises(TypeError):
        event.details["api_key"] = "sentinel"  # type: ignore[index]
    assert "api_key" not in event.details
    assert json.loads(json.dumps(dict(event.details))) == {
        "sessions_revoked": 2,
        "remember_me": True,
        "reason": "reset",
        "note": None,
    }


@pytest.mark.parametrize("value", [1.5, ["a"], {"nested": 1}, b"raw"])
def test_audit_details_refuse_non_scalar_values(value):
    when = datetime(2026, 1, 1, 12, tzinfo=UTC)
    with pytest.raises(ValueError):
        AuditEvent(when, None, None, "sync", AuditOutcome.FAILED, details={"value": value})


def test_error_codes_have_statuses_and_reauthentication_never_signs_out():
    assert set(ACCESS_ERROR_STATUS) == set(AccessErrorCode)
    unauthenticated = {code for code, status in ACCESS_ERROR_STATUS.items() if status == 401}
    assert unauthenticated == {
        AccessErrorCode.AUTHENTICATION_REQUIRED,
        AccessErrorCode.INVALID_API_KEY,
    }


def test_error_envelope_matches_the_existing_policy_response():
    from scanner.security.auth_policy import policy_response

    existing = json.loads(policy_response(401, "authentication_required", "Required").body)
    shaped = AccessError(code=AccessErrorCode.AUTHENTICATION_REQUIRED, detail="Required")
    assert shaped.model_dump(mode="json") == existing


def test_capability_payload_rejects_unknown_capabilities_and_fields():
    payload = {
        "version": "editarr-capabilities-9",
        "permission_revision": 4,
        "principal": {
            "user_id": 3,
            "role": "user",
            "credential": "api_key",
            "status": "active",
            "link_state": "unlinked",
        },
        "capabilities": ["reviews.view.own", "scans.start"],
    }
    parsed = CapabilityPayload.model_validate(payload)
    assert parsed.capabilities == [Capability.REVIEWS_VIEW_OWN, Capability.SCANS_START]
    for broken in (
        {**payload, "capabilities": ["reviews.view.everything"]},
        {**payload, "extra": True},
        {**payload, "version": "editarr-capabilities-1"},
    ):
        with pytest.raises(ValidationError):
            CapabilityPayload.model_validate(broken)
