"""Resolve stable title IDs and keep version identity separate from catalog locators."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from scanner.db import (
    MediaItemRow,
    MediaTitleAliasRow,
    MediaTitleRow,
    MediaTitleSourceRow,
    MediaVersionLocatorRow,
    MediaVersionRow,
    ReviewMediaVersionRow,
    SavedReviewRow,
    ScanMediaVersionRow,
    ScanRow,
)
from scanner.modeling.media_identity import MediaIdentity, ProviderIdentifier
from scanner.persistence.write_lock import write_lock

if TYPE_CHECKING:
    from scanner.runtime.media_fingerprints import SampledMediaFingerprint

Alias = tuple[str, str, str]
_SHA256 = re.compile(r"[0-9a-f]{64}")
_MD5 = re.compile(r"[0-9a-f]{32}")


def _alias(identifier: ProviderIdentifier, entity_type: str) -> Alias | None:
    if identifier.entity_type != entity_type:
        return None
    value = identifier.value
    if value.isdecimal():
        value = value.lstrip("0")
    if not value:
        return None
    return identifier.provider, entity_type, value


def _aliases(identity: MediaIdentity | None, kind: str) -> frozenset[Alias]:
    if identity is None or identity.entity_type != kind:
        return frozenset()
    aliases = {key for item in identity.identifiers if (key := _alias(item, kind))}
    if kind == "episode":
        numbers = identity.episode_span or identity.episode_numbers
        if numbers and all(number.scheme == "aired" for number in numbers):
            span = ",".join(
                f"{season}.{number}"
                for season, number in sorted({(item.season, item.number) for item in numbers})
            )
            aliases.update(
                (provider, "aired_episode", f"{value}:{span}")
                for identifier in identity.series_identifiers
                if (key := _alias(identifier, "series"))
                for provider, _, value in [key]
            )
    return frozenset(aliases)


def _alias_claimants(db: Session, aliases: frozenset[Alias]) -> set[str]:
    return {
        claim.title_id for key in aliases if (claim := db.get(MediaTitleAliasRow, key)) is not None
    }


def _alias_keys_for_title(db: Session, title_id: str) -> set[Alias]:
    return {
        (claim.provider, claim.entity_type, claim.value)
        for claim in db.scalars(
            select(MediaTitleAliasRow).where(MediaTitleAliasRow.title_id == title_id)
        )
    }


def _alias_conflict(known: set[Alias], proposed: frozenset[Alias]) -> bool:
    """Catch provider conflicts even when the differing ID has no owner yet."""
    aliases = known | proposed
    return len(aliases) != len({(provider, kind) for provider, kind, _ in aliases})


def observe_title(db: Session, media: MediaItemRow, identity: MediaIdentity | None = None) -> str:
    """Resolve one catalog source, keeping established bindings and refusing conflicting aliases."""
    kind = media.item_type.casefold()
    aliases = _aliases(identity, kind)
    existing = db.get(MediaTitleSourceRow, media.id)
    if existing is not None and not aliases:
        return existing.title_id

    with write_lock(db):
        source = db.get(MediaTitleSourceRow, media.id)
        claimants = _alias_claimants(db, aliases)
        title_id = source.title_id if source is not None else None
        contradictory = len(claimants) > 1 or _alias_conflict(set(), aliases)
        if title_id is None and len(claimants) == 1 and not contradictory:
            candidate = next(iter(claimants))
            if not _alias_conflict(_alias_keys_for_title(db, candidate), aliases):
                title_id = candidate
            else:
                contradictory = True
        if title_id is None:
            title_id = str(uuid4())
            db.add(MediaTitleRow(id=title_id, entity_type=kind))
            db.flush()
        if (
            not contradictory
            and not claimants - {title_id}
            and not _alias_conflict(_alias_keys_for_title(db, title_id), aliases)
        ):
            db.add_all(
                MediaTitleAliasRow(
                    provider=provider, entity_type=entity, value=value, title_id=title_id
                )
                for provider, entity, value in aliases
                if db.get(MediaTitleAliasRow, (provider, entity, value)) is None
            )
        if source is None:
            db.add(MediaTitleSourceRow(media_item_id=media.id, title_id=title_id))
        db.flush()
        return title_id


def find_title_id(db: Session, *, server_id: str, jellyfin_id: str) -> str | None:
    """Resolve an observed Jellyfin item ID on its original server, including retired items."""
    return db.scalar(
        select(MediaTitleSourceRow.title_id)
        .join(MediaItemRow, MediaItemRow.id == MediaTitleSourceRow.media_item_id)
        .where(MediaItemRow.server_id == server_id, MediaItemRow.jellyfin_id == jellyfin_id)
    )


def title_has_aliases(db: Session, title_id: str) -> bool:
    return bool(_alias_keys_for_title(db, title_id))


def identity_conflicts_title(db: Session, title_id: str, identity: MediaIdentity | None) -> bool:
    """Detect provider evidence that would replace an established work with another title."""
    if identity is None:
        return False
    title = db.get(MediaTitleRow, title_id)
    if title is None or title.entity_type != identity.entity_type:
        return True
    aliases = _aliases(identity, title.entity_type)
    return bool(
        _alias_claimants(db, aliases) - {title_id}
        or _alias_conflict(_alias_keys_for_title(db, title_id), aliases)
    )


def identity_matches_title(db: Session, title_id: str, identity: MediaIdentity) -> bool:
    """Match typed live provider evidence to a known title without changing claims."""
    title = db.get(MediaTitleRow, title_id)
    if title is None or title.entity_type != identity.entity_type:
        return False
    aliases = _aliases(identity, title.entity_type)
    return _alias_claimants(db, aliases) == {title_id} and not _alias_conflict(
        _alias_keys_for_title(db, title_id), aliases
    )


def title_media_items(db: Session, title_id: str) -> list[MediaItemRow]:
    """Return source records so the caller can apply current catalog access checks."""
    return list(
        db.scalars(
            select(MediaItemRow)
            .join(MediaTitleSourceRow, MediaTitleSourceRow.media_item_id == MediaItemRow.id)
            .where(MediaTitleSourceRow.title_id == title_id)
            .order_by(MediaItemRow.updated_at.desc(), MediaItemRow.id)
        )
    )


def _visible_versions():
    return (
        select(MediaVersionRow)
        .join(MediaVersionLocatorRow, MediaVersionLocatorRow.version_id == MediaVersionRow.id)
        .join(MediaItemRow, MediaItemRow.id == MediaVersionLocatorRow.media_item_id)
        .distinct()
    )


def get_version(db: Session, version_id: str) -> MediaVersionRow | None:
    """Load an observed version with a non-archived catalog source."""
    return db.scalar(_visible_versions().where(MediaVersionRow.id == version_id))


def title_versions(db: Session, title_id: str) -> list[MediaVersionRow]:
    return list(
        db.scalars(
            _visible_versions()
            .where(MediaVersionRow.title_id == title_id)
            .order_by(MediaVersionRow.id)
        )
    )


def version_locators(db: Session, version_id: str) -> list[MediaVersionLocatorRow]:
    """Return paths for internal exact-target checks."""
    return list(
        db.scalars(
            select(MediaVersionLocatorRow)
            .join(MediaItemRow, MediaItemRow.id == MediaVersionLocatorRow.media_item_id)
            .where(MediaVersionLocatorRow.version_id == version_id)
            .order_by(MediaVersionLocatorRow.media_item_id, MediaVersionLocatorRow.media_source_id)
        )
    )


def observe_version(
    db: Session,
    *,
    media: MediaItemRow,
    media_source_id: str | None,
    source_path: str,
    resolved_path: str | None = None,
    content_sha256: str | None = None,
    video_hash: str | None = None,
    scan_id: str | None = None,
) -> str | None:
    """Record a proven exact version. Never resolve a current file using weak evidence."""
    if content_sha256 is None:
        return None
    digest = content_sha256.lower()
    if not _SHA256.fullmatch(digest):
        raise ValueError("Invalid full-file SHA-256")
    with write_lock(db):
        title_id = observe_title(db, media)
        version = db.scalar(
            select(MediaVersionRow).where(
                MediaVersionRow.title_id == title_id, MediaVersionRow.content_sha256 == digest
            )
        )
        if version is None:
            version = MediaVersionRow(
                id=str(uuid4()), title_id=title_id, content_sha256=digest, video_hash=video_hash
            )
            db.add(version)
            db.flush()
        locator_id = (version.id, media.id, media_source_id or "", source_path, resolved_path or "")
        if db.get(MediaVersionLocatorRow, locator_id) is None:
            db.add(
                MediaVersionLocatorRow(
                    version_id=locator_id[0],
                    media_item_id=locator_id[1],
                    media_source_id=locator_id[2],
                    source_path=locator_id[3],
                    resolved_path=locator_id[4],
                )
            )
            db.flush()
        if scan_id is not None:
            bind_scan_version(db, scan_id, version.id)
        return version.id


def _generation_key(generation: tuple[int, ...]) -> str:
    return json.dumps(generation, separators=(",", ":"))


def find_observed_version(
    db: Session,
    *,
    title_id: str,
    path: str | Path,
    generation: tuple[int, ...],
    known_content_sha256: str | None = None,
) -> str | None:
    """Use an existing observation for this generation without reading media bytes."""
    if known_content_sha256 and _SHA256.fullmatch(known_content_sha256):
        known = db.scalar(
            select(MediaVersionRow.id).where(
                MediaVersionRow.title_id == title_id,
                MediaVersionRow.content_sha256 == known_content_sha256,
            )
        )
        if known is not None:
            return known
    versions = list(db.scalars(
        select(MediaVersionRow.id)
        .join(MediaVersionLocatorRow)
        .where(
            MediaVersionRow.title_id == title_id,
            MediaVersionLocatorRow.resolved_path == str(path),
            MediaVersionLocatorRow.sample_generation == _generation_key(generation),
        )
        .distinct()
        .limit(2)
    ))
    return versions[0] if len(versions) == 1 else None


def find_sampled_version(
    db: Session,
    *,
    title_id: str,
    fingerprint: SampledMediaFingerprint,
    known_content_sha256: str | None = None,
) -> str | None:
    """Match a current witness or a unique same-title sample and size, even after a move."""
    observed = find_observed_version(
        db, title_id=title_id, path=fingerprint.path, generation=fingerprint.generation,
        known_content_sha256=known_content_sha256,
    )
    if observed is not None:
        version = db.get(MediaVersionRow, observed)
        if version is not None and (
            version.sampled_md5 is None
            or (version.sampled_md5, version.sample_size_bytes)
            == (fingerprint.digest, fingerprint.size_bytes)
        ):
            return observed
        return None

    versions = list(db.scalars(
        select(MediaVersionRow.id)
        .where(
            MediaVersionRow.title_id == title_id,
            MediaVersionRow.sampled_md5 == fingerprint.digest,
            MediaVersionRow.sample_size_bytes == fingerprint.size_bytes,
        )
        .limit(2)
    ))
    return versions[0] if len(versions) == 1 else None


def matches_sampled_version(
    db: Session,
    version: MediaVersionRow | None,
    fingerprint: SampledMediaFingerprint,
    *,
    known_content_sha256: str | None = None,
) -> bool:
    """Check a version without promoting a legacy hash or choosing between collisions."""
    return version is not None and find_sampled_version(
        db, title_id=version.title_id, fingerprint=fingerprint,
        known_content_sha256=known_content_sha256,
    ) == version.id


def observe_sampled_version(
    db: Session,
    *,
    media: MediaItemRow,
    media_source_id: str | None,
    source_path: str,
    fingerprint: SampledMediaFingerprint,
    known_content_sha256: str | None = None,
    scan_id: str | None = None,
) -> str:
    """Add a sampled locator, preserving all existing full and weak historical identities."""
    if not _MD5.fullmatch(fingerprint.digest) or fingerprint.size_bytes < 0:
        raise ValueError("Invalid sampled media fingerprint")
    with write_lock(db):
        title_id = observe_title(db, media)
        version_id = find_sampled_version(
            db, title_id=title_id, fingerprint=fingerprint,
            known_content_sha256=known_content_sha256,
        )
        version = db.get(MediaVersionRow, version_id) if version_id is not None else None
        if version is None:
            version = MediaVersionRow(id=str(uuid4()), title_id=title_id)
            db.add(version)
        version.sampled_md5 = fingerprint.digest
        version.sample_size_bytes = fingerprint.size_bytes
        db.flush()

        locators = (version.id, media.id, media_source_id or "", source_path, str(fingerprint.path))
        locator = db.get(MediaVersionLocatorRow, locators)
        if locator is None:
            locator = MediaVersionLocatorRow(
                version_id=locators[0], media_item_id=locators[1], media_source_id=locators[2],
                source_path=locators[3], resolved_path=locators[4],
            )
            db.add(locator)
        locator.sample_generation = _generation_key(fingerprint.generation)
        db.flush()
        if scan_id is not None:
            bind_scan_version(db, scan_id, version.id)
        return version.id


def scan_version_id(db: Session, scan_id: str) -> str | None:
    return db.scalar(
        select(ScanMediaVersionRow.version_id)
        .join(ScanRow, ScanRow.id == ScanMediaVersionRow.scan_id)
        .where(ScanMediaVersionRow.scan_id == scan_id)
    )


def review_version_id(db: Session, review_id: str) -> str | None:
    return db.scalar(
        select(ReviewMediaVersionRow.version_id)
        .join(SavedReviewRow, SavedReviewRow.id == ReviewMediaVersionRow.review_id)
        .where(ReviewMediaVersionRow.review_id == review_id)
    )


def _bind_version(db, artifact_type, binding_type, key, artifact_id: str, version_id: str) -> str:
    with write_lock(db):
        existing = db.get(binding_type, artifact_id)
        if existing is not None:
            if existing.version_id != version_id:
                raise ValueError("Artifact is already bound to another media version")
            return existing.version_id
        artifact = db.scalar(select(artifact_type).where(artifact_type.id == artifact_id))
        if artifact is None or not any(
            locator.media_item_id == artifact.media_item_id
            and locator.media_source_id == (artifact.media_source_id or "")
            for locator in version_locators(db, version_id)
        ):
            raise ValueError("Artifact and version must have the same media source")
        db.add(binding_type(**{key: artifact_id, "version_id": version_id}))
        db.flush()
        return version_id


def bind_scan_version(db: Session, scan_id: str, version_id: str) -> str:
    """Pin a scan to the version that produced it."""
    return _bind_version(db, ScanRow, ScanMediaVersionRow, "scan_id", scan_id, version_id)


def bind_review_version(db: Session, review_id: str, version_id: str) -> str:
    """Pin a review to its original version, even when memberships change."""
    return _bind_version(
        db, SavedReviewRow, ReviewMediaVersionRow, "review_id", review_id, version_id
    )
