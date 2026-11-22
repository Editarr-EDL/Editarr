"""Physical target guards for explicitly associated immutable community evidence."""

import json
from pathlib import Path

from sqlalchemy import func
from sqlalchemy.orm import object_session

from scanner.db import CommunityImportRow, CommunityImportTargetRow, MediaItemRow


def stored_target(db, scan, media_item_id, media_source_id):
    """Find an intact signed association, including a pending verification."""
    imported = db.query(CommunityImportRow).filter_by(scan_id=scan.id).one_or_none()
    if imported is None or not all(
        (imported.artifact_bytes, imported.manifest_bytes, imported.signature_bytes)
    ):
        return None
    return db.get(CommunityImportTargetRow, (imported.id, media_item_id, media_source_id or ""))


def pending_target_channels(association) -> set[str]:
    if association is None:
        return set()
    evidence = json.loads(association.target_evidence_json)
    return set(evidence.get("pending_channels", ())) & {"audio", "video"}


def verified_target(db, scan, media_item_id, media_source_id):
    """Find a target association with at least one approved channel."""
    association = stored_target(db, scan, media_item_id, media_source_id)
    if pending_target_channels(association) and not _approved_channels(association):
        return None
    return association


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
        and not pending_target_channels(association)
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


def _approvals_by_import(db, scans_by_import):
    """Load target approvals for verified imports keyed by import id, in one query."""
    approvals = {import_id: [] for import_id in scans_by_import}
    if not approvals:
        return approvals
    rows = (
        db.query(CommunityImportTargetRow, MediaItemRow)
        .join(MediaItemRow, MediaItemRow.id == CommunityImportTargetRow.media_item_id)
        .filter(CommunityImportTargetRow.import_id.in_(list(approvals)))
        .all()
    )
    for association, media in rows:
        approved = approved_target_channels(association, scans_by_import[association.import_id])
        if not approved:
            continue
        approvals[association.import_id].append(
            {
                "source": "jellyfin",
                "target_item_id": media.jellyfin_id,
                "media_source_id": association.media_source_id or None,
                "channels": approved,
            }
        )
    return approvals


def scan_target_approvals(scan):
    """Expose local target/channel approvals, without physical paths or target evidence."""
    db = object_session(scan)
    if db is None or scan.community_import is None or not scan.community_import.artifact_bytes:
        return []
    return _approvals_by_import(db, {scan.community_import.id: scan})[scan.community_import.id]


def scan_pending_targets(scan):
    """Expose candidate channels for the scan's visible source item only."""
    imported = scan.community_import
    db = object_session(scan)
    if db is None or imported is None:
        return []
    rows = db.query(CommunityImportTargetRow).filter_by(
        import_id=imported.id,
        media_item_id=scan.media_item_id,
    )
    return [
        {
            "source": "jellyfin",
            "target_item_id": scan.media_item.jellyfin_id,
            "media_source_id": item.media_source_id or None,
            "channels": sorted(pending),
        }
        for item in rows
        if (pending := pending_target_channels(item))
    ]


def scan_target_approvals_by_scan(db, scans):
    """Batch `scan_target_approvals` for many scans without reading artifact blobs."""
    if not scans:
        return {}
    by_id = {scan.id: scan for scan in scans}
    imports = (
        db.query(CommunityImportRow.id, CommunityImportRow.scan_id)
        .filter(
            CommunityImportRow.scan_id.in_(list(by_id)),
            func.length(CommunityImportRow.artifact_bytes) > 0,
        )
        .all()
    )
    approvals = _approvals_by_import(
        db, {import_id: by_id[scan_id] for import_id, scan_id in imports}
    )
    by_scan = {scan_id: approvals[import_id] for import_id, scan_id in imports}
    return {scan_id: by_scan.get(scan_id, []) for scan_id in by_id}


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
