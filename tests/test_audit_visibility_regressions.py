"""Action authority is live and applies before audit counts, pages, and details."""

import pytest

from scanner.security.access.authorization import require_principal
from scanner.security.access.capability_ids import Capability as C
from scanner.security.access.identity import CredentialKind
from scanner.security.access.overrides import OverrideEffect
from scanner.security.access.records import AuditEvent, AuditOutcome
from scanner.security.audit.actions import AuditAction
from scanner.security.audit.store import stage_event
from scanner.security.audit.visibility import action_visible
from tests.multi_user_access_support import ADMIN, OWNER, USER
from tests.test_audit_history_http import NOW, get
from tests.test_audit_history_http import history as history


def record(history, action, details, target=ADMIN, resource=None, media_key=None):
    with history[1].factory() as db:
        identifier = stage_event(
            db,
            AuditEvent(
                NOW,
                ADMIN,
                CredentialKind.SESSION,
                action,
                AuditOutcome.SUCCEEDED,
                target_user=target,
                media_key=media_key,
                resource=resource,
                details=details,
            ),
        )
        db.commit()
    return identifier


@pytest.mark.parametrize("kind", ["own_review", "other_review"])
def test_review_publication_audit_uses_viewers_live_target_capability(history, kind):
    history[1].set_override(ADMIN, C.COMMUNITY_PUBLISH_OTHER_REVIEW, OverrideEffect.DENY)
    with history[1].factory() as db:
        access = require_principal(db, ADMIN, CredentialKind.SESSION)
        assert action_visible(AuditAction.COMMUNITY_PUBLISH, ADMIN, {"kind": kind}, access)
        assert not action_visible(AuditAction.COMMUNITY_PUBLISH, USER, {"kind": kind}, access)
    history[1].set_override(ADMIN, C.COMMUNITY_PUBLISH_OTHER_REVIEW, OverrideEffect.ALLOW)
    with history[1].factory() as db:
        access = require_principal(db, ADMIN, CredentialKind.SESSION)
        assert action_visible(AuditAction.COMMUNITY_PUBLISH, USER, {"kind": kind}, access)


def test_stored_password_changes_read_with_own_profile_editing(history):
    action = AuditAction.ACCOUNT_CHANGE
    stored = [
        record(history, action, {"changed": changed})
        for changed in ("password", "password_and_username")
    ]

    def statuses(user):
        return [get(history, user, f"/api/audit/{event}").status_code for event in stored]

    assert statuses(ADMIN) == [200, 200]
    history[1].set_override(ADMIN, C.PROFILE_EDIT_OWN, OverrideEffect.DENY)
    assert statuses(ADMIN) == [404, 404]
    assert get(history, ADMIN, "/api/audit", action=action.value).json()["total"] == 0
    assert statuses(OWNER) == [200, 200]


@pytest.mark.parametrize(
    ("action", "details", "capability"),
    [
        (AuditAction.PATH_MAPPING_CHANGE, {"change": "created"}, C.PATH_MAPPINGS_CREATE),
        (AuditAction.PATH_MAPPING_CHANGE, {"change": "updated"}, C.PATH_MAPPINGS_UPDATE),
        (AuditAction.PATH_MAPPING_CHANGE, {"change": "deleted"}, C.PATH_MAPPINGS_DELETE),
        (AuditAction.PATH_MAPPING_CHANGE, {"change": "reordered"}, C.PATH_MAPPINGS_REORDER),
        (AuditAction.BACKUP_DELETE, {}, C.BACKUPS_DELETE),
    ],
)
def test_delegated_owner_default_actions_are_visible(history, action, details, capability):
    target = USER if action is AuditAction.FILTER_POLICY_CHANGE else None
    identifier = record(history, action, details, target, "operation-1" if target is None else None)
    history[1].set_override(ADMIN, capability, OverrideEffect.ALLOW)
    assert get(history, ADMIN, f"/api/audit/{identifier}").status_code == 200
    page = get(history, ADMIN, "/api/audit", action=action.value).json()
    assert (page["total"], page["pages"]) == (1, 1)
    history[1].set_override(ADMIN, capability, OverrideEffect.DENY)
    assert get(history, ADMIN, f"/api/audit/{identifier}").status_code == 404
    assert get(history, ADMIN, "/api/audit", action=action.value).json()["total"] == 0
    assert get(history, OWNER, f"/api/audit/{identifier}").status_code == 200


