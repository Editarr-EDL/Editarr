"""Qwen segment grouping, enrichment, and replay bundles."""

import json
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from scanner.evaluation.subcategorization import (
    SUBCATEGORIZATION_FALLBACKS,
    SubcategorizationMethod,
)
from scanner.models import FindingCreate, McfCategoryFamily
from scanner.pipeline.cache import atomic_write_json
from scanner.settings.capabilities import (
    DEFAULT_QWEN_MODEL,
    SUPPORTED_QWEN_MODELS,
    CapabilityUnavailableError,
)


@dataclass
class _FlaggedSegment:
    frames: list[tuple[Path, float]]
    findings: list[FindingCreate]
    end_seconds: float


def _flagged_segments(
    findings: list[FindingCreate],
    frame_paths: dict[float, Path],
    join_gap_seconds: float,
) -> list[_FlaggedSegment]:
    """Group flagged frames, allowing short clean gaps to stay together."""
    findings_by_timestamp: dict[float, list[FindingCreate]] = {}
    for finding in findings:
        findings_by_timestamp.setdefault(finding.start_seconds, []).append(finding)

    segments: list[_FlaggedSegment] = []
    for timestamp in sorted(findings_by_timestamp):
        frame_path = frame_paths.get(timestamp)
        if frame_path is None:
            continue
        timestamp_findings = findings_by_timestamp[timestamp]
        end_seconds = max(
            finding.end_seconds if finding.end_seconds is not None else timestamp
            for finding in timestamp_findings
        )
        if segments and timestamp - segments[-1].end_seconds <= join_gap_seconds + 1e-6:
            segments[-1].frames.append((frame_path, timestamp))
            segments[-1].findings.extend(timestamp_findings)
            segments[-1].end_seconds = max(segments[-1].end_seconds, end_seconds)
        else:
            segments.append(
                _FlaggedSegment([(frame_path, timestamp)], list(timestamp_findings), end_seconds)
            )
    return segments


def _representative_frames(
    segment: _FlaggedSegment, summaries_per_segment: int
) -> list[tuple[Path, float]]:
    """Pick flagged frames closest to evenly spaced positions in a segment."""
    frames = segment.frames
    if len(frames) <= summaries_per_segment:
        return frames
    if summaries_per_segment == 1:
        return [frames[0]]

    start_seconds = frames[0][1]
    target_end_seconds = frames[-1][1]
    targets = [
        start_seconds
        + ((target_end_seconds - start_seconds) * index / (summaries_per_segment - 1))
        for index in range(summaries_per_segment)
    ]
    selected: list[tuple[Path, float]] = []
    selected_timestamps: set[float] = set()
    for target in targets:
        # Prefer a later frame when candidates are equally close, so a target
        # located inside a clean gap advances to the next flagged frame.
        candidates = sorted(frames, key=lambda frame: (abs(frame[1] - target), -frame[1]))
        chosen = next((frame for frame in candidates if frame[1] not in selected_timestamps), None)
        if chosen is not None:
            selected.append(chosen)
            selected_timestamps.add(chosen[1])
    return selected


def _save_qwen_replay(
    replay_dir: Path,
    selected_by_segment: list[tuple[_FlaggedSegment, list[tuple[Path, float]]]],
    *,
    qwen_model: str,
    summaries_per_segment: int,
    join_gap_seconds: float,
) -> None:
    """Preserve Qwen's exact input frames for prompt-only replay experiments."""
    if not selected_by_segment:
        return

    replay_dir.mkdir(parents=True, exist_ok=True)
    segments = []
    for segment_index, (segment, selected_frames) in enumerate(selected_by_segment, start=1):
        frames = []
        for frame_index, (frame_path, timestamp) in enumerate(selected_frames, start=1):
            filename = f"segment_{segment_index:04d}_frame_{frame_index:02d}.jpg"
            shutil.copy2(frame_path, replay_dir / filename)
            frames.append({"filename": filename, "timestamp": timestamp})
        segments.append(
            {
                "end_seconds": segment.end_seconds,
                "findings": [finding.model_dump(mode="json") for finding in segment.findings],
                "frames": frames,
            }
        )

    atomic_write_json(
        replay_dir / "manifest.json",
        {
            "version": 1,
            "qwen_model": qwen_model,
            "summaries_per_segment": summaries_per_segment,
            "join_gap_seconds": join_gap_seconds,
            "segments": segments,
        },
    )


