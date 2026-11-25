"""Turn a saved review's community filter snapshots into export, sidecar and subtitle input.

Snapshots are stored on the link's unclipped local timeline. Every consumer goes
through `exportable`, so EDL and MCF agree: a filter whose subcategory doesn't belong
to its category is left out of both (and reported by `unusable_filter_ids`), bounds
are clipped to the media runtime, and the review owner's enforced severities apply.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import replace

from scanner.mcf import McfCueEntry
from scanner.modeling.model_core import MCF_CATEGORY_TO_FAMILY
from scanner.models import McfCategory, McfChannel

from .community_master_sources import ActiveMasterFilter, as_findings, clamp_range

SOURCE_ID = "community-master"


def classified(item: ActiveMasterFilter) -> bool:
    """Whether the filter's subcategory belongs to its top-level category."""
    return MCF_CATEGORY_TO_FAMILY.get(item.subcategory) == item.category


def unusable_filter_ids(filters: Iterable[ActiveMasterFilter]) -> list[str]:
    """Snapshot filters left out of every export for an invalid classification."""
    return [item.filter_id for item in filters if not classified(item)]


def _severity(item: ActiveMasterFilter, severities: Mapping) -> str:
    enforced = severities.get(McfCategory(item.subcategory))
    return getattr(enforced, "value", enforced) or item.severity


def exportable(
    filters: Iterable[ActiveMasterFilter],
    *,
    severities: Mapping | None = None,
    duration_seconds: float | None = None,
) -> list[ActiveMasterFilter]:
    """The classified filters clipped to `[0, duration]`, with enforced severities."""
    duration_ms = round(duration_seconds * 1000) if duration_seconds else None
    found = []
    for item in filters:
        if not classified(item):
            continue
        bounds = clamp_range(item.start_ms, item.end_ms, duration_ms)
        if bounds is None:
            continue
        found.append(
            replace(
                item,
                start_ms=bounds[0],
                end_ms=bounds[1],
                severity=_severity(item, severities or {}),
            )
        )
    return found


def edl_findings(filters: list[ActiveMasterFilter], first_id: int):
    """EDL findings for already `exportable` filters, numbered from `first_id`."""
    return [
        finding.model_copy(update={"id": first_id + position})
        for position, finding in enumerate(as_findings(filters, scan_id=SOURCE_ID))
    ]


def mcf_entries(filters: list[ActiveMasterFilter], offset: int) -> list[McfCueEntry]:
    """MCF payloads for already `exportable` filters; a mute is an audio cue."""
    entries = []
    for item, finding in zip(filters, as_findings(filters, scan_id=SOURCE_ID), strict=True):
        position = offset + len(entries)
        entries.append(
            McfCueEntry(
                cue_index=position,
                payload_index=0,
                line_number=position + 1,
                start_seconds=finding.start_seconds,
                end_seconds=finding.end_seconds,
                category=finding.category,
                subcategory=finding.subcategory,
                severity=finding.severity,
                mcf_channel=McfChannel.AUDIO if item.action == "mute" else McfChannel.BOTH,
                mcf_comment=None,
            )
        )
    return entries
