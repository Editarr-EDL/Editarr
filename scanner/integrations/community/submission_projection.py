"""Project a local scan or saved review, and its master link, into a v3 submission
(contract §5.2, local API §7, D21–D22).

| Source | Entry |
| --- | --- |
| Kept review or scan cue | `support`, proposing the master filter it overlaps |
| `rejected` master row | `dispute` |
| `verified`/`adjusted` row no cue proposes | `support` with the effective values |
| Untouched master row | not shared |
| Completed scan stages | `coverage` |

All times are milliseconds on the local file timeline.
"""

import json
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

from scanner.formats.community.artifact_v3_models import ArtifactV3
from scanner.modeling.community_filter_proposals import (
    Cue,
    Estimates,
    LinkedFilter,
    estimate,
    inverse_alignment,
    propose_filters,
    shared_rows,
)
from scanner.modeling.community_master_state import intervals_overlap
from scanner.modeling.community_match_keys import FileKey, HashKey
from scanner.modeling.community_work_identity import WorkIdentity
from scanner.modeling.model_core import MCF_CATEGORY_TO_FAMILY

MAX_ENTRIES = 2000
MAX_INTERVALS = 512
# Categories a completed generated stage examined across its recorded intervals. Kept
# deliberately narrow: coverage creates implicit negatives on other contributors' filters.
STAGE_COVERAGE = {"audio_scan": ("language",), "visual": ("nudity", "sex", "violence")}
_ORIGINS = {"generated": "model", "manual": "manual", "imported": "downloaded"}


@dataclass(frozen=True)
class LinkProjection:
    """A master link as sharing needs it: rows already on the local timeline."""

    version_id: str
    revision: int
    alignment: dict | None
    filters: tuple[LinkedFilter, ...]


@dataclass(frozen=True)
class SubmissionOptions:
    submission_id: str
    contributor_id: str
    key_id: str
    created_at: int
    work: WorkIdentity
    version_choice: dict
    duration_ms: int
    match_keys: tuple[FileKey, ...]


@dataclass(frozen=True)
class Projection:
    artifact: ArtifactV3
    estimates: Estimates
    warnings: tuple[str, ...]


def _ms(seconds: float, duration_ms: int) -> int:
    return max(0, min(duration_ms, int(round(seconds * 1000))))


def _value(item):
    return getattr(item, "value", item)


def _evidence(origin: str, manual_review: bool, verified: bool, model_version: str | None):
    return {
        "origin": origin,
        "manualReview": manual_review,
        "locallyVerified": verified,
        "modelVersion": model_version if origin == "model" else None,
    }


def _model_version(scan: dict) -> str:
    for field in ("release_version", "scanner_behavior_version", "audio_model", "video_model"):
        value = scan.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip().encode("utf-8")[:128].decode("utf-8", "ignore")
    return "unknown"


def _model_findings(data: dict) -> dict[str, list[tuple[float, float | None]]]:
    """Local model findings by category, from the captured generated scans."""
    generated = {scan["id"] for scan in data["scans"] if scan["origin"] == "generated"}
    rows = data["policy_evidence"] or data["base_findings"]
    found = defaultdict(list)
    for row in rows:
        if row.get("scan_id") in generated and row.get("category"):
            found[_value(row["category"])].append((row["start_seconds"], row["end_seconds"]))
    return found


def _cues(data: dict, kind: str, duration_ms: int, warnings: list[str]):
    """Kept, classified cues with their evidence, on the local timeline."""
    scans = {scan["id"]: scan for scan in data["scans"]}
    model = _model_findings(data)
    cues = []
    for row in data["findings"]:
        if not row.get("selected", True):
            continue
        category, subcategory = _value(row.get("category")), row.get("subcategory")
        severity, end = _value(row.get("severity")), row.get("end_seconds")
        if (
            not (category and subcategory and severity)
            or end is None
            or _value(MCF_CATEGORY_TO_FAMILY.get(subcategory)) != category
        ):
            warnings.append("unclassified_cue_skipped")
            continue
        start_ms, end_ms = _ms(row["start_seconds"], duration_ms), _ms(end, duration_ms)
        if not start_ms < end_ms:
            warnings.append("cue_outside_media")
            continue
        scan = scans[row.get("source_scan_id", row.get("scan_id"))]
        edited = bool(row.get("manually_added") or row.get("manually_edited"))
        if kind == "review" and row.get("source_finding_id") is None:
            origin = "manual"
        else:
            origin = _ORIGINS[scan["origin"]]
        review = kind == "review" or origin == "manual" or edited
        verified = (
            origin != "downloaded"
            or edited
            or any(
                intervals_overlap(start_ms, end_ms, start, stop) for start, stop in model[category]
            )
        )
        cues.append(
            (
                Cue(start_ms, end_ms, category),
                {
                    "subcategory": subcategory,
                    "severity": severity,
                    "action": "mute" if row.get("edit_action") == "mute" else "skip",
                    "evidence": _evidence(origin, review, verified, _model_version(scan)),
                },
            )
        )
    return cues