def test_instance_default_policy_changes_require_nondelegable_enforcement(history):
    identifier = record(
        history, AuditAction.FILTER_POLICY_CHANGE, {"cause": "instance_defaults"}, USER
    )
    history[1].set_override(ADMIN, C.FILTERS_INSTANCE_DEFAULTS_EDIT, OverrideEffect.ALLOW)
    assert get(history, ADMIN, f"/api/audit/{identifier}").status_code == 404
    assert get(history, OWNER, f"/api/audit/{identifier}").status_code == 200


@pytest.mark.parametrize(
    ("target_kind", "source_owner", "revoked"),
    [
        ("personal", USER, C.REVIEWS_EXPORT_OTHER),
        ("item_default", USER, C.REVIEWS_EXPORT_OTHER),
        ("personal", USER, C.REVIEWS_VIEW_OTHER),
        ("item_default", USER, C.REVIEWS_VIEW_OTHER),
        ("personal", ADMIN, C.EXPORTS_PERSONAL_PUBLISH_OWN),
        ("personal", ADMIN, C.REVIEWS_VIEW_OWN),
        ("item_default", ADMIN, C.REVIEWS_VIEW_OWN),
    ],
)
@pytest.mark.parametrize("action", [AuditAction.EXPORT_PUBLISH, AuditAction.EXPORT_REGENERATE])
def test_export_audit_requires_live_source_authority(
    history, tmp_path, target_kind, source_owner, revoked, action
):
    from scanner.db import MediaItemRow

    media = tmp_path / "synthetic.mkv"
    media.write_bytes(b"synthetic")
    with history[1].factory() as db:
        db.add(
            MediaItemRow(
                id="export-proof",
                server_id="http://jellyfin.test",
                jellyfin_id="c3000000000000000000000000000001",
                library_id="a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1",
                item_type="Movie",
                name="Synthetic",
                path=str(media),
            )
        )
        db.commit()
    identifier = record(
        history,
        action,
        {"format": "edl", "target_kind": target_kind, "source_owner_id": source_owner},
        ADMIN if target_kind == "personal" else None,
        "export:1",
        "export-proof",
    )
    assert get(history, ADMIN, f"/api/audit/{identifier}").status_code == 200
    history[1].set_override(ADMIN, revoked, OverrideEffect.DENY)
    assert get(history, ADMIN, f"/api/audit/{identifier}").status_code == 404
    assert get(history, ADMIN, "/api/audit", action=action.value).json()["total"] == 0
    assert get(history, OWNER, f"/api/audit/{identifier}").status_code == 200


@pytest.mark.parametrize("change", ["created", "updated", "reordered"])
def test_path_mutations_require_current_path_visibility_too(history, change):
    capability = {
        "created": C.PATH_MAPPINGS_CREATE,
        "updated": C.PATH_MAPPINGS_UPDATE,
        "reordered": C.PATH_MAPPINGS_REORDER,
    }[change]
    identifier = record(history, AuditAction.PATH_MAPPING_CHANGE, {"change": change}, None)
    history[1].set_override(ADMIN, capability, OverrideEffect.ALLOW)
    assert get(history, ADMIN, f"/api/audit/{identifier}").status_code == 200
    history[1].set_override(ADMIN, C.MEDIA_PATHS_VIEW, OverrideEffect.DENY)
    assert get(history, ADMIN, f"/api/audit/{identifier}").status_code == 404
