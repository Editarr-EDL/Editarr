"""Authorize derived evidence through its immutable original and target version."""

import json
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

from sqlalchemy import func
from sqlalchemy.orm import object_session

from scanner.db import ScanFindingRow, ScanRow
from scanner.modeling.model_audio_tracks import AudioSelection
from scanner.modeling.model_version_reuse import Channel, VersionReuseCue, VersionReuseRequest
from scanner.modeling.version_alignment import Alignment
from scanner.runtime.media_fingerprints import content_digest

_COPIED_FIELDS = (
    "category",
    "subcategory",
    "severity",
    "mcf_comment",
    "confidence",
    "label",
    "details",
    "selected",
    "edit_action",
    "group_key",
    "manually_added",
    "manually_edited",
)
_MAX_CUES = 10_000


def _canonical(value) -> str:
    return json.dumps(value, allow_nan=False, separators=(",", ":"), sort_keys=True)


def reuse_state(scan) -> dict:
    """Read optional provenance on imported evidence without affecting ordinary scans."""
    if scan.origin != "imported" or not scan.checkpoint_state:
        return {}
    try:
        value = json.loads(scan.checkpoint_state)
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def source_pending_reuse(scan) -> bool:
    """Whether an imported scan must be aligned before it joins a target review."""
    return reuse_state(scan).get("identity_reuse_pending") is True


def approved_reuse(scan) -> dict | None:
    value = reuse_state(scan).get("identity_reuse")
    return value if isinstance(value, dict) else None


def bound_scan_file_matches(scan, path: Path) -> bool | None:
    """Recheck a strong scan pin before access to physical evidence."""
    from .media_identity_store import get_version, scan_version_id

    approval = approved_reuse(scan)
    db = object_session(scan)
    version_id = scan_version_id(db, scan.id) if db is not None else None
    version = get_version(db, version_id) if version_id else None
    digest = approval.get("target_sha256") if approval is not None else None
    if version is not None and version.content_sha256:
        if digest and digest != version.content_sha256:
            return False
        digest = version.content_sha256
    if version_id is not None and not digest:
        return False
    if not digest and approval is None:
        return None
    try:
        return content_digest(path) == digest
    except (OSError, ValueError):
        return False


def source_findings(db, scan_id: str) -> list[ScanFindingRow]:
    """Read immutable finding data for a bounded verification operation."""
    rows = (
        db.query(ScanFindingRow)
        .filter(ScanFindingRow.scan_id == scan_id)
        .order_by(ScanFindingRow.start_seconds, ScanFindingRow.id)
        .limit(_MAX_CUES + 1)
        .all()
    )
    if len(rows) > _MAX_CUES:
        raise ValueError("This scan contains too many cues to verify together")
    return rows


def cue_channels(source: str, mcf_channel: str | None) -> tuple[Channel, ...]:
    """Resolve the audio and video components of an original finding."""
    if mcf_channel == "both":
        return ("audio", "video")
    channel = mcf_channel or ("audio" if source == "audio" else "video")
    return ("audio",) if channel == "audio" else ("video",)


def project_cues(
    findings: list[ScanFindingRow],
    channels: list[Channel],
    alignment: Alignment,
    target_duration: float,
) -> list[VersionReuseCue]:
    """Keep unmappable cues visible in preview without making them attachable."""
    using = set(channels)
    cues = []
    for item in findings:
        pending = [
            channel for channel in cue_channels(item.source, item.mcf_channel) if channel in using
        ]
        channel = "both" if len(pending) == 2 else next(iter(pending), None)
        if not channel:
            continue
        direct = (
            alignment.audio_matches.get((item.start_seconds, item.end_seconds))
            if channel == "audio" and item.end_seconds is not None
            else None
        )
        projected = direct or alignment.project(item.start_seconds, item.end_seconds)
        start, end = projected or (None, None)
        if start is not None and (
            not 0 <= start < target_duration
            or (end is not None and (end <= start or end > target_duration))
        ):
            start, end = None, None
        cues.append(
            VersionReuseCue(
                source_finding_id=item.id,
                channel=channel,
                source_start=item.start_seconds,
                source_end=item.end_seconds,
                target_start=start,
                target_end=end,
            )
        )
    return cues