def _summarized_findings(
    segment: _FlaggedSegment, combined_summary
) -> list[FindingCreate]:
    """Combine one flagged segment and its Qwen summary into review findings."""
    from scanner.pipeline import visual as root

    findings_by_classification: dict[tuple[object, object], list[FindingCreate]] = {}
    for finding in segment.findings:
        findings_by_classification.setdefault(
            (finding.category, finding.subcategory), []
        ).append(finding)

    selected = not (
        bool(combined_summary.categories)
        and all(_is_no_objectionable_content(category) for category in combined_summary.categories)
    )
    summary_categories = tuple(
        "No objectionable content" if _is_no_objectionable_content(category) else category
        for category in combined_summary.categories
    )
    qwen_results = {
        result.category: result for result in combined_summary.subcategorization
    }
    summarized: list[FindingCreate] = []
    for classification_findings in findings_by_classification.values():
        first = classification_findings[0]
        labels = list(
            dict.fromkeys(
                finding.label for finding in classification_findings if finding.label
            )
        )
        qwen_result = qwen_results.get(first.category)
        subcategory = first.subcategory
        if (
            first.subcategory == SUBCATEGORIZATION_FALLBACKS[first.category]
            and qwen_result is not None
            and qwen_result.method == SubcategorizationMethod.QWEN_SUMMARY
        ):
            subcategory = qwen_result.subcategory
        summarized.append(
            FindingCreate(
                video_id=first.video_id,
                category=first.category,
                subcategory=subcategory,
                start_seconds=segment.frames[0][1],
                end_seconds=segment.end_seconds,
                severity=max(
                    (
                        finding.severity
                        for finding in classification_findings
                        if finding.severity
                    ),
                    key=lambda severity: root._SEVERITY_ORDER[severity],
                    default=None,
                ),
                confidence=max(
                    finding.confidence for finding in classification_findings
                ),
                label=", ".join(labels) or first.category.value,
                details=combined_summary.explanation,
                group_key="|".join(summary_categories) or first.category.value,
                mcf_channel=first.mcf_channel,
                mcf_comment=first.mcf_comment,
                source="visual",
                selected=selected,
            )
        )
    return summarized


def _is_no_objectionable_content(value: str) -> bool:
    normalized = value.strip().casefold().rstrip(".!")
    return normalized in {
        "no objectionable content",
        "no objectionable content detected",
    }


def _replay_finding(payload: object) -> FindingCreate:
    """Load current or pre-MCF replay evidence without mutating the bundle."""
    if not isinstance(payload, dict):
        raise ValueError("Qwen replay finding must be an object")
    values = dict(payload)
    if "subcategory" not in values:
        try:
            family = McfCategoryFamily(values["category"])
            values["subcategory"] = SUBCATEGORIZATION_FALLBACKS[family]
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Qwen replay finding has no valid MCF classification") from exc
    return FindingCreate(**values)


