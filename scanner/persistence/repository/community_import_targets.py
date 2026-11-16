"""Physical target guards for explicitly associated immutable community evidence."""

import json
from pathlib import Path

from sqlalchemy.orm import object_session

from scanner.db import CommunityImportRow, CommunityImportTargetRow, MediaItemRow


def verified_target(db, scan, media_item_id, media_source_id):
    imported = db.query(CommunityImportRow).filter_by(scan_id=scan.id).one_or_none()
    if imported is None or not all(
        (imported.artifact_bytes, imported.manifest_bytes, imported.signature_bytes)
    ):
        return None
    return db.get(CommunityImportTargetRow, (imported.id, media_item_id, media_source_id or ""))


def matches_target(association, target_path):
    from scanner import repository as root

    if Path(association.resolved_local_path).resolve() != target_path.resolve():
        return False
    if association.video_hash is None:
        return True
    try:
        return root.compute_video_md5(str(target_path)) == association.video_hash
    except OSError:
        return False


def _approved_channels(association):
    from scanner import repository as root

    if association is None:
        return None
    evidence = json.loads(association.target_evidence_json)
    if "selected_channels" not in evidence:
        return None
    channels = evidence["selected_channels"]
    if (
        not isinstance(channels, list)
        or not channels
        or any(
            not isinstance(channel, str) or channel not in {"audio", "video"}
            for channel in channels
        )
    ):
        raise root.ReviewMembershipError(
            "This community import is not approved for the selected channels"
        )
    return set(channels)


def validate_target_role(association, role):
    """Restrict review reuse to explicitly approved channels; legacy targets stay unchanged."""
    from scanner import repository as root

    channels = _approved_channels(association)
    requested = {"audio", "video"} if role == "both" else {role}
    if channels is not None and not requested <= channels:
        raise root.ReviewMembershipError(
            "This community import is not approved for the selected channels"
        )


def project_review_channel(association, channel, role):
    """Narrow a copied both-channel cue, never its immutable source finding."""
    if _approved_channels(association) is not None and channel == "both" and role != "both":
        return role
    return channel


def approved_target_channels(association, scan):
    channels = _approved_channels(association)
    if channels is None:
        channels = {channel for channel in ("audio", "video") if getattr(scan, f"scan_{channel}")}
    return sorted(channels)


def scan_target_approvals(scan):
    """Expose local target/channel approvals, without physical paths or target evidence."""
    db = object_session(scan)
    if db is None or scan.community_import is None or not scan.community_import.artifact_bytes:
        return []
    rows = (
        db.query(CommunityImportTargetRow, MediaItemRow)
        .join(MediaItemRow, MediaItemRow.id == CommunityImportTargetRow.media_item_id)
        .filter(CommunityImportTargetRow.import_id == scan.community_import.id)
        .all()
    )
    return [
        {
            "source": "jellyfin",
            "target_item_id": media.jellyfin_id,
            "media_source_id": association.media_source_id or None,
            "channels": approved_target_channels(association, scan),
        }
        for association, media in rows
    ]


def verified_both_channel(scan, finding):
    """Canonical both-channel remote cues retain one immutable local source row."""
    imported = scan.community_import
    return bool(
        finding is not None
        and finding.mcf_channel == "both"
        and imported is not None
        and imported.artifact_bytes
        and imported.manifest_bytes
        and imported.signature_bytes
    )


def review_duration(scan, media_item_id=None, media_source_id=None):
    """Use an independently measured approved target runtime, never alter source duration."""
    db = object_session(scan)
    if db is not None and media_item_id is not None:
        association = verified_target(db, scan, media_item_id, media_source_id)
        if association is not None:
            duration = json.loads(association.target_evidence_json)["media"]["release"]["duration"]
            if duration is not None and duration["source"] == "file_probe":
                return duration["seconds"]
    return scan.duration_seconds
