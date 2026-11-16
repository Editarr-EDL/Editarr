"""Live-gated re-downloads with explicit restored, unavailable, and transient outcomes."""

import json
from dataclasses import dataclass

from scanner.db import SavedReviewRow
from scanner.modeling.model_community_downloads import DownloadChoice
from scanner.modeling.model_community_lookup import LookupRequest, LookupTarget
from scanner.models import SavedReview
from scanner.persistence.repository.community_import_projection import LocalImportTarget
from scanner.persistence.repository.repository_exports import _review_target
from scanner.persistence.repository.review_access import SAVE
from scanner.persistence.repository.review_community_repairs import (
    RepairDownload,
    restore_community_review,
)
from scanner.persistence.repository.review_repairs import repair_records
from scanner.security.access.capability_ids import Capability as C
from scanner.security.private_identity_files import IdentityUnavailable

from .downloads import fetch_verified_download
from .local_downloads import drained_thread
from .lookup_targets import capture_target
from .transport import CommunityFailure


@dataclass(frozen=True)
class Restored:
    review: SavedReview


@dataclass(frozen=True)
class Unavailable:
    reason: str


@dataclass(frozen=True)
class Transient:
    reason: str


RepairOutcome = Restored | Unavailable | Transient
_DEFINITIVE = frozenset(
    {
        "artifact_unavailable",
        "contribution_not_found",
        "contribution_unavailable",
        "revision_not_available",
        "historical_key_untrusted",
        "invalid_artifact",
        "invalid_artifact_signature",
        "digest_mismatch",
        "mapping_mismatch",
        "incompatible_artifact",
        "invalid_channels",
    }
)


def _snapshot(db, review_id):
    row = db.get(SavedReviewRow, review_id)
    records = tuple(
        (
            value.id,
            value.missing_scan_id,
            value.preserved_json,
            value.community_json,
            value.community_unavailable_reason,
        )
        for value in repair_records(db, review_id)
    )
    return row.media_item_id, _review_target(row), records


def _unavailable(db, review_id, snapshot, reason):
    records = repair_records(db, review_id)
    if _snapshot(db, review_id)[2] != snapshot:
        raise CommunityFailure("review_changed", retryable=True)
    for record in records:
        record.community_unavailable_reason = reason
    db.commit()


def _channels(preserved_json):
    preserved = json.loads(preserved_json)
    roles = {value["role"] for value in preserved.get("review_scan_memberships", [])}
    roles.update(
        value.get("mcf_channel") or ("audio" if value["source"] == "audio" else "video")
        for value in preserved.get("review_filters", [])
    )
    if "both" in roles:
        roles = (roles - {"both"}) | {"audio", "video"}
    return tuple(sorted(roles)) or None


async def repair_from_community(
    caller, review_id, prepared, service_factory, *, confirmed=False
) -> RepairOutcome:
    """Fetch outside the writer; recheck authority, identity and file before atomic restore."""
    from scanner.repository import get_saved_review

    options = {"repair": True, "extra_capabilities": (C.COMMUNITY_DOWNLOAD,)}
    media_id, target, snapshot = caller.run(review_id, SAVE, _snapshot, **options)
    if not snapshot:
        return Restored(caller.run(review_id, SAVE, get_saved_review, **options))
    if any(not value[3] for value in snapshot):
        return Unavailable("community_identity_unavailable")
    stored = next((value[4] for value in snapshot if value[4]), None)
    if stored:
        return Unavailable(stored)
    db = caller.db
    try:
        service = service_factory()
        identity = service.client._identity()
        if not identity.contributor_id:
            raise CommunityFailure("not_connected")
        if identity.pending_replacement is not None:
            raise CommunityFailure("rotation_pending")
        identities = [json.loads(value[3]) for value in snapshot]
        if any(
            value["service_url"] != service.client.transport.endpoint.url for value in identities
        ):
            raise CommunityFailure("wrong_service")
        captured = await drained_thread(
            capture_target,
            db,
            LookupRequest(
                target=LookupTarget(
                    source=target.source,
                    item_id=target.item_id,
                    media_source_id=target.media_source_id,
                )
            ),
        )
        if str(captured.path) != prepared.media.resolved_path:
            raise CommunityFailure("target_changed")
        downloads = []
        for record, residual in zip(snapshot, identities, strict=True):
            await service.match_choice(
                identity,
                captured,
                DownloadChoice(
                    contributionId=residual["contribution_id"],
                    revisionId=residual["revision_id"],
                    artifactDigest=residual["digest"].removeprefix("sha256:"),
                    channels=list(_channels(record[2]) or ("audio", "video")),
                ),
            )
            downloaded = await fetch_verified_download(
                service.client.transport,
                identity,
                contribution_id=residual["contribution_id"],
                revision_id=residual["revision_id"],
                bounds=service.bounds,
                response_bytes=service.response_bytes,
            )
            captured.check()
            downloads.append(
                RepairDownload(
                    record[0],
                    record[1],
                    record[2],
                    record[3],
                    downloaded,
                    LocalImportTarget(
                        media_id,
                        target.media_source_id,
                        str(captured.path),
                        captured.evidence,
                        _channels(record[2]),
                        captured.key,
                    ),
                )
            )

        def restore(database, identifier):
            latest = service.client._identity()
            if (latest.key_id, latest.contributor_id, latest.pending_replacement) != (
                identity.key_id,
                identity.contributor_id,
                None,
            ):
                raise CommunityFailure("identity_changed")
            captured.check()
            return restore_community_review(
                database,
                identifier,
                downloads,
                bounds=service.bounds,
                credential_ref=service.client.home.credential_ref,
                confirm_uncertain=confirmed,
            )

        review = await drained_thread(
            caller.run, review_id, SAVE, restore, prepared=prepared, **options
        )
        return Restored(review)
    except IdentityUnavailable:
        db.rollback()
        return Transient("identity_unavailable")
    except CommunityFailure as error:
        db.rollback()
        if error.code in _DEFINITIVE and not error.retryable:
            caller.run(review_id, SAVE, _unavailable, snapshot, error.code, **options)
            return Unavailable(error.code)
        return Transient(error.code)
