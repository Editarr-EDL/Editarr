"""Prepare immutable donation previews, then sign only explicitly approved bytes."""

import gzip
import hashlib
import io
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from scanner.formats.community.codec import (
    ArtifactError,
    ResourceBounds,
    parse_artifact,
    parse_manifest,
)
from scanner.modeling.media_identity import DurationEvidence
from scanner.pipeline.audio_tracks import AudioSelectionError, _file_state
from scanner.pipeline.frames import probe_video_duration
from scanner.security.community_artifact_signatures import SignedArtifact, sign_artifact

from .donation_projection import DonationOptions, project_source
from .donation_sources import CapturedSource, SourceRef, canonical, capture_source
from .fingerprints import FingerprintCache
from .pcm_fingerprints import fingerprint_pcm_audio
from .stream_fingerprints import (
    StreamUnsupported,
    analyzed_audio_source,
    fingerprint_video,
)

_files = FingerprintCache()
_UNSUPPORTED = {
    "unsupported_codec_configuration",
    "missing_stream_timing_index",
    "stream_too_short",
    "empty_sample",
    "h264_requires_avcc",
    "invalid_avcc",
    "invalid_nal_packet",
    "missing_stream",
    "audio_pcm_budget",
    "audio_pcm_unavailable",
}


@dataclass(frozen=True)
class PreparedDonation:
    source: CapturedSource
    artifact_bytes: bytes
    manifest_bytes: bytes
    preview_bytes: bytes
    consent_token: str
    bounds: ResourceBounds
    source_limit: int
    file_states: tuple[tuple[str, tuple], ...]


def _consent(artifact: bytes, manifest: bytes, preview: bytes, guard: str) -> str:
    return hashlib.sha256(
        canonical(
            [
                hashlib.sha256(artifact).hexdigest(),
                hashlib.sha256(manifest).hexdigest(),
                hashlib.sha256(preview).hexdigest(),
                guard,
            ]
        )
    ).hexdigest()


def _require_original_versions(bind, captured, sources):
    """Check pinned evidence before claiming physical fingerprints in a contribution."""
    from scanner.db import ScanRow
    from scanner.persistence.repository.media_identity_store import (
        get_version,
        review_version_id,
        version_locators,
    )
    from scanner.persistence.repository.review_version_evidence import version_file_matches
    from scanner.persistence.repository.version_reuse import bound_scan_file_matches

    with Session(bind) as db:
        if captured.reference.kind == "review":
            pin = review_version_id(db, captured.reference.identifier)
            if pin:
                review = captured.data()["root"]
                version = get_version(db, pin)
                if not any(
                    locator.media_item_id == review["media_item_id"]
                    and locator.media_source_id == review["media_source_id"]
                    and locator.resolved_path
                    and version_file_matches(version, locator.resolved_path)
                    for locator in version_locators(db, pin)
                ):
                    raise ArtifactError("The original review version is unavailable")
        for identifier, path in sources:
            scan = db.get(ScanRow, identifier)
            if scan is None or bound_scan_file_matches(scan, Path(path)) is False:
                raise ArtifactError("The original scan version is unavailable")


