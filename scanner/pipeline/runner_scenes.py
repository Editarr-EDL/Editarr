"""Scene-aware visual passes for the scan orchestrator."""

from collections.abc import Callable
from pathlib import Path

from scanner.models import FindingCreate
from scanner.pipeline.processes import ScanCancelledError
from scanner.pipeline.scenes import Scene
from scanner.settings.capabilities import DEFAULT_QWEN_MODEL


def _scan_visual_by_scene(
    video_path: str,
    video_id: str,
    work_dir: Path,
    coarse_scan_enabled: bool,
    coarse_scan_interval_seconds: float | None,
    qwen_model: str = DEFAULT_QWEN_MODEL,
    qwen_summaries_per_segment: int = 4,
    qwen_segment_join_gap_seconds: float = 3.0,
    video_duration_seconds: float | None = None,
    cached_scenes: list[Scene] | None = None,
    scene_cache_callback: Callable[[list[Scene]], None] | None = None,
    progress_callback: Callable[[float, str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    video_model: str | None = None,
    provenance_callback: Callable[[str], None] | None = None,
) -> list[FindingCreate]:
    """Scan each scene, caching its findings and reporting progress to the caller."""

    from scanner.pipeline import runner as root

    if coarse_scan_enabled and (
        coarse_scan_interval_seconds is None or coarse_scan_interval_seconds <= 0
    ):
        raise ValueError("Coarse scan interval must be greater than zero")
    if qwen_summaries_per_segment < 1:
        raise ValueError("Representative frames per flagged segment must be at least one")
    if qwen_segment_join_gap_seconds < 0:
        raise ValueError("Qwen segment join gap cannot be negative")
    scene_detection_weight = 0.15
    if cached_scenes is not None:
        scenes = cached_scenes
        if progress_callback:
            progress_callback(0.0, "Detecting scene boundaries")
            progress_callback(scene_detection_weight, "Loaded cached scene boundaries")
    else:
        if progress_callback:
            progress_callback(0.0, "Detecting scene boundaries")
        scenes = root.detect_scenes(
            video_path,
            duration_seconds=video_duration_seconds,
            progress_callback=(
                (
                    lambda fraction, message: progress_callback(
                        scene_detection_weight * fraction,
                        message,
                    )
                )
                if progress_callback
                else None
            ),
            should_cancel=should_cancel,
        )
        if scene_cache_callback:
            scene_cache_callback(scenes)
    findings: list[FindingCreate] = []
    skipped = 0
    result_dir = work_dir / "visual-results"
    result_dir.mkdir(parents=True, exist_ok=True)

    if not scenes:
        return findings

    source_frame_interval = root.get_video_frame_interval(video_path, video_duration_seconds)
    total_scenes = len(scenes)
    for idx, scene in enumerate(scenes, start=1):
        if should_cancel and should_cancel():
            raise ScanCancelledError("Scan cancelled")
        scene_start = scene_detection_weight + (
            ((idx - 1) / total_scenes) * (1.0 - scene_detection_weight)
        )
        scene_weight = (1.0 - scene_detection_weight) / total_scenes
        if progress_callback:
            progress_callback(scene_start, f"Visual scan: scene {idx}/{total_scenes}")

        result_path = result_dir / f"scene_{scene.index:04d}.json"
        cached_findings = root._load_cached_visual_findings(result_path)
        if cached_findings is not None:
            if provenance_callback:
                provenance_callback(f"scene_{scene.index:04d}:reused")
            findings.extend(cached_findings)
            if progress_callback:
                progress_callback(
                    scene_start + scene_weight,
                    f"Visual scan: scene {idx}/{total_scenes} restored from cache",
                )
            continue

        fine_extract_start = 0.0
        fine_extract_weight = 0.5
        fine_scan_start = 0.5
        fine_scan_weight = 0.4
        fine_summary_start = 0.9
        fine_summary_weight = 0.1
        if coarse_scan_enabled:
            coarse_dir = work_dir / "coarse" / f"scene_{scene.index:04d}"
            coarse_frames = root.sample_scene_frames(
                video_path,
                scene,
                coarse_dir,
                interval_seconds=coarse_scan_interval_seconds,
                should_cancel=should_cancel,
                progress_callback=(
                    (
                        lambda fraction, message: progress_callback(
                            scene_start + (scene_weight * 0.35 * fraction),
                            f"Coarse sample: scene {idx}/{total_scenes} — {message}",
                        )
                    )
                    if progress_callback
                    else None
                ),
            )
            flagged = False
            for coarse_idx, (frame_path, timestamp) in enumerate(coarse_frames, start=1):
                if should_cancel and should_cancel():
                    raise ScanCancelledError("Scan cancelled")
                if root.scan_frame(frame_path, timestamp, video_model=video_model):
                    flagged = True
                    break
                if progress_callback:
                    progress_callback(
                        scene_start
                        + (scene_weight * (0.35 + (0.15 * coarse_idx / len(coarse_frames)))),
                        (
                            f"Coarse scan: scene {idx}/{total_scenes}, "
                            f"frame {coarse_idx}/{len(coarse_frames)}"
                        ),
                    )
            if not coarse_frames:
                root.logger.info("Scene %d has no coarse sample; fine-scanning it", scene.index)
                flagged = True
            if not flagged:
                skipped += 1
                root.atomic_write_json(result_path, [])
                if progress_callback:
                    progress_callback(
                        scene_start + scene_weight,
                        f"Visual scan: scene {idx}/{total_scenes} complete",
                    )
                continue
            fine_extract_start = 0.50
            fine_extract_weight = 0.25
            fine_scan_start = 0.75
            fine_scan_weight = 0.20
            fine_summary_start = 0.95
            fine_summary_weight = 0.05

        root.logger.info(
            "Scene %d (%.1f–%.1fs): fine-scanning every source frame",
            scene.index,
            scene.start_seconds,
            scene.end_seconds,
        )
        fine_dir = work_dir / "fine" / f"scene_{scene.index:04d}"
        fine_frames = root.sample_scene_frames(
            video_path,
            scene,
            fine_dir,
            interval_seconds=None,
            timestamp_interval_seconds=source_frame_interval,
            should_cancel=should_cancel,
            progress_callback=(
                (
                    lambda fraction, message: progress_callback(
                        scene_start
                        + (scene_weight * (fine_extract_start + (fine_extract_weight * fraction))),
                        f"Fine sample: scene {idx}/{total_scenes} — {message}",
                    )
                )
                if progress_callback
                else None
            ),
        )
        scene_findings = root.scan_frames(
            fine_frames,
            video_id,
            video_model=video_model,
            frame_interval_seconds=source_frame_interval,
            video_duration_seconds=video_duration_seconds,
            progress_callback=(
                (lambda done, total: progress_callback(
                    scene_start
                    + (
                        scene_weight
                        * (fine_scan_start + (fine_scan_weight * done / max(total, 1)))
                    ),
                    f"Visual scan: scene {idx}/{total_scenes}, frame {done}/{total}",
                ))
                if progress_callback and fine_frames
                else None
            ),
        )
        scene_findings = root.summarize_flagged_segments(
            scene_findings,
            fine_frames,
            qwen_model=qwen_model,
            summaries_per_segment=qwen_summaries_per_segment,
            join_gap_seconds=qwen_segment_join_gap_seconds,
            replay_dir=root.settings.qwen_replays_dir / video_id / f"scene_{scene.index:04d}",
            should_cancel=should_cancel,
            provenance_callback=(
                (lambda state: provenance_callback(f"scene_{scene.index:04d}:{state}"))
                if provenance_callback else None
            ),
            progress_callback=(
                (lambda done, total: progress_callback(
                    scene_start
                    + (scene_weight * (
                        fine_summary_start + (fine_summary_weight * done / max(total, 1))
                    )),
                    f"Visual summary: scene {idx}/{total_scenes}, segment {done}/{total}",
                ))
                if progress_callback
                else None
            ),
        )
        root.atomic_write_json(
            result_path,
            [finding.model_dump(mode="json") for finding in scene_findings],
        )
        findings.extend(scene_findings)

        if should_cancel and should_cancel():
            raise ScanCancelledError("Scan cancelled")

        if progress_callback:
            progress_callback(
                scene_start + scene_weight,
                f"Visual scan: scene {idx}/{total_scenes} complete",
            )

    root.logger.info(
        "Scene-based visual scan: %d/%d scenes skipped by coarse scanning, %d findings",
        skipped,
        len(scenes),
        len(findings),
    )
    return findings

