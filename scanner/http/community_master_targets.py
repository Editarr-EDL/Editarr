"""Resolve a Find or download target to authorized local media (local API §1–§3).

The backend resolves the Jellyfin item version, its local file and its local
`media_versions.id`; the browser never supplies keys, hashes or paths. The file
generation guard binds a Find result to the file it measured, so a download can
revalidate it without measuring the keys again.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from scanner.integrations.community.find_targets import FindTarget
from scanner.integrations.community.transport import CommunityFailure
from scanner.persistence.repository.review_access import ReviewAccess, not_found
from scanner.persistence.repository.scan_jobs import ScanMedia
from scanner.runtime.scan_version import ScanVersionChanged, file_generation
from scanner.security.access.capability_ids import Capability as C

from .community_master_access import media_allowed, require_capability


@dataclass(frozen=True)
class MasterTarget:
    review_target: object
    guard: str

    @property
    def media(self) -> ScanMedia:
        return self.review_target.media


def target_guard(target: FindTarget, media: ScanMedia) -> str:
    """Bind the target to its resolved file and current generation."""
    try:
        generation = file_generation(media.resolved_path)
    except ScanVersionChanged as error:
        raise CommunityFailure("target_changed") from error
    document = [target.model_dump(), media.resolved_path, list(generation)]
    return hashlib.sha256(json.dumps(document, separators=(",", ":")).encode()).hexdigest()


def resolve_target(db, access, target: FindTarget, capability: C) -> MasterTarget:
    """The target's authorized media: `403` without `capability`, `404` without access."""
    # Review target capture reaches `scanner.api`, which imports these routes.
    from .review_targets import capture_target

    require_capability(access, capability)
    caller = ReviewAccess(db, access)
    resolved = capture_target(caller, target)
    if not media_allowed(access, caller.media_decision(resolved.media), capability):
        raise not_found()
    guard = target_guard(target, resolved.media)
    db.rollback()
    return MasterTarget(resolved, guard)


def local_version(db, target: MasterTarget, sampled) -> str:
    """Observe the target's local `media_versions.id` from its sampled fingerprint."""
    from .stable_media_capture import cached_target_digest, observe_sampled_target_version

    db.rollback()
    try:
        version_id = observe_sampled_target_version(
            db,
            target.review_target,
            fingerprint=sampled,
            known_content_sha256=cached_target_digest(db, target.review_target),
        )
        db.commit()
    except ScanVersionChanged as error:
        db.rollback()
        raise CommunityFailure("target_changed") from error
    return version_id