def source_signature(scan: ScanRow, findings: list[ScanFindingRow]) -> str:
    """Bind confirmation to source evidence, including flags copied into the new scan."""
    return sha256(
        _canonical(
            {
                "scan": scan.id,
                "selection": scan.analyzed_audio_selection_json,
                "revision": scan.selection_revision,
                "channels": [scan.scan_audio, scan.scan_video],
                "runtime": scan.duration_seconds,
                "state": scan.checkpoint_state,
                "cues": [
                    [
                        item.id,
                        item.start_seconds,
                        item.end_seconds,
                        item.source,
                        item.mcf_channel,
                        *(getattr(item, field) for field in _COPIED_FIELDS),
                    ]
                    for item in findings
                ],
            }
        ).encode()
    ).hexdigest()


def approval_token(
    request: VersionReuseRequest,
    *,
    version_id: str,
    source_version_id: str,
    source_stamp: str,
    actor_stamp: tuple[int, int],
    target_stamp: tuple,
    duration: float,
    anchors,
    cues,
    alignment_audio_scan_id: str | None,
) -> str:
    """Produce a stale-preview check. The server independently revalidates every approval."""
    return sha256(
        _canonical(
            {
                "options": request.model_dump(exclude={"confirmed", "token"}),
                "version": version_id,
                "origin": source_version_id,
                "source": source_stamp,
                "actor": actor_stamp,
                "target": target_stamp,
                "duration": duration,
                "anchors": [anchor.model_dump() for anchor in anchors],
                "cues": [cue.model_dump() for cue in cues],
                "alignment_audio_scan_id": alignment_audio_scan_id,
            }
        ).encode()
    ).hexdigest()


def stage_verified_reuse(
    db,
    *,
    media,
    target,
    version_id: str,
    source_version_id: str,
    source: ScanRow,
    findings: list[ScanFindingRow],
    preview,
    request,
    audio_selection,
    duration: float,
    target_sha256: str,
) -> tuple[ScanRow, bool]:
    """Stage target-bound copies and original finding references under the caller's write lock."""
    from .import_transaction import stage_imported_scan
    from .media_identity_store import bind_scan_version

    mapped = [cue for cue in preview.cues if cue.target_start is not None]
    state = _canonical(
        {
            "identity_reuse": {
                "source_scan_id": source.id,
                "source_version_id": source_version_id,
                "target_version_id": version_id,
                "target_sha256": target_sha256,
                "token": preview.token,
                "method": request.method,
                "exact": preview.exact,
                "channels": preview.verified_channels,
                "alignment_audio_scan_id": preview.alignment_audio_scan_id,
                "anchors": [anchor.model_dump() for anchor in preview.anchors],
                "cues": [cue.model_dump() for cue in mapped],
            }
        }
    )
    existing = (
        db.query(ScanRow)
        .filter_by(
            media_item_id=media.id,
            media_source_id=target.source.media_source_id,
            checkpoint_state=state,
        )
        .one_or_none()
    )
    if existing is not None:
        return existing, True

    scan_number = db.query(func.max(ScanRow.scan_number)).filter_by(media_item_id=media.id).scalar()
    scan = stage_imported_scan(
        db,
        media_item=media,
        scan_number=(scan_number or 0) + 1,
        resolved_local_path=target.media.resolved_path,
        media_source_id=target.source.media_source_id,
        source_name="Verified reuse",
        review_name=source.name,
        duration_seconds=duration,
        has_audio="audio" in preview.verified_channels,
        has_video="video" in preview.verified_channels,
        progress_message="Verified previous scan cues",
        imported_at=datetime.now(UTC),
    )
    scan.checkpoint_state = state
    if audio_selection is not None and scan.scan_audio:
        selection = AudioSelection.from_selection(audio_selection).model_dump_json()
        scan.requested_audio_selection_json = selection
        scan.analyzed_audio_selection_json = selection
    originals = {item.id: item for item in findings}
    for cue in mapped:
        item = originals[cue.source_finding_id]
        content = {field: getattr(item, field) for field in _COPIED_FIELDS}
        if item.mcf_channel == "both" and cue.channel == "audio":
            content["edit_action"] = "mute"
        db.add(
            ScanFindingRow(
                scan_id=scan.id,
                source="audio" if cue.channel == "audio" else "visual",
                mcf_channel=cue.channel,
                start_seconds=cue.target_start,
                end_seconds=cue.target_end,
                **content,
            )
        )
    bind_scan_version(db, scan.id, version_id)
    db.flush()
    return scan, False


def stage_reuse_source(db, scan, version_id: str) -> None:
    """Record an uploaded source for target verification without creating a review."""
    from .media_identity_store import bind_scan_version

    scan.checkpoint_state = '{"identity_reuse_pending":true}'
    bind_scan_version(db, scan.id, version_id)
