"""Community preparation binds effective content to current-owner policy evidence."""

import gzip
import json

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from scanner.db import SavedReviewRow
from scanner.formats.community.codec import ArtifactError
from scanner.integrations.community.donation_sources import SourceRef, capture_source
from scanner.integrations.community.donations import approve_donation, prepare_donation
from scanner.persistence.repository.review_policy import prepare_review_policy
from tests.test_community_donations import BOUNDS, options
from tests.test_review_policy_view import base_rows, enforce
from tests.test_review_policy_view import policy_review as policy_review


def test_community_capture_uses_same_effective_filters_and_changes_guard(policy_review):
    accounts, review_id = policy_review
    source = SourceRef("review", review_id)
    original = capture_source(accounts.engine, source, bounds=BOUNDS, source_limit=4)
    before = base_rows(accounts, review_id)
    enforce(accounts, severities=["swearing"])
    captured = capture_source(accounts.engine, source, bounds=BOUNDS, source_limit=4)
    data = captured.data()
    assert captured.guard != original.guard
    assert data["base_findings"][0]["selected"] is False
    assert data["findings"][0]["selected"] is True
    assert data["findings"][0]["severity"] == "high"
    with accounts.factory() as db:
        prepared = prepare_review_policy(db, db.get(SavedReviewRow, review_id))
        assert data["policy"] == {
            "owner_id": prepared.owner_id,
            "revision": prepared.policy_revision,
        }
        assert data["findings"][0]["severity"] == prepared.filters[0].severity.value
    assert base_rows(accounts, review_id) == before
    enforce(accounts, rules=[])
    restored = capture_source(accounts.engine, source, bounds=BOUNDS, source_limit=4)
    assert restored.guard != captured.guard
    assert restored.data()["findings"][0]["selected"] is False


def test_actual_community_bytes_use_effective_severity_and_old_consent_is_refused(policy_review):
    accounts, review_id = policy_review
    enforce(accounts, severities=["swearing"])
    prepared = prepare_donation(
        accounts.engine,
        SourceRef("review", review_id),
        options(duration_seconds=100),
        bounds=BOUNDS,
        source_limit=4,
    )
    artifact = json.loads(gzip.decompress(prepared.artifact_bytes))
    assert len(artifact["findings"]) == 1
    assert artifact["findings"][0]["severity"] == "high"
    assert artifact["findings"][0]["start_seconds"] == 1
    assert b"policy_owner" not in prepared.artifact_bytes
    enforce(accounts, rules=[])
    with pytest.raises(ArtifactError, match="Source changed"):
        approve_donation(
            accounts.engine,
            prepared,
            consent_token=prepared.consent_token,
            private_key=Ed25519PrivateKey.generate(),
        )


def test_audio_choice_cannot_exclude_required_findings(policy_review):
    from scanner import repository
    from scanner.db import ScanFindingRow, ScanRow
    from scanner.models import ReviewCreate, ReviewScanMembership
    from scanner.persistence.repository.activity_ownership import ActivityOwner
    from tests.multi_user_access_support import USER

    accounts, _review_id = policy_review
    enforce(accounts)
    with accounts.factory() as db:
        db.info["review_actor"] = ActivityOwner(USER, 1)
        source = db.get(ScanRow, "scan")
        db.add(
            ScanRow(
                id="other",
                media_item_id="media",
                scan_number=2,
                source_path=source.source_path,
                resolved_local_path=source.resolved_local_path,
                status="complete",
                duration_seconds=100,
                scan_audio=True,
                scan_video=False,
            )
        )
        db.flush()
        db.add(
            ScanFindingRow(
                scan_id="other",
                source="audio",
                category="language",
                subcategory="swearing",
                severity="low",
                mcf_channel="audio",
                start_seconds=4,
                end_seconds=5,
                edit_action="mute",
                selected=False,
            )
        )
        db.commit()
        review = repository.create_saved_review(
            db,
            media_item_id="media",
            media_source_id=None,
            data=ReviewCreate(
                name="Two tracks",
                memberships=[
                    ReviewScanMembership(scan_id=identifier, role="audio")
                    for identifier in ("scan", "other")
                ],
            ),
        )
    with pytest.raises(ArtifactError, match="enforced review finding"):
        prepare_donation(
            accounts.engine,
            SourceRef("review", review.id),
            options(duration_seconds=100, audio_source_scan_id="scan"),
            bounds=BOUNDS,
            source_limit=4,
        )
