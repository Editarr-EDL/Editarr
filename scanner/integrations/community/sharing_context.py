"""Authorized source descriptions do not require an enrolled contributor."""

import json
from dataclasses import dataclass

from pydantic import TypeAdapter
from sqlalchemy.orm import Session

from scanner.db import CommunityImportRow
from scanner.formats.community.codec import ArtifactError, ResourceBounds, parse_artifact
from scanner.modeling.media_identity import MediaIdentity

from .donation_sources import SourceRef, capture_source
from .transport import CommunityFailure

TERMS_VERSION = "community-reuse-1"
TERMS_TEXT = (
    "Contributions, including public notes, may be copied, shared and modified. "
    "Original attribution and change history remain attached. Submit only material "
    "you have permission to share. Authors cannot withdraw contributions; operators "
    "may moderate availability. Media files and third-party rights are not included."
)


def audio_source_ids(data: dict, reference: SourceRef) -> set[str]:
    """Return the exact source candidates used by the public audio projection."""
    generated = reference.kind == "scan" and data["root"]["origin"] == "generated"
    scans = {item["id"]: item for item in data["scans"]}
    identifiers = {
        item.get("source_scan_id", item.get("scan_id"))
        for item in data["findings"]
        if not item.get("community_source_id")
        and (
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


@dataclass(frozen=True)
class SharingSource:
    db: Session
    bounds: ResourceBounds
    source_limit: int

    def capture(self, source: SourceRef):
        try:
            return capture_source(
                self.db.get_bind(), source, bounds=self.bounds, source_limit=self.source_limit
            )
        except ArtifactError as error:
            raise CommunityFailure("source_unavailable") from error

    def terms(self, captured):
        terms = []
        try:
            for imported in captured.data()["imports"]:
                row = self.db.get(CommunityImportRow, imported["id"])
                if row is None or not row.artifact_bytes:
                    raise CommunityFailure("source_permission_unknown")
                original = parse_artifact(row.artifact_bytes, bounds=self.bounds, compressed=True)
                if original.artifact.sharing_terms != TERMS_VERSION:
                    raise CommunityFailure("source_permission_incompatible")
                terms.append(
                    {
                        "authorId": row.author_id,
                        "termsVersion": original.artifact.sharing_terms,
                    }
                )
        except ArtifactError as error:
            raise CommunityFailure("source_permission_unknown") from error
        finally:
            self.db.rollback()
        return terms

    def describe(self, source: SourceRef) -> dict:
        """Describe a source only after its caller has checked current source authority."""
        captured = self.capture(source)
        data = captured.data()
        identity = data["media"][0].get("confirmed_identity_json") if data["media"] else None
        public_identity = (
            TypeAdapter(MediaIdentity).dump_python(
                TypeAdapter(MediaIdentity).validate_json(identity), mode="json"
            )
            if identity
            else None
        )
        tracks = []
        audio_sources = audio_source_ids(data, source)
        for scan in data["scans"]:
            if scan["id"] in audio_sources and scan["scan_audio"]:
                selection = (
                    json.loads(scan["analyzed_audio_selection_json"] or "null")
                    if scan["origin"] == "generated"
                    else None
                )
                tracks.append(
                    {"scanId": scan["id"], "track": selection["track"] if selection else None}
                )
        duration = (
            data["synchronization"][0]["end_seconds"]
            if data["synchronization"]
            else data["root"].get("duration_seconds")
        )
        return {
            "name": data["root"].get("name") or "",
            "mediaIdentity": public_identity,
            "durationSeconds": duration,
            "audioSources": tracks,
            "sourceTerms": self.terms(captured),
            "terms": {"version": TERMS_VERSION, "text": TERMS_TEXT},
        }
