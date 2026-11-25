"""The account's in-effect master filters as a review and EDL source (local API §3).

Master sets are a filter source next to scans; nothing is materialized as an
imported scan. A row is in effect when the master view marks it `active`: after
local decisions, the saved threshold and Low Confidence preference, and alignment.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from scanner.db import CommunityMasterLinkRow, ScanMediaVersionRow, ScanRow
from scanner.modeling.model_core import MCF_CATEGORY_TO_FAMILY
from scanner.models import EditAction, McfCategory, McfCategoryFamily, ScanFinding, Severity

from .community_master_filters import master_view
from .community_master_links import list_links

SOURCE = "community"


@dataclass(frozen=True)
class ActiveMasterFilter:
    """One in-effect filter on the local timeline, in milliseconds."""

    link_id: str
    filter_id: str
    category: str
    start_ms: int
    end_ms: int
    subcategory: str
    severity: str
    action: str
    confidence: float | None = None

    def as_json(self) -> dict:
        return {
            "linkId": self.link_id,
            "filterId": self.filter_id,
            "category": self.category,
            "startMs": self.start_ms,
            "endMs": self.end_ms,
            "subcategory": self.subcategory,
            "severity": self.severity,
            "action": self.action,
            "confidence": self.confidence,
        }


def version_duration_ms(db: Session, media_version_id: str) -> int | None:
    """The local version's playback duration from its completed scans, or None if unknown."""
    seconds = db.scalar(
        select(func.max(ScanRow.duration_seconds))
        .join(ScanMediaVersionRow, ScanMediaVersionRow.scan_id == ScanRow.id)
        .where(
            ScanMediaVersionRow.version_id == media_version_id,
            ScanRow.status == "complete",
            ScanRow.duration_seconds > 0,
        )
    )
    return int(seconds * 1000) if seconds else None


def clamp_range(start_ms: int, end_ms: int, duration_ms: int | None) -> tuple[int, int] | None:
    """Clip a local range to `[0, duration]`; None when nothing of it remains."""
    start = max(0, start_ms)
    end = end_ms if duration_ms is None else min(end_ms, duration_ms)
    return (start, end) if start < end else None


def link_active_filters(
    db: Session, owner_id: int, link: CommunityMasterLinkRow
) -> list[ActiveMasterFilter]:
    """The link's active effective filters in master view order, clipped to the local
    file; a filter wholly outside it is left out, so no EDL range is empty or negative."""
    view = master_view(db, owner_id, link.id)
    duration = version_duration_ms(db, link.media_version_id)
    found = []
    for row in view.filters:
        if not row.active:
            continue
        values = row.effective
        bounds = clamp_range(values["startMs"], values["endMs"], duration)
        if bounds is None:
            continue
        found.append(
            ActiveMasterFilter(
                link.id,
                row.filter_id,
                row.category,
                *bounds,
                values["subcategory"],
                values["severity"],
                values["action"],
                row.master.get("confidence"),
            )
        )
    return found


def active_master_filters(
    db: Session, owner_id: int, media_version_id: str
) -> list[ActiveMasterFilter]:
    """Every active filter of the account's unreplaced links to one local version."""
    found: list[ActiveMasterFilter] = []
    for link in list_links(db, owner_id, [media_version_id]):
        found.extend(link_active_filters(db, owner_id, link))
    return found


def as_findings(filters: list[ActiveMasterFilter], *, scan_id: str) -> list[ScanFinding]:
    """Selected findings for the existing EDL range merging (`export_edl_content`).

    `scan_id` names the source in the findings; these are not stored scan rows.
    """
    findings = []
    for position, item in enumerate(filters, start=1):
        family = MCF_CATEGORY_TO_FAMILY.get(item.subcategory)
        findings.append(
            ScanFinding(
                id=position,
                scan_id=scan_id,
                category=McfCategoryFamily(family) if family == item.category else None,
                subcategory=McfCategory(item.subcategory) if family == item.category else None,
                severity=Severity(item.severity),
                start_seconds=item.start_ms / 1000,
                end_seconds=item.end_ms / 1000,
                confidence=None,
                label="Community filter",
                source=SOURCE,
                selected=True,
                edit_action=EditAction(item.action),
            )
        )
    return findings