def replay_qwen_summaries(
    replay_dir: Path,
    *,
    prompt: str | None = None,
) -> list[FindingCreate]:
    """Rerun Qwen summaries from a replay bundle without rescanning a video."""
    manifest_path = replay_dir / "manifest.json"
    try:
        with manifest_path.open("r", encoding="utf-8") as file_obj:
            manifest = json.load(file_obj)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read Qwen replay manifest: {manifest_path}") from exc
    if not isinstance(manifest, dict) or manifest.get("version") != 1:
        raise ValueError(f"Unsupported Qwen replay manifest: {manifest_path}")
    segments = manifest.get("segments")
    if not isinstance(segments, list):
        raise ValueError(f"Qwen replay manifest has no segments: {manifest_path}")
    qwen_model = manifest.get("qwen_model", DEFAULT_QWEN_MODEL)
    if qwen_model not in SUPPORTED_QWEN_MODELS:
        raise ValueError(f"Qwen replay manifest has an unsupported model: {manifest_path}")

    from scanner.pipeline.qwen_summary import PROMPT, summarize_frames

    frame_prompt = prompt if prompt is not None else PROMPT
    findings: list[FindingCreate] = []
    for segment_data in segments:
        if not isinstance(segment_data, dict):
            raise ValueError(f"Qwen replay manifest has an invalid segment: {manifest_path}")
        raw_findings = segment_data.get("findings")
        frame_data = segment_data.get("frames")
        if not isinstance(raw_findings, list) or not isinstance(frame_data, list) or not frame_data:
            raise ValueError(f"Qwen replay manifest has an incomplete segment: {manifest_path}")
        try:
            segment_findings = [_replay_finding(item) for item in raw_findings]
            selected_frames = [
                (replay_dir / str(item["filename"]), float(item["timestamp"]))
                for item in frame_data
            ]
            end_seconds = float(segment_data["end_seconds"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                f"Qwen replay manifest has an invalid segment: {manifest_path}"
            ) from exc
        missing = [str(frame_path) for frame_path, _ in selected_frames if not frame_path.is_file()]
        if missing:
            raise ValueError(f"Qwen replay frame is missing: {missing[0]}")

        families = tuple(dict.fromkeys(finding.category for finding in segment_findings))
        combined = summarize_frames(
            [frame_path for frame_path, _ in selected_frames],
            prompt=frame_prompt,
            families=families,
            qwen_model=qwen_model,
        )
        findings.extend(
            _summarized_findings(
                _FlaggedSegment(selected_frames, segment_findings, end_seconds), combined
            )
        )
    return findings


def summarize_flagged_segments(
    findings: list[FindingCreate],
    frames: list[tuple[Path, float]],
    *,
    qwen_model: str = DEFAULT_QWEN_MODEL,
    summaries_per_segment: int,
    join_gap_seconds: float,
    replay_dir: Path | None = None,
    should_cancel: Callable[[], bool] | None = None,
    progress_callback: Callable[[int, int], None] | None = None,
    provenance_callback: Callable[[str], None] | None = None,
) -> list[FindingCreate]:
    """Apply one Qwen summary to representative frames after a scene is scanned."""
    if summaries_per_segment < 1:
        raise ValueError("Representative frames per flagged segment must be at least one")
    if join_gap_seconds < 0:
        raise ValueError("Qwen segment join gap cannot be negative")

    from scanner.pipeline import visual as root

    qwen = root.qwen_enrichment_capability()
    if qwen.availability == "unavailable":
        # Windows and Linux intentionally retain detector findings without prose.
        # Do this before selecting/copying replay frames so unavailable enrichment has
        # no model-cache or durable-replay side effect.  Other unavailability states
        # are configuration failures and must have been rejected before scan admission.
        if qwen.enrichment_behavior == "detector_findings_only":
            if provenance_callback:
                provenance_callback("unsupported")
            return findings
        detail = qwen.reason.replace("_", " ") if qwen.reason else "unavailable"
        raise CapabilityUnavailableError(f"{qwen.display_name} is unavailable: {detail}")

    frame_paths = {timestamp: frame_path for frame_path, timestamp in frames}
    segments = _flagged_segments(findings, frame_paths, join_gap_seconds)
    selected_by_segment = [
        (segment, _representative_frames(segment, summaries_per_segment)) for segment in segments
    ]
    if replay_dir is not None:
        _save_qwen_replay(
            replay_dir,
            selected_by_segment,
            qwen_model=qwen_model,
            summaries_per_segment=summaries_per_segment,
            join_gap_seconds=join_gap_seconds,
        )
    total_summaries = len(selected_by_segment)
    if not total_summaries:
        if provenance_callback:
            provenance_callback("no_inputs")
        return []

    from scanner.pipeline.qwen_summary import summarize_frames

    summarized_findings: list[FindingCreate] = []
    completed_summaries = 0
    if provenance_callback:
        provenance_callback("started")
    try:
        for segment, selected_frames in selected_by_segment:
            if should_cancel and should_cancel():
                from scanner.pipeline.processes import ScanCancelledError

                raise ScanCancelledError("Scan cancelled")
            families = tuple(dict.fromkeys(finding.category for finding in segment.findings))
            combined_summary = summarize_frames(
                [frame_path for frame_path, _timestamp in selected_frames],
                families=families,
                qwen_model=qwen_model,
            )
            completed_summaries += 1
            if progress_callback:
                progress_callback(completed_summaries, total_summaries)

            summarized_findings.extend(_summarized_findings(segment, combined_summary))
    except BaseException:
        if provenance_callback:
            provenance_callback("partial" if completed_summaries else "failed")
        raise
    if provenance_callback:
        provenance_callback("complete")
    return summarized_findings
