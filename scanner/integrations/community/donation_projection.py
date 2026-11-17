"""Allowlisted public claims from private persisted donation snapshots."""

import json
import re
import unicodedata
from dataclasses import asdict, dataclass, replace
from typing import Literal

from scanner.formats.community.artifact_v2 import PUBLIC_EXECUTION_SETTINGS
from scanner.formats.community.codec import _SETTINGS, ArtifactError, ResourceBounds
from scanner.modeling.community_matching import MatchEvidence
from scanner.modeling.media_identity import AudioEvidence, MediaIdentity
from scanner.modeling.model_audio_tracks import AudioSelection

from .donation_sources import CapturedSource, SourceRef


@dataclass(frozen=True)
class DonationOptions:
    submission_id: str
    name: str
    contributor_id: str
    key_id: str
    created_at: str
    media: MediaIdentity
    duration_seconds: float
    sharing_terms: str | None = None
    audio_source_scan_id: str | None = None
    notes: tuple[tuple[str, str], ...] = ()
    supersedes: str | None = None


_PRIVATE_TEXT = re.compile(
    r"https?://|file://|(?:^|\s)(?:/[^\s]+|[A-Za-z]:[\\/]|\\\\)|"
    r"(?:password|api[_-]?key|authorization|bearer|token|secret)\s*[:=]|\bsk-[A-Za-z0-9]",
    re.IGNORECASE,
)


def audio_source_ids(data: dict, reference: SourceRef) -> set[str]:
    """Return the exact source candidates used by the public audio projection."""
    generated = reference.kind == "scan" and data["root"]["origin"] == "generated"
    scans = {item["id"]: item for item in data["scans"]}
    identifiers = {
        item.get("source_scan_id", item.get("scan_id"))
        for item in data["findings"]
        if (
            item["mcf_channel"]
            or ("both" if not generated else "audio" if item["source"] == "audio" else "video")
        )
        in {"audio", "both"}
    }
    if reference.kind == "scan" and data["root"]["scan_audio"]:
        identifiers.add(data["root"]["id"])
    for membership in data["memberships"]:
        if membership["role"] in {"audio", "both"} and scans[membership["scan_id"]]["scan_audio"]:
            identifiers.add(membership["scan_id"])
    return identifiers


