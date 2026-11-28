"""Editable, unsaved community filters and validation against their captured link."""

import hashlib
import json
from uuid import NAMESPACE_URL, uuid5

from scanner.db import CommunityMasterLinkRow
from scanner.modeling.community_review_filters import clip, filter_values
from scanner.modeling.model_review_filters import SavedReviewFilter
from scanner.security.access.api_shapes import AccessErrorCode
from scanner.security.access.authorization import require
from scanner.security.access.capability_ids import Capability as C
from scanner.security.access.decisions import AccessDenied

from .community_master_sources import version_duration_ms

CUE_FIELDS = frozenset(
    {
        "edit_action",
        "mcf_channel",
        "mcf_comment",
        "label",
        "details",
        "group_key",
        "start_seconds",
        "end_seconds",
    }
)
FILTER_FIELDS = ("selected", "category", "subcategory", "severity", "community_edited")
PROVENANCE = ("id", "community_source_id", "community_filter_id", "source", "confidence")


def _invalid(message):
    from scanner.repository import ReviewMembershipError

    return ReviewMembershipError(message)


def project_draft(db, link, entries=None) -> tuple[str, list[SavedReviewFilter]]:
    """Project the aligned link onto its local timeline without writing a review."""
    from .review_community_filters import link_entries

    entries = link_entries(db, link) if entries is None else entries
    duration = version_duration_ms(db, link.media_version_id)
    token = hashlib.sha256(
        json.dumps(
            [link.id, link.applied_revision, link.alignment_revision, duration, entries],
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    rows = []
    for entry in entries:
        bounds = clip(entry, duration)
        if bounds is None:
            continue
        fid = entry["filter_id"]
        values = filter_values({**entry, "initial": {"start_ms": bounds[0], "end_ms": bounds[1]}})
        rows.append(
            SavedReviewFilter(
                id=str(uuid5(NAMESPACE_URL, f"editarr-review-draft:{link.id}:{fid}")),
                community_source_id=link.id,
                community_filter_id=fid,
                community_edited=False,
                community={
                    "source_id": link.id,
                    "filter_id": fid,
                    "label": link.label or "Other",
                    "master_revision": link.applied_revision,
                    "confidence": entry["confidence"],
                    "contributor_count": entry["contributor_count"],
                    "low_confidence": entry["low_confidence"],
                },
                **values,
            )
        )
    return token, rows


def validate_draft(db, link, draft, entries=None) -> dict[str, SavedReviewFilter]:
    """Check freshness and bind every submitted row to exactly one served filter."""
    token, rows = project_draft(db, link, entries)
    if token != draft.token:
        raise _invalid("Community filters changed. Remove and re-add the source before saving.")
    originals = {row.community_filter_id: row for row in rows}
    seen = set()
    for row in draft.filters:
        fid = row.community_filter_id
        original = originals.get(fid)
        if (
            original is None
            or fid in seen
            or any(getattr(row, key) != getattr(original, key) for key in PROVENANCE)
        ):
            raise _invalid("Review filter provenance is invalid")
        seen.add(fid)
    return originals


def require_draft_edits(db, access, target, drafts) -> None:
    """Authorize edits independently of the permission to include a source."""
    for draft in drafts:
        link = db.get(CommunityMasterLinkRow, draft.link_id)
        if (
            link is None
            or target.owner is None
            or link.owner_id != access.principal.user_id
            or link.owner_id != target.owner.user_id
        ):
            raise AccessDenied(AccessErrorCode.CAPABILITY_DENIED)
        originals = validate_draft(db, link, draft)
        deleted = len(draft.filters) < len(originals)
        filter_edits, cue_edits = deleted, deleted
        for row in draft.filters:
            original = originals[row.community_filter_id]
            filter_edits |= any(
                getattr(row, key) != getattr(original, key) for key in FILTER_FIELDS
            )
            cue_edits |= any(getattr(row, key) != getattr(original, key) for key in CUE_FIELDS)
        if filter_edits:
            require(access, C.REVIEWS_FILTERS_EDIT_OWN, target)
        if cue_edits:
            require(access, C.REVIEWS_CUES_EDIT_OWN, target)
