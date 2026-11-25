"""Alignment for fuzzy master sets: preview a transform, then approve it (local API §4).

A master set has no source transcript, so the user places anchors (or enters an
offset). The preview fits `local = master × scale + offsetMs` and returns every row's
aligned bounds; clips play through the existing review draft-filter preview at those
bounds. Approval stores the previewed transform only while the link, its rows and
the revision still match the preview.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import threading
import time
from dataclasses import dataclass

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from scanner.db import CommunityMasterFilterRow, CommunityMasterUpdateRow, get_db
from scanner.integrations.community.transport import CommunityFailure
from scanner.modeling.community_master_alignment import (
    AnchorPair,
    fit_alignment,
    offset_alignment,
)
from scanner.modeling.community_master_diff import MasterValues
from scanner.modeling.community_master_state import aligned
from scanner.persistence.repository.community_master_filters import refresh_local_verification
from scanner.persistence.repository.community_master_links import (
    approve_alignment,
    require_link,
)
from scanner.persistence.write_lock import write_lock
from scanner.security.access.capability_ids import Capability as C
from scanner.security.access.grants import PrincipalAccess
from scanner.security.access.request_access import request_access

from .community_master_access import authorize_link, invalid, require_confirmed, respond
from .community_master_json import link_json
from .community_master_models import AlignmentApproval, AlignmentPreviewRequest

router = APIRouter(prefix="/api/community/masters/{link_id}/alignment", tags=["community"])

PREVIEW_TTL_SECONDS = 1800
MAX_PREVIEWS = 256


@dataclass(frozen=True)
class AlignmentPreviewRecord:
    owner_id: int
    link_id: str
    revision: int
    alignment: dict
    fingerprint: str
    expires_at: float


class AlignmentPreviews:
    """A bounded, expiring in-process map of preview token to its previewed transform."""

    def __init__(self, *, clock=time.monotonic):
        self.clock = clock
        self.lock = threading.Lock()
        self.records: dict[str, AlignmentPreviewRecord] = {}

    def put(self, **fields) -> str:
        with self.lock:
            now = self.clock()
            self.records = {k: v for k, v in self.records.items() if v.expires_at > now}
            while len(self.records) >= MAX_PREVIEWS:
                self.records.pop(next(iter(self.records)))
            token = secrets.token_urlsafe(24)
            self.records[token] = AlignmentPreviewRecord(
                **fields, expires_at=now + PREVIEW_TTL_SECONDS
            )
            return token

    def take(self, token: str, owner_id: int, link_id: str) -> AlignmentPreviewRecord | None:
        """Consume the caller's live preview for the link, or None."""
        with self.lock:
            record = self.records.get(token)
            if record is None or record.expires_at <= self.clock():
                self.records.pop(token, None)
                return None
            if (record.owner_id, record.link_id) != (owner_id, link_id):
                return None
            return self.records.pop(token)


previews = AlignmentPreviews()


def _master_rows(db: Session, link) -> dict[str, dict]:
    """Raw master values by filter ID: present rows' baselines, then new update rows."""
    found = {}
    for row in db.scalars(
        select(CommunityMasterFilterRow)
        .where(CommunityMasterFilterRow.link_id == link.id, CommunityMasterFilterRow.present)
        .order_by(CommunityMasterFilterRow.filter_id)
    ):
        found[row.filter_id] = json.loads(row.baseline_json)
    for item in db.scalars(
        select(CommunityMasterUpdateRow)
        .where(CommunityMasterUpdateRow.link_id == link.id)
        .order_by(CommunityMasterUpdateRow.filter_id)
    ):
        if item.change != "removed" and item.filter_id not in found:
            found[item.filter_id] = MasterValues.from_json(
                json.loads(item.new_master_json)
            ).as_json()
    return found


def _fingerprint(link, rows: dict[str, dict]) -> str:
    document = [link.latest_revision, link.alignment_state, sorted(rows.items())]
    return hashlib.sha256(json.dumps(document, separators=(",", ":")).encode()).hexdigest()


def _fit(body: AlignmentPreviewRequest, rows: dict[str, dict]) -> dict:
    if body.offset_ms is not None:
        return offset_alignment(body.offset_ms)
    pairs = []
    for index, anchor in enumerate(body.anchors):
        master = rows.get(anchor.filter_id)
        if master is None:
            raise invalid(f"anchors.{index}.filter_id", "Unknown filter")
        pairs.append(AnchorPair(master["startMs"], anchor.local_start_ms))
    return fit_alignment(pairs)


def _require_fuzzy(link) -> None:
    if link.alignment_state == "not_required":
        raise CommunityFailure("alignment_not_required")


@router.post("/preview")
def preview(
    link_id: str,
    body: AlignmentPreviewRequest,
    db: Session = Depends(get_db),
    access: PrincipalAccess = Depends(request_access),
):
    """Fit a transform and show every row on the local timeline; nothing is stored."""

    def action():
        link = authorize_link(db, access, link_id, C.COMMUNITY_DOWNLOAD)
        _require_fuzzy(link)
        rows = _master_rows(db, link)
        alignment = _fit(body, rows)
        token = previews.put(
            owner_id=access.principal.user_id,
            link_id=link.id,
            revision=link.latest_revision,
            alignment=alignment,
            fingerprint=_fingerprint(link, rows),
        )
        result = {
            "version": 1,
            "token": token,
            "alignment": alignment,
            "rows": [
                {
                    "filterId": filter_id,
                    "alignedStartMs": placed["startMs"],
                    "alignedEndMs": placed["endMs"],
                }
                for filter_id, values in sorted(
                    rows.items(), key=lambda item: (item[1]["startMs"], item[0])
                )
                for placed in (aligned(values, alignment),)
            ],
        }
        db.rollback()
        return result

    return respond(action)


@router.post("/approve")
def approve(
    link_id: str,
    body: AlignmentApproval,
    db: Session = Depends(get_db),
    access: PrincipalAccess = Depends(request_access),
):
    """Store the previewed transform for `for_revision` and return the link."""

    def action():
        require_confirmed(body.confirmed)
        owner = access.principal.user_id
        link = authorize_link(db, access, link_id, C.COMMUNITY_DOWNLOAD)
        _require_fuzzy(link)
        record = previews.take(body.token, owner, link.id)
        db.rollback()
        with write_lock(db):
            link = require_link(db, owner, link_id)
            if (
                record is None
                or record.revision != body.for_revision
                or record.fingerprint != _fingerprint(link, _master_rows(db, link))
            ):
                raise CommunityFailure("alignment_preview_changed")
            link = approve_alignment(db, owner, link_id, record.alignment, body.for_revision)
            refresh_local_verification(db, link)
            db.commit()
        result = {"version": 1, "link": link_json(db, require_link(db, owner, link_id))}
        db.rollback()
        return result

    return respond(action)