def project_source(captured: CapturedSource, options: DonationOptions, *, bounds: ResourceBounds):
    """Project only public fields; edits/exclusions never alter the captured source."""
    data = captured.data()
    warnings: list[str] = []

    def text(value, label, *, required=False):
        if value is None:
            return None
        if not isinstance(value, str):
            raise ArtifactError("Public text must be a string")
        value = unicodedata.normalize(
            "NFC",
            "".join(
                character
                for character in value
                if not unicodedata.category(character).startswith("C")
            ),
        ).strip()
        if len(value.encode("utf8")) > bounds.text:
            raise ArtifactError("Public text exceeds configured bound")
        if not value or _PRIVATE_TEXT.search(value):
            if required:
                raise ArtifactError("Public text contains excluded private content")
            warnings.append(f"redacted:{label}")
            return None
        return value

    def token(value, label):
        result = text(value, label, required=True)
        if result is None or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", result):
            raise ArtifactError("Public identity must be a bounded token")
        return result

    scans = {item["id"]: item for item in data["scans"]}
    source_ids = {identifier: f"source-{index + 1}" for index, identifier in enumerate(scans)}
    imports = {item["scan_id"]: item for item in data["imports"]}
    documents = {item["scan_id"]: item for item in data["documents"]}
    notes = dict(options.notes)
    derived = captured.reference.kind == "review"
    generated = not derived and data["root"]["origin"] == "generated"

    def channel_of(row):
        return row["mcf_channel"] or (
            "both" if not generated else "audio" if row["source"] == "audio" else "video"
        )

    if len(notes) != len(options.notes) or notes.keys() - {
        str(item["id"]) for item in data["findings"]
    }:
        raise ArtifactError("Notes must refer to unique captured findings")
    has_audio = any(channel_of(item) in {"audio", "both"} for item in data["findings"]) or (
        captured.reference.kind == "scan" and data["root"]["scan_audio"]
    )
    audio_scans = audio_source_ids(data, captured.reference)
    has_audio = has_audio or bool(audio_scans)

    def track_key(identifier):
        scan = scans[identifier]
        return scan["analyzed_audio_selection_json"] if scan["origin"] == "generated" else None

    def track_group(identifier):
        return track_key(identifier) or f"unknown-source:{identifier}"

    track_groups = {track_group(identifier) for identifier in audio_scans}
    chosen = options.audio_source_scan_id
    if chosen is not None and chosen not in audio_scans:
        raise ArtifactError("Chosen audio source is not part of this snapshot")
    if len(track_groups) > 1 and chosen is None:
        raise ArtifactError("Choose one audio source for a mixed-track contribution")
    if chosen is None and audio_scans:
        chosen = sorted(audio_scans)[0]
    chosen_key = track_key(chosen) if chosen else None
    selection = AudioSelection.model_validate_json(chosen_key) if chosen_key else None
    analyzed = None
    audio = None
    if selection:
        track = selection.track
        role: Literal["main", "commentary", "descriptive", "unknown"] = "unknown"
        if track.disposition & 8:
            role = "commentary"
        elif track.disposition & 256:
            role = "descriptive"
        language = text(track.language, "audio_language")
        if language is not None and not re.fullmatch(r"[A-Za-z0-9-]{1,64}", language):
            warnings.append("redacted:audio_language")
            language = None
        audio = AudioEvidence(language, role, text(track.codec, "audio_codec"), track.channels)
        analyzed = {
            "provenance": "analyzed",
            "language": audio.language,
            "description": text(track.title, "audio_description"),
            "role": role,
            "codec": audio.codec,
            "channels": audio.channels,
            "stream_start_seconds": track.start_seconds,
            "stream_duration_seconds": track.duration_seconds,
            "time_base": text(track.time_base, "audio_time_base"),
        }
    identity = options.media
    release = replace(identity.release, audio=audio)
    release = replace(
        release,
        **{
            field: text(getattr(release, field), field)
            for field in ("scene_name", "group", "edition", "quality")
        },
    )
    identity = replace(
        identity,
        identifiers=tuple(
            replace(
                item,
                provenance="user_confirmed"
                if item.provenance == "user_confirmed"
                else "source_metadata",
            )
            for item in identity.identifiers
        ),
        series_identifiers=tuple(
            replace(
                item,
                provenance="user_confirmed"
                if item.provenance == "user_confirmed"
                else "source_metadata",
            )
            for item in identity.series_identifiers
        ),
        release=release,
        input_requirement=None,
    )
    matching = asdict(MatchEvidence(identity))
    sources = []
    references = []
    for identifier, scan in scans.items():
        stages = []
        coverage = []
        if scan["origin"] == "generated":
            for index, row in enumerate(
                item for item in data["stages"] if item["scan_id"] == identifier
            ):
                stage = {
                    "execution_id": f"{source_ids[identifier]}-attempt-{index + 1}",
                    "capability": token(row["stage"], "stage"),
                    "state": row["status"],
                    "settings": {
                        key: value if not isinstance(value, str) else text(value, f"setting:{key}")
                        for key, value in json.loads(row["effective_settings_json"] or "{}").items()
                        if key in _SETTINGS | PUBLIC_EXECUTION_SETTINGS
                    },
                    "intervals": json.loads(row["intervals_json"] or "[]"),
                    "sampling_seconds": row["sampling_seconds"],
                }
                for field in (
                    "model_id",
                    "model_revision",
                    "model_digest",
                    "release_version",
                    "scanner_behavior_version",
                    "build_revision",
                    "quantization",
                ):
                    stage[field] = text(row[field], field)
                stages.append(stage)
                stage_name = row["stage"]
                channel = (
                    "audio"
                    if stage_name.startswith("audio") or stage_name == "qwen_language"
                    else "video"
                    if stage_name.startswith(("visual", "credits"))
                    or stage_name
                    in {"visual_analysis", "scene_detection", "qwen_summaries", "credits_detection"}
                    else "both"
                )
                coverage.append(
                    {
                        "channel": channel,
                        "capability": stage["capability"],
                        "state": stage["state"],
                        "intervals": stage["intervals"],
                    }
                )
        if not coverage:
            coverage = [
                {
                    "channel": "both",
                    "capability": "recorded_analysis"
                    if scan["origin"] == "generated"
                    else "authored_filters",
                    "state": "unknown",
                    "intervals": [],
                }
            ]
        provenance = {
            field: text(scan[field], field) if scan["origin"] == "generated" else None
            for field in ("release_version", "scanner_behavior_version", "build_revision")
        }
        provenance["stages"] = stages
        reference = None
        imported = imports.get(identifier)
        if imported:
            if imported.get("provenance_json"):
                coverage = json.loads(imported["provenance_json"])["coverage"]
            digest = imported["digest"].removeprefix("sha256:")
            if not re.fullmatch(r"[a-f0-9]{64}", digest):
                raise ArtifactError("Imported source digest is invalid")
            reference = {
                "submission_id": token(imported["revision_id"], "parent"),
                "digest": digest,
            }
            if reference not in references:
                references.append(reference)
        sources.append(
            {
                "source_id": source_ids[identifier],
                "origin": scan["origin"],
                "status": scan["status"],
                "created_at": scan["created_at"],
                "revision": scan["selection_revision"],
                "duration_seconds": scan["duration_seconds"],
                "provenance": provenance,
                "coverage": coverage,
                "reference": reference,
                "author_id": token(imported["author_id"], "source_author") if imported else None,
            }
        )
    findings, exclusions = [], []
    required = set(data.get("policy_required", ()))
    parent_events = {}
    parent_counts = dict.fromkeys(scans, 0)
    if (
        not derived
        and data["root"]["duration_seconds"] is not None
        and options.duration_seconds != data["root"]["duration_seconds"]
    ):
        raise ArtifactError("Donation duration disagrees with the source timeline")
    for index, row in enumerate(data["findings"]):
        identifier = row.get("source_scan_id", row.get("scan_id"))
        event_id = f"event-{index + 1}"
        parent_event = None
        if derived and row["source_finding_id"] is not None:
            imported_events = json.loads(imports.get(identifier, {}).get("event_ids_json") or "{}")
            parent_key = (identifier, row["source_finding_id"])
            if parent_key not in parent_events:
                parent_counts[identifier] += 1
                parent_events[parent_key] = (
                    f"{source_ids[identifier]}-event-{parent_counts[identifier]}"
                )
            parent_event = imported_events.get(
                str(row["source_finding_id"]), parent_events[parent_key]
            )
        if not generated and not row["selected"]:
            exclusions.append({"event_id": event_id, "reason": "unselected"})
            continue
        channel = channel_of(row)
        if channel in {"audio", "both"} and track_group(identifier) != track_group(chosen):
            if row["id"] in required:
                raise ArtifactError(
                    "An enforced review finding cannot be excluded by audio selection"
                )
            exclusions.append(
                {"event_id": event_id, "reason": "other_audio_track", "channel": "audio"}
            )
            if channel != "both":
                continue
            channel = "video"
        if row["end_seconds"] is None:
            raise ArtifactError("Donation findings require an explicit end time")
        manual = (
            {"added": False, "edited": False}
            if generated
            else {"added": row["manually_added"], "edited": row["manually_edited"]}
        )
        findings.append(
            {
                "event_id": event_id,
                "start_seconds": row["start_seconds"],
                "end_seconds": row["end_seconds"],
                "source": "manual"
                if derived and row["source_finding_id"] is None
                else "derived"
                if derived
                else row["source"]
                if generated
                else "manual",
                "channel": channel,
                "category": row["category"],
                "subcategory": row["subcategory"],
                "severity": row["severity"],
                "confidence": row["confidence"] if generated else None,
                "action": None if generated else row["edit_action"],
                "note": text(notes.get(str(row["id"]), row["mcf_comment"]), f"note:{event_id}"),
                "parent_event_id": parent_event,
                "source_snapshot_id": source_ids[identifier],
                "manual": manual,
            }
        )
    retiming = []
    if derived:
        if not data["synchronization"]:
            raise ArtifactError("Saved review synchronization is unavailable")
        sync = data["synchronization"][0]
        for identifier in scans:
            document = documents.get(identifier)
            source_bounds = (
                {
                    "start_seconds": document["material_start_seconds"],
                    "end_seconds": document["material_end_seconds"],
                }
                if document
                else None
            )
            retiming.append(
                {
                    "source_id": source_ids[identifier],
                    "source_bounds": source_bounds,
                    "target_bounds": {
                        "start_seconds": sync["start_seconds"],
                        "end_seconds": sync["end_seconds"],
                    },
                    "mapping": "affine" if source_bounds else "unknown",
                    "applied": True,
                }
            )
    provenance = (
        sources[0]["provenance"]
        if generated
        else {
            "release_version": None,
            "scanner_behavior_version": None,
            "build_revision": None,
            "stages": [],
        }
    )
    coverage = (
        sources[0]["coverage"]
        if generated
        else [
            {
                "channel": "both",
                "capability": "authored_filters",
                "state": "unknown",
                "intervals": [],
            }
        ]
    )
    association = (
        "identified"
        if analyzed
        else "unknown"
        if has_audio or any(item["channel"] == "both" for item in findings)
        else "none"
    )
    payload = {
        "schema_version": "community-artifact-2",
        "manual_filter_tracking": "manual-1",
        "name": text(options.name, "name", required=True),
        "source_revision": data["root"].get("revision", data["root"].get("selection_revision", 0)),
        "submission_id": token(options.submission_id, "submission"),
        "created_at": options.created_at,
        "author": {
            "contributor_id": token(options.contributor_id, "contributor"),
            "key_id": token(options.key_id, "key"),
        },
        "sharing_terms": text(options.sharing_terms, "terms", required=True),
        "kind": "derived" if derived else "scan" if generated else "manual",
        "source_references": references,
        "supersedes": token(options.supersedes, "supersedes") if options.supersedes else None,
        "matching": matching,
        "sources": sources,
        "retiming": retiming,
        "audio_association": association,
        "audio_source_id": source_ids[chosen] if analyzed else None,
        "analyzed_audio": analyzed,
        "media": {
            "duration_seconds": options.duration_seconds,
            "timeline": "derived_target" if derived else "source_file",
            "time_unit": "seconds",
            "work_ids": [
                {"provider": item.provider, "entity_type": item.entity_type, "value": item.value}
                for item in (*identity.identifiers, *identity.series_identifiers)
            ],
            "release": release.scene_name,
        },
        "provenance": provenance,
        "coverage": coverage,
        "findings": findings,
        "derivation": {"parents": references, "transform": "already_applied_target_timeline"}
        if derived
        else None,
    }
    return payload, exclusions, warnings, chosen
