"""Prepare exact `community-artifact-3` submission previews, then sign only approved bytes.

This keeps the sharing consent rules: the preview shows the exact artifact and
manifest, its consent token binds their digests to the captured source and master
link generations, and approval re-checks both, plus the media files, before signing.
"""

import gzip
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from scanner.db import CommunityMasterFilterRow, ScanRow
from scanner.formats.community.artifact_v3 import (
    ArtifactV3Error,
    build_manifest_v3,
    encode_artifact_v3,
)
from scanner.formats.community.codec import ArtifactError, ResourceBounds
from scanner.modeling.community_filter_proposals import Estimates, LinkedFilter
from scanner.modeling.community_normalization import SEEDED_ALIASES, Alias
from scanner.modeling.community_work_identity import WorkIdentity
from scanner.persistence.repository.community_master_filters import effective_values
from scanner.persistence.repository.community_master_links import alignment_of, get_link
from scanner.pipeline.audio_tracks import _file_state
from scanner.security.community_artifact_signatures import SignedArtifact
from scanner.security.community_artifact_v3_signatures import sign_artifact_v3

from .donation_sources import CapturedSource, SourceRef, canonical, capture_source
from .match_key_capture import capture_file_keys
from .stream_fingerprints import StreamUnsupported, analyzed_audio_source
from .submission_projection import LinkProjection, SubmissionOptions, project_submission
from .transport import CommunityFailure


@dataclass(frozen=True)
class SubmissionRequest:
    """What the user chose for one preview (local API §7)."""

    submission_id: str
    contributor_id: str
    key_id: str
    created_at: int
    work: WorkIdentity
    version_choice: dict
    owner_id: int
    link_id: str | None = None
    audio_source_scan_id: str | None = None
    aliases: tuple[Alias, ...] = SEEDED_ALIASES


@dataclass(frozen=True)
class PreparedSubmission:
    source: CapturedSource
    request: SubmissionRequest
    artifact_bytes: bytes
    manifest_bytes: bytes
    preview_bytes: bytes
    consent_token: str
    link_guard: str
    estimates: Estimates
    version_choice: dict
    entry_count: int
    warnings: tuple[str, ...]
    bounds: ResourceBounds
    source_limit: int
    file_states: tuple[tuple[str, tuple], ...]

    def summary(self) -> dict:
        """The master-set preview fields beside the exact preview (local API §7)."""
        work = self.request.work
        return {
            "estimates": self.estimates.as_json(),
            "versionChoice": self.version_choice,
            "work": {
                "kind": work.kind,
                "tmdb": work.tmdb,
                "tvdb": work.tvdb,
                "title": work.title,
                "year": work.year,
            },
            "entryCount": self.entry_count,
            "warnings": list(self.warnings),
        }


def version_choice(choice: object, aliases: tuple[Alias, ...]) -> tuple[dict, dict]:
    """Return the artifact `version` object and its display form.

    `choice` is `{"choice": "alias", "alias_id": …}` or `{"choice": "other", "text": …}`.
    An alias outside `aliases` is `unknown_alias`.
    """
    if not isinstance(choice, dict):
        raise CommunityFailure("version_choice_required")
    if choice.get("choice") == "alias" and set(choice) == {"choice", "alias_id"}:
        alias = next((item for item in aliases if item.alias_id == choice["alias_id"]), None)
        if alias is None:
            raise CommunityFailure("unknown_alias")
        return (
            {"choice": "alias", "aliasId": alias.alias_id},
            {"choice": "alias", "aliasId": alias.alias_id, "label": alias.label},
        )
    text = choice.get("text")
    if (
        choice.get("choice") == "other"
        and set(choice) <= {"choice", "text"}
        and (text is None or (isinstance(text, str) and len(text) <= 64))
    ):
        text = text if text and text.strip() else None
        return (
            {"choice": "other", "text": text},
            {"choice": "other", "text": text, "label": "Other"},
        )
    raise CommunityFailure("version_choice_required")


def link_projection(db: Session, owner_id: int, link_id: str | None) -> LinkProjection | None:
    """The account's link rows on the local timeline; a link still awaiting alignment
    has no local timeline yet and is not projected."""
    if link_id is None:
        return None
    link = get_link(db, owner_id, link_id)
    if link is None or link.link_state == "replaced":
        raise CommunityFailure("link_unavailable")
    if link.alignment_state == "required":
        return None
    alignment = alignment_of(link)
    rows = db.scalars(
        select(CommunityMasterFilterRow)
        .where(CommunityMasterFilterRow.link_id == link.id, CommunityMasterFilterRow.present)
        .order_by(CommunityMasterFilterRow.filter_id)
    )
    filters = []
    for row in rows:
        values = effective_values(row, alignment)
        filters.append(
            LinkedFilter(
                row.filter_id,
                row.category,
                row.state,
                values["startMs"],
                values["endMs"],
                values["subcategory"],
                values["severity"],
                values["action"],
                bool(row.locally_verified),
            )
        )
    return LinkProjection(
        link.community_version_id, link.latest_revision, alignment, tuple(filters)
    )


def _link_guard(link: LinkProjection | None) -> str:
    return hashlib.sha256(canonical(repr(link))).hexdigest()


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


def _primary_scan(data: dict, chosen: str | None) -> dict | None:
    scans = {scan["id"]: scan for scan in data["scans"]}
    if chosen is not None:
        if chosen not in scans:
            raise CommunityFailure("preview_invalid")
        return scans[chosen]
    if data["root"].get("origin") is not None:
        return scans.get(data["root"]["id"])
    generated = [scan for scan in data["scans"] if scan["origin"] == "generated"]
    return (generated or data["scans"] or [None])[0]


