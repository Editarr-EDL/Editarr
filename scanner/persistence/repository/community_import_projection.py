"""Project verified remote evidence without interpreting local paths or applying retiming."""

import hashlib
import json
from dataclasses import dataclass

from scanner.db import ScanFindingRow, ScanRow, ScanStageCoverageRow
from scanner.formats.community.artifact_v2 import validate_v2
from scanner.formats.community.codec import ResourceBounds
from scanner.integrations.community.downloads import VerifiedDownload
from scanner.integrations.community.transport import CommunityFailure
from scanner.modeling.community_matching import Compatibility, MatchEvidence, classify
from scanner.modeling.media_identity import (
    MediaIdentity,
    ProviderIdentifier,
)
from scanner.modeling.model_community_imports import CommunityScanProvenance


@dataclass(frozen=True)
class LocalImportTarget:
    media_item_id: str
    media_source_id: str | None
    resolved_local_path: str
    evidence: MatchEvidence
    selected_channels: tuple[str, ...] | None = None
    lookup_target_key: str | None = None
    stage_for_verification: bool = False
    content_sha256: str | None = None


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def import_compatibility(
    downloaded: VerifiedDownload,
    target: LocalImportTarget,
    bounds: ResourceBounds,
) -> tuple[Compatibility, bool]:
    """Reuse the approved compatibility core; v1 cannot invent independent stream evidence."""
    artifact = downloaded.verified.artifact.artifact
    if artifact.schema_version == "community-artifact-2":
        candidate = validate_v2(artifact, source_limit=bounds.events)
    else:
        ids = tuple(
            ProviderIdentifier(item.provider, item.entity_type, item.value, "artifact")
            for item in artifact.media.work_ids
            if item.provider in {"tmdb", "tvdb"}
        )
        kind = next(
            (item.entity_type for item in ids if item.entity_type != "series"),
            "series" if ids else target.evidence.media.entity_type,
        )
        candidate = MatchEvidence(
            MediaIdentity(
                kind,
                tuple(item for item in ids if item.entity_type == kind),
                tuple(item for item in ids if item.entity_type == "series")
                if kind == "episode"
                else (),
            )
        )
    compatibility = classify(target.evidence, candidate)
    channels = set()
    for item in [*artifact.findings, *artifact.coverage]:
        channels.update({"audio", "video"} if item.channel == "both" else {item.channel})
    if not channels:
        channels = {"audio", "video"}
    if target.selected_channels is not None:
        selected = set(target.selected_channels)
        if (
            not selected
            or len(selected) != len(target.selected_channels)
            or not selected <= channels
        ):
            raise CommunityFailure("invalid_channels")
        channels = selected
    outcomes = [getattr(compatibility, channel) for channel in channels]
    if any(item.band == "incompatible" for item in outcomes):
        raise CommunityFailure("incompatible_artifact")
    return compatibility, any(not item.strong_eligible for item in outcomes)


def project_findings(downloaded: VerifiedDownload, scan_id: str) -> list[ScanFindingRow]:
    """Keep source cue times and confidence; canonical channels drive local review roles."""
    result = []
    for item in downloaded.verified.artifact.artifact.findings:
        source = "audio" if item.channel == "audio" else "visual"
        result.append(
            ScanFindingRow(
                scan_id=scan_id,
                category=item.category,
                subcategory=item.subcategory,
                severity=item.severity,
                mcf_channel=item.channel,
                mcf_comment=item.note,
                start_seconds=item.start_seconds,
                end_seconds=item.end_seconds,
                confidence=item.confidence,
                label=item.subcategory,
                details=item.note,
                source=source,
                selected=True,
                edit_action=item.action or ("mute" if source == "audio" else "skip"),
                manually_added=item.manual.added if item.manual else None,
                manually_edited=item.manual.edited if item.manual else None,
            )
        )
    return result


def project_execution(downloaded: VerifiedDownload, scan: ScanRow) -> None:
    """Project only recorded remote execution; imported scans do not execute local models."""
    artifact = downloaded.verified.artifact.artifact
    provenance = artifact.provenance
    scan.release_version = provenance.release_version
    scan.scanner_behavior_version = provenance.scanner_behavior_version
    scan.build_revision = provenance.build_revision
    for channel in ("audio", "video"):
        prefixes = ("audio",) if channel == "audio" else ("visual", "video")
        stages = [stage for stage in provenance.stages if stage.capability.startswith(prefixes)]
        models = {stage.model_id for stage in stages if stage.model_id is not None}
        revisions = {stage.model_revision for stage in stages if stage.model_revision is not None}
        setattr(scan, f"{channel}_model", next(iter(models)) if len(models) == 1 else None)
        setattr(
            scan,
            f"{channel}_model_revision",
            next(iter(revisions)) if len(revisions) == 1 else None,
        )
    scan.qwen_model = "unknown"
    scan.stage_coverage = [
        ScanStageCoverageRow(
            stage=stage.capability,
            execution_id=stage.execution_id,
            status=stage.state,
            source="imported",
            release_version=stage.release_version,
            scanner_behavior_version=stage.scanner_behavior_version,
            build_revision=stage.build_revision,
            model_id=stage.model_id,
            model_revision=stage.model_revision,
            model_digest=stage.model_digest,
            quantization=stage.quantization,
            effective_settings_json=canonical(stage.settings),
            intervals_json=canonical(
                [
                    interval.model_dump()
                    for interval in stage.intervals
                    if interval.end_seconds > interval.start_seconds
                ]
            ),
            sampling_seconds=stage.sampling_seconds,
        )
        for stage in provenance.stages
    ]


def public_provenance(downloaded: VerifiedDownload) -> str:
    """Allowlist public attribution without exposing byte bundles, receipts or local targets."""
    verified = downloaded.verified
    artifact = verified.artifact.artifact
    signed = verified.signed
    value = CommunityScanProvenance(
        service_url=downloaded.service,
        contribution_id=downloaded.contribution_id,
        revision_id=downloaded.revision_id,
        digest=verified.manifest.manifest.artifact_digest,
        manifest_digest=hashlib.sha256(signed.manifest_bytes).hexdigest(),
        signature_digest=hashlib.sha256(signed.envelope_bytes).hexdigest(),
        author_id=artifact.author.contributor_id,
        author_key_id=artifact.author.key_id,
        historical_key_status=verified.historical_key.status,
        artifact_schema=artifact.schema_version,
        artifact_type=artifact.kind,
        timeline=artifact.media.timeline,
        source_duration_seconds=artifact.media.duration_seconds,
        source_references=[item.model_dump() for item in artifact.source_references],
        retiming=[item.model_dump() for item in artifact.retiming or []],
        sources=[item.model_dump() for item in artifact.sources or []],
        provenance=artifact.provenance.model_dump(),
        coverage=[item.model_dump() for item in artifact.coverage],
        analyzed_audio=artifact.analyzed_audio.model_dump() if artifact.analyzed_audio else None,
    )
    return canonical(value.model_dump())
