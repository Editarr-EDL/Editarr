"""Community source checks use live accounts and synthetic shared scan evidence."""

from contextlib import contextmanager

import pytest

from scanner.db import UserCapabilityOverrideRow
from scanner.integrations.community.activity_access import CHECK, PUBLISH_REVIEW, ActivityAccess
from scanner.integrations.community.donation_sources import SourceRef
from scanner.persistence.repository.review_access import ReviewAccess
from scanner.security.access.authorization import require_principal
from scanner.security.access.capability_ids import Capability as C
from scanner.security.access.decisions import AccessDenied
from scanner.security.access.identity import CredentialKind, UserId
from tests.multi_user_access_support import ADMIN, PEER_USER, USER, live_accounts
from tests.scan_access_http import ScanApp


@pytest.fixture
def activities(tmp_path):
    accounts = live_accounts(tmp_path)
    media = tmp_path / "media"
    media.mkdir()
    app = ScanApp(None, accounts, media)
    yield app
    accounts.close()


def access(app, db, user, source, action):
    principal = require_principal(db, UserId(user), CredentialKind.API_KEY)
    return ActivityAccess(ReviewAccess(db, principal), source, action)


def test_shared_evidence_does_not_grant_private_review_publication(activities):
    scan, _ = activities.seed_scan(USER, "shared", status="complete")
    review = activities.seed_review(USER, "private", scan)
    with activities.accounts.factory() as db:
        access(
            activities, db, PEER_USER, SourceRef("scan", scan), C.COMMUNITY_PUBLISH_SCAN_EVIDENCE
        ).check()
        with pytest.raises(AccessDenied):
            access(activities, db, PEER_USER, SourceRef("review", review), PUBLISH_REVIEW).check()
    with activities.accounts.factory() as db:
        access(activities, db, ADMIN, SourceRef("review", review), PUBLISH_REVIEW).check()


def test_permission_revoked_after_boundary_construction_prevents_write(activities):
    scan, _ = activities.seed_scan(USER, "revocation", status="complete")
    with activities.accounts.factory() as db:
        authority = access(
            activities, db, USER, SourceRef("scan", scan), C.COMMUNITY_PUBLISH_SCAN_EVIDENCE
        )
        with activities.accounts.factory() as racing:
            racing.add(
                UserCapabilityOverrideRow(
                    user_id=USER, capability=C.COMMUNITY_PUBLISH_SCAN_EVIDENCE.value, effect="deny"
                )
            )
            racing.commit()
        calls = []
        with pytest.raises(AccessDenied):
            authority.run(lambda: calls.append("wrote"))
        assert calls == []


def test_permission_change_between_evidence_and_lock_prevents_write(activities, monkeypatch):
    from scanner.persistence.repository import review_access

    scan, _ = activities.seed_scan(USER, "lock-race", status="complete")
    original = review_access.write_lock
    raced = []

    @contextmanager
    def racing_lock(db):
        if not raced:
            with activities.accounts.factory() as racing:
                racing.add(
                    UserCapabilityOverrideRow(
                        user_id=USER,
                        capability=C.COMMUNITY_PUBLISH_SCAN_EVIDENCE.value,
                        effect="deny",
                    )
                )
                racing.commit()
            raced.append(True)
        with original(db):
            yield

    monkeypatch.setattr(review_access, "write_lock", racing_lock)
    with activities.accounts.factory() as db:
        authority = access(
            activities, db, USER, SourceRef("scan", scan), C.COMMUNITY_PUBLISH_SCAN_EVIDENCE
        )
        writes = []
        with pytest.raises(AccessDenied):
            authority.run(lambda: writes.append("written"))
        assert raced == [True] and writes == []


@pytest.mark.parametrize("exists", [True, False])
def test_missing_activity_grant_is_refused_before_source_lookup(activities, exists):
    scan, _ = activities.seed_scan(USER, "admission", status="complete")
    with activities.accounts.factory() as db:
        for capability in (CHECK.own, CHECK.other):
            db.add(
                UserCapabilityOverrideRow(user_id=USER, capability=capability.value, effect="deny")
            )
        db.commit()
        authority = access(
            activities, db, USER, SourceRef("scan", scan if exists else "absent"), CHECK
        )
        with pytest.raises(AccessDenied) as refused:
            authority.check()
        assert refused.value.code.value == "capability_denied"