def _media_keys(bind, scan: dict | None, warnings: list[str], states: dict, should_cancel):
    """Tier 1–3 keys of the scanned file, only while it is still the scanned version."""
    from scanner.persistence.repository.version_reuse import bound_scan_file_matches

    if scan is None:
        return ()
    path = Path(scan["resolved_local_path"])
    with Session(bind) as db:
        row = db.get(ScanRow, scan["id"])
        if row is None or bound_scan_file_matches(row, path) is False:
            raise ArtifactError("The original scan version is unavailable")
        try:
            selection = analyzed_audio_source(db, scan["id"])[1]
        except StreamUnsupported:
            selection = None
    try:
        states[str(path)] = _file_state(path)
        return capture_file_keys(
            path,
            audio_selection=selection,
            video=bool(scan["scan_video"]),
            should_cancel=should_cancel,
            warnings=warnings,
        )
    except FileNotFoundError:
        warnings.append("media_unavailable")
        return ()


def _duration_ms(data: dict) -> int:
    seconds = (
        data["synchronization"][0]["end_seconds"]
        if data["synchronization"]
        else data["root"].get("duration_seconds")
    )
    milliseconds = int(round((seconds or 0) * 1000))
    if not 1 <= milliseconds <= 86_400_000:
        raise CommunityFailure("source_duration_unknown")
    return milliseconds


def prepare_submission(
    bind: Engine,
    source: SourceRef,
    request: SubmissionRequest,
    *,
    bounds: ResourceBounds,
    source_limit: int,
    should_cancel=None,
) -> PreparedSubmission:
    """Freeze the source and link, measure the file keys, and build the exact preview."""
    artifact_version, display = version_choice(request.version_choice, request.aliases)
    captured = capture_source(bind, source, bounds=bounds, source_limit=source_limit)
    data = captured.data()
    with Session(bind) as db:
        link = link_projection(db, request.owner_id, request.link_id)
    warnings: list[str] = [] if link or request.link_id is None else ["link_alignment_required"]
    states: dict[str, tuple] = {}
    keys = _media_keys(
        bind, _primary_scan(data, request.audio_source_scan_id), warnings, states, should_cancel
    )
    options = SubmissionOptions(
        submission_id=request.submission_id,
        contributor_id=request.contributor_id,
        key_id=request.key_id,
        created_at=request.created_at,
        work=request.work,
        version_choice=artifact_version,
        duration_ms=_duration_ms(data),
        match_keys=keys,
    )
    try:
        projection = project_submission(data, source.kind, options, link)
        decoded = encode_artifact_v3(projection.artifact)
    except (ValueError, ArtifactV3Error) as error:
        raise CommunityFailure(getattr(error, "code", "preview_invalid")) from error
    manifest_bytes = build_manifest_v3(decoded)
    if capture_source(bind, source, bounds=bounds, source_limit=source_limit).guard != (
        captured.guard
    ):
        raise ArtifactError("Source changed during preparation; preview again")
    warnings = sorted({*warnings, *projection.warnings})
    preview_bytes = canonical(
        {
            "artifact": json.loads(gzip.decompress(decoded.gzip_bytes)),
            "manifest": json.loads(manifest_bytes),
            "artifact_digest": decoded.digest,
            "manifest_digest": hashlib.sha256(manifest_bytes).hexdigest(),
            "warnings": warnings,
        }
    )
    guard = _link_guard(link)
    return PreparedSubmission(
        source=captured,
        request=request,
        artifact_bytes=decoded.gzip_bytes,
        manifest_bytes=manifest_bytes,
        preview_bytes=preview_bytes,
        consent_token=_consent(
            decoded.gzip_bytes, manifest_bytes, preview_bytes, captured.guard + guard
        ),
        link_guard=guard,
        estimates=projection.estimates,
        version_choice=display,
        entry_count=len(projection.artifact.entries),
        warnings=tuple(warnings),
        bounds=bounds,
        source_limit=source_limit,
        file_states=tuple(states.items()),
    )


def approve_submission(
    bind: Engine,
    prepared: PreparedSubmission,
    *,
    consent_token: str,
    private_key: Ed25519PrivateKey,
) -> SignedArtifact:
    """Sign the exact previewed bytes only while the source, link and files are unchanged."""
    expected = _consent(
        prepared.artifact_bytes,
        prepared.manifest_bytes,
        prepared.preview_bytes,
        prepared.source.guard + prepared.link_guard,
    )
    if consent_token != prepared.consent_token or consent_token != expected:
        raise ArtifactError("Preview consent does not match prepared bytes")
    current = capture_source(
        bind, prepared.source.reference, bounds=prepared.bounds, source_limit=prepared.source_limit
    )
    if current.guard != prepared.source.guard:
        raise ArtifactError("Source changed after preview; prepare a new preview")
    with Session(bind) as db:
        link = link_projection(db, prepared.request.owner_id, prepared.request.link_id)
    if _link_guard(link) != prepared.link_guard:
        raise ArtifactError("Master set changed after preview; prepare a new preview")
    for path, state in prepared.file_states:
        try:
            unchanged = _file_state(Path(path)) == state
        except OSError:
            unchanged = False
        if not unchanged:
            raise ArtifactError("Media changed after preview; prepare a new preview")
    return sign_artifact_v3(
        private_key=private_key,
        artifact_bytes=prepared.artifact_bytes,
        manifest_bytes=prepared.manifest_bytes,
    )
