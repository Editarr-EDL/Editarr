"""Explicit channel approval checks using the existing frozen import fixtures."""

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.orm import Session

from scanner import repository
from scanner.db import CommunityImportRow, CommunityImportTargetRow
from scanner.integrations.community.transport import CommunityFailure
from scanner.models import ReviewCreate, ReviewFilterInput, ReviewSave, ReviewScanMembership
from scanner.persistence.repository import community_import_projection as projection
from scanner.persistence.repository.community_import_targets import (
    project_review_channel,
    validate_target_role,
)
from scanner.persistence.repository.community_imports import import_verified_download
from tests.community_identity_support import only_identity_ref
from tests.test_community_verified_import import (
    BOUNDS,
    download_input,
    local_target,
    verified_input,
)
from tests.test_community_verified_import import import_database as import_database


def test_selected_channel_scope_never_approves_the_opposite_review_role():
    association = SimpleNamespace(target_evidence_json=json.dumps({"selected_channels": ["video"]}))
    validate_target_role(association, "video")
    assert project_review_channel(association, "both", "video") == "video"
    for role in ("audio", "both"):
        with pytest.raises(repository.ReviewMembershipError, match="not approved"):
            validate_target_role(association, role)
    legacy = SimpleNamespace(target_evidence_json="{}")
    validate_target_role(legacy, "both")
    assert project_review_channel(legacy, "both", "video") == "both"


@pytest.mark.parametrize("channels", [None, [], {}, ["video", {}], ["unknown"]])
def test_malformed_scopes_cannot_fall_back_to_legacy_approval(channels):
    association = SimpleNamespace(target_evidence_json=json.dumps({"selected_channels": channels}))
    with pytest.raises(repository.ReviewMembershipError, match="not approved"):
        validate_target_role(association, "video")


def test_video_only_import_preserves_source_and_rejects_unapproved_review_roles(
    import_database, tmp_path
):
    downloaded = verified_input("retimed")
    target = replace(local_target(downloaded, tmp_path), selected_channels=("video",))
    Path(target.resolved_local_path).write_bytes(b"synthetic media only")
    with Session(import_database) as db:
        result = import_verified_download(
            db,
            downloaded,
            target,
            bounds=BOUNDS,
            confirm_uncertain=True,
            credential_ref=only_identity_ref(db),
        )
        association = db.query(CommunityImportTargetRow).one()
        assert json.loads(association.target_evidence_json)["selected_channels"] == ["video"]
        imported = db.query(CommunityImportRow).one()
        assert imported.artifact_bytes == download_input("retimed")[2][0]
        for role in ("audio", "both"):
            with pytest.raises(repository.ReviewMembershipError, match="not approved"):
                repository.create_saved_review(
                    db,
                    media_item_id=target.media_item_id,
                    media_source_id=None,
                    data=ReviewCreate(
                        name="Rejected",
                        memberships=[ReviewScanMembership(scan_id=result.scan.id, role=role)],
                    ),
                )
        review = repository.create_saved_review(
            db,
            media_item_id=target.media_item_id,
            media_source_id=None,
            data=ReviewCreate(
                name="Video only",
                memberships=[ReviewScanMembership(scan_id=result.scan.id, role="video")],
            ),
        )
        assert review.memberships[0].role == "video"
        assert review.filters and all(value.mcf_channel == "video" for value in review.filters)
        with pytest.raises(repository.ReviewMembershipError, match="not approved"):
            repository.save_saved_review(
                db,
                review.id,
                ReviewSave(
                    revision=review.revision,
                    name=review.name,
                    memberships=review.memberships,
                    filters=[
                        ReviewFilterInput(**(value.model_dump() | {"mcf_channel": "audio"}))
                        for value in review.filters
                    ],
                    mcf_document=review.mcf_document,
                    synchronization=review.synchronization,
                ),
            )
        reused = import_verified_download(
            db,
            downloaded,
            target,
            bounds=BOUNDS,
            confirm_uncertain=True,
            credential_ref=only_identity_ref(db),
        )
        assert reused.reused and reused.scan.id == result.scan.id
        assert json.loads(db.query(CommunityImportTargetRow).one().target_evidence_json)[
            "selected_channels"
        ] == ["video"]


def test_import_compatibility_checks_only_selected_channels_and_rejects_invalid_scope(
    tmp_path, monkeypatch
):
    downloaded = verified_input("retimed")
    target = local_target(downloaded, tmp_path)
    compatibility, _ = projection.import_compatibility(downloaded, target, BOUNDS)
    incompatible_audio = replace(
        compatibility,
        audio=replace(compatibility.audio, band="incompatible", strong_eligible=False),
    )
    monkeypatch.setattr(projection, "classify", lambda *_args: incompatible_audio)
    projection.import_compatibility(
        downloaded, replace(target, selected_channels=("video",)), BOUNDS
    )
    for channels in (None, ("audio",), ("audio", "video")):
        with pytest.raises(CommunityFailure, match="incompatible_artifact"):
            projection.import_compatibility(
                downloaded, replace(target, selected_channels=channels), BOUNDS
            )
    for channels in ((), ("video", "video"), ("unknown",)):
        with pytest.raises(CommunityFailure, match="invalid_channels"):
            projection.import_compatibility(
                downloaded, replace(target, selected_channels=channels), BOUNDS
            )