def _coverage(data: dict, kind: str, duration_ms: int) -> list[dict]:
    """Completed stage intervals per category; reviews may be retimed, so only scans."""
    if kind != "scan" or data["root"]["origin"] != "generated":
        return []
    spans = defaultdict(list)
    for stage in data["stages"]:
        if stage["status"] != "complete" or stage["stage"] not in STAGE_COVERAGE:
            continue
        for interval in _intervals(stage.get("intervals_json")):
            start = _ms(interval["start_seconds"], duration_ms)
            end = _ms(interval["end_seconds"], duration_ms)
            if start < end:
                for category in STAGE_COVERAGE[stage["stage"]]:
                    spans[category].append((start, end))
    coverage = []
    for category in sorted(spans):
        merged: list[list[int]] = []
        for start, end in sorted(spans[category]):
            if merged and start <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([start, end])
        if len(merged) <= MAX_INTERVALS:
            intervals = [{"startMs": start, "endMs": end} for start, end in merged]
            coverage.append({"category": category, "intervals": intervals})
    return coverage


def _intervals(encoded) -> list[dict]:
    try:
        value = json.loads(encoded or "[]")
    except ValueError:
        return []
    return [
        item
        for item in value
        if isinstance(item, dict)
        and isinstance(item.get("start_seconds"), (int, float))
        and isinstance(item.get("end_seconds"), (int, float))
    ]


def _row_entry(row: LinkedFilter, kind: str, duration_ms: int) -> dict | None:
    start, end = max(0, row.start_ms), min(duration_ms, row.end_ms)
    if not start < end:
        return None
    support = kind == "support"
    return {
        "kind": kind,
        "proposedFilterId": row.filter_id,
        "startMs": start,
        "endMs": end,
        "category": row.category,
        "subcategory": row.subcategory if support else None,
        "severity": row.severity if support else None,
        "action": row.action if support else None,
        "evidence": _evidence("downloaded", True, support, None),
    }


def _key_json(key: FileKey) -> dict:
    if isinstance(key, HashKey):
        return {"type": key.type, "value": key.value, "sizeBytes": key.size_bytes}
    return {"type": key.type, "algorithm": key.algorithm, "value": key.value}


def _hint(link: LinkProjection | None) -> dict | None:
    if link is None:
        return None
    inverse = inverse_alignment(link.alignment)
    if inverse is None:
        return None
    anchors = (link.alignment or {}).get("anchors", 0)
    return {
        "versionId": link.version_id,
        "revision": link.revision,
        "offsetMs": inverse[0],
        "scale": inverse[1],
        "anchors": anchors if isinstance(anchors, int) and 0 <= anchors <= 2000 else 0,
    }


def project_submission(
    data: dict, kind: str, options: SubmissionOptions, link: LinkProjection | None
) -> Projection:
    """Build the validated artifact and the ≈ estimates against the linked master set.

    `data` is a captured scan or review source (`donation_sources.capture_source`).
    """
    warnings: list[str] = []
    duration = options.duration_ms
    cues = _cues(data, kind, duration, warnings)
    rows: Iterable[LinkedFilter] = link.filters if link else ()
    proposals = propose_filters([cue for cue, _values in cues], rows)
    disputes, supports = shared_rows(rows, proposals.values())
    entries = [
        {
            "kind": "support",
            "proposedFilterId": proposals.get(index),
            "startMs": cue.start_ms,
            "endMs": cue.end_ms,
            "category": cue.category,
            **values,
        }
        for index, (cue, values) in enumerate(cues)
    ]
    for row in supports:
        entries.append(_row_entry(row, "support", duration))
    for row in disputes:
        entries.append(_row_entry(row, "dispute", duration))
    if None in entries:
        warnings.append("master_row_outside_media")
    entries = [entry for entry in entries if entry is not None]
    if len(entries) > MAX_ENTRIES:
        raise ValueError("A submission holds at most 2,000 entries")
    document = {
        "schemaVersion": "community-artifact-3",
        "submissionId": options.submission_id,
        "createdAt": options.created_at,
        "author": {"contributorId": options.contributor_id, "keyId": options.key_id},
        "work": options.work.as_artifact(),
        "version": options.version_choice,
        "media": {
            "durationMs": duration,
            "matchKeys": [_key_json(key) for key in options.match_keys],
        },
        "alignmentHint": _hint(link),
        "coverage": _coverage(data, kind, duration),
        "entries": [{"entryId": f"e{index + 1}", **entry} for index, entry in enumerate(entries)],
    }
    artifact = ArtifactV3.model_validate(document, strict=True, by_alias=True, by_name=False)
    return Projection(
        artifact,
        estimate(proposals, len(cues), supports, disputes),
        tuple(sorted(set(warnings))),
    )