def prepare_donation(
    bind: Engine,
    source: SourceRef,
    options: DonationOptions,
    *,
    bounds: ResourceBounds,
    source_limit: int,
    should_cancel=None,
) -> PreparedDonation:
    """Build exact gzip/manifest/preview bytes from a stable read-only source generation."""
    captured = capture_source(bind, source, bounds=bounds, source_limit=source_limit)
    payload, exclusions, warnings, chosen = project_source(captured, options, bounds=bounds)
    scans = {item["id"]: item for item in captured.data()["scans"]}
    selected = scans.get(chosen) if chosen else None
    primary = selected or next(iter(scans.values()), None)
    _require_original_versions(
        bind,
        captured,
        {(scan["id"], scan["resolved_local_path"]) for scan in (primary, selected) if scan},
    )
    states = {}

    def fingerprint(channel, scan):
        path = Path(scan["resolved_local_path"])
        try:
            states[str(path)] = _file_state(path)
            if should_cancel and should_cancel():
                raise ArtifactError("Donation preparation cancelled")
            if channel == "audio":
                with Session(bind) as db:
                    analyzed_path, selection = analyzed_audio_source(db, scan["id"])
                if Path(analyzed_path) != path:
                    raise ArtifactError("Scanned media changed; prepare a new source snapshot")
                return asdict(
                    fingerprint_pcm_audio(
                        path,
                        selection,
                        provenance="analyzed",
                        description=payload["analyzed_audio"]["description"],
                        should_cancel=should_cancel,
                    )
                )
            result = fingerprint_video(path, should_cancel=should_cancel)
        except FileNotFoundError:
            warnings.append(f"{channel}_media_unavailable")
            return None
        except StreamUnsupported as exc:
            if str(exc) not in _UNSUPPORTED:
                raise ArtifactError("Stream evidence could not be captured safely") from exc
            warnings.append(f"{channel}_fingerprint_unsupported")
            return None
        except AudioSelectionError as exc:
            raise ArtifactError("Scanned media changed; prepare a new source snapshot") from exc
        return {
            "algorithm": result.algorithm,
            "digest": result.digest,
            "playback_start": result.playback_start,
            "duration": result.duration,
            "provenance": "unknown",
            "description": None,
        }

    if payload["analyzed_audio"]:
        payload["matching"]["audio_stream"] = fingerprint("audio", selected)
    verified_file = payload["matching"]["audio_stream"] is not None
    if primary is not None and primary["video_hash"] and primary["scan_video"]:
        from scanner.repository import compute_video_md5

        try:
            if compute_video_md5(primary["resolved_local_path"]) != primary["video_hash"]:
                raise ArtifactError("Scanned media changed; prepare a new source snapshot")
            payload["matching"]["video_stream"] = fingerprint("video", primary)
            verified_file = True
        except FileNotFoundError:
            warnings.append("video_media_unavailable")
    if verified_file:
        path = Path(primary["resolved_local_path"])
        result = _files.fingerprint(path)
        payload["matching"]["fingerprints"] = [
            {
                key: value
                for key, value in asdict(result).items()
                if key in {"algorithm", "digest", "size_bytes"}
            }
        ]
        try:
            duration = DurationEvidence(probe_video_duration(path, should_cancel), "file_probe")
        except (OSError, RuntimeError, ValueError):
            warnings.append("measured_duration_unavailable")
        else:
            payload["matching"]["media"]["release"]["duration"] = asdict(duration)
    for path, state in states.items():
        if _file_state(Path(path)) != state:
            raise ArtifactError("Media changed during donation preparation")
    if (
        capture_source(bind, source, bounds=bounds, source_limit=source_limit).guard
        != captured.guard
    ):
        raise ArtifactError("Source changed during preparation; preview again")
    if should_cancel and should_cancel():
        raise ArtifactError("Donation preparation cancelled")
    buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=buffer, filename="", mode="wb", compresslevel=9, mtime=0) as archive:
        archive.write(canonical(payload))
    artifact_bytes = buffer.getvalue()
    decoded = parse_artifact(artifact_bytes, bounds=bounds, compressed=True)
    manifest_bytes = canonical(
        {
            "schema_version": "community-manifest-1",
            "artifact_format": "gzip-json",
            "artifact_schema_version": decoded.artifact.schema_version,
            "artifact_digest": hashlib.sha256(artifact_bytes).hexdigest(),
            "submission_id": decoded.artifact.submission_id,
            "contributor_id": decoded.artifact.author.contributor_id,
            "key_id": decoded.artifact.author.key_id,
            "summary": decoded.summary,
        }
    )
    parse_manifest(manifest_bytes, artifact=decoded, bounds=bounds)
    preview_bytes = canonical(
        {
            "artifact": json.loads(gzip.decompress(artifact_bytes)),
            "manifest": json.loads(manifest_bytes),
            "artifact_digest": hashlib.sha256(artifact_bytes).hexdigest(),
            "manifest_digest": hashlib.sha256(manifest_bytes).hexdigest(),
            "exclusions": exclusions,
            "warnings": sorted(set(warnings)),
        }
    )
    if len(preview_bytes) > bounds.expanded_bytes:
        raise ArtifactError("Donation preview exceeds configured byte bound")
    return PreparedDonation(
        captured,
        artifact_bytes,
        manifest_bytes,
        preview_bytes,
        _consent(artifact_bytes, manifest_bytes, preview_bytes, captured.guard),
        bounds,
        source_limit,
        tuple(states.items()),
    )


def approve_donation(
    bind: Engine, prepared: PreparedDonation, *, consent_token: str, private_key: Ed25519PrivateKey
) -> SignedArtifact:
    """Require exact preview consent and unchanged persisted/file generations before signing."""
    expected = _consent(
        prepared.artifact_bytes,
        prepared.manifest_bytes,
        prepared.preview_bytes,
        prepared.source.guard,
    )
    if consent_token != prepared.consent_token or consent_token != expected:
        raise ArtifactError("Preview consent does not match prepared bytes")
    current = capture_source(
        bind, prepared.source.reference, bounds=prepared.bounds, source_limit=prepared.source_limit
    )
    if current.guard != prepared.source.guard:
        raise ArtifactError("Source changed after preview; prepare a new preview")
    for path, state in prepared.file_states:
        try:
            unchanged = _file_state(Path(path)) == state
        except OSError:
            unchanged = False
        if not unchanged:
            raise ArtifactError("Media changed after preview; prepare a new preview")
    decoded = parse_artifact(prepared.artifact_bytes, bounds=prepared.bounds, compressed=True)
    if decoded.artifact.sharing_terms is None:
        raise ArtifactError("Explicit sharing terms are required before signing")
    return sign_artifact(
        private_key=private_key,
        artifact_bytes=prepared.artifact_bytes,
        manifest_bytes=prepared.manifest_bytes,
        bounds=prepared.bounds,
    )
