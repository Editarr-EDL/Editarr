"""Spawned audio and visual workers and their result collection."""

import multiprocessing as mp
import time
from collections.abc import Callable
from pathlib import Path
from queue import Empty

from scanner.model_acquisition import AcquisitionCancelled, scoped_acquisition_cancellation
from scanner.models import FindingCreate
from scanner.pipeline.cache import load_cached_scenes, save_cached_scenes
from scanner.pipeline.frames import prepare_playable_video_stream
from scanner.pipeline.processes import ScanCancelledError
from scanner.pipeline.scenes import Scene
from scanner.runtime.process_control import enter_isolated_visual_worker
from scanner.settings.config import settings

_CANCELLATION_GRACE_SECONDS = 5.0


def _scan_audio_worker(
    audio_path: str,
    video_id: str,
    audio_model: str | None,
    audio_filter: str | None,
    deny_words: list[str] | None,
    transcript_cache_path_value: str | None,
    queue: mp.Queue,
    cancel_event,
    qwen_model: str | None = None,
    audio_padding_seconds: float | None = None,
    apple_speech_locale: str | None = None,
) -> None:
    """Run audio scanning in a subprocess to avoid native dylib collisions."""
    from scanner.pipeline import runner as root

    try:
        with scoped_acquisition_cancellation(cancel_event.is_set):
            findings = root.scan_audio(
                audio_path,
                video_id,
                audio_model,
                audio_filter,
                deny_words=deny_words,
                transcript_cache_path=transcript_cache_path_value,
                qwen_model=qwen_model,
                audio_padding_seconds=audio_padding_seconds,
                apple_speech_locale=apple_speech_locale,
                should_cancel=cancel_event.is_set,
                provenance_callback=lambda state: queue.put({"enrichment": state}),
                progress_callback=lambda fraction, message: queue.put(
                    {"progress": (fraction, message)}
                ),
            )
        queue.put({"findings": [f.model_dump() for f in findings]})
    except (AcquisitionCancelled, ScanCancelledError):
        queue.put({"cancelled": True})
    except Exception as exc:
        queue.put({"error": str(exc)})


def _scan_visual_worker(
    video_path: str,
    video_id: str,
    work_dir_value: str,
    coarse_scan_enabled: bool,
    coarse_scan_interval_seconds: float | None,
    qwen_model: str,
    qwen_summaries_per_segment: int,
    qwen_segment_join_gap_seconds: float,
    video_duration_seconds: float,
    video_hash: str,
    video_model: str | None,
    queue: mp.Queue,
    cancel_event,
    checkpoint_dir_value: str | None = None,
) -> None:
    """Run a visual pass in a disposable process so model memory is released."""
    from scanner.pipeline import runner as root

    # Keep FFmpeg in this helper's owned group without exposing POSIX session
    # mechanics to pipeline callers.
    enter_isolated_visual_worker()

    try:
        work_dir = Path(work_dir_value)
        scan_video_path = prepare_playable_video_stream(video_path, work_dir)
        threshold = settings.scene_detection_threshold
        cached_scene_data = load_cached_scenes(video_hash, threshold)
        cached_scenes = (
            [
                Scene(
                    start_seconds=float(item["start_seconds"]),
                    end_seconds=float(item["end_seconds"]),
                    index=int(item["index"]),
                )
                for item in cached_scene_data
            ]
            if cached_scene_data is not None
            else None
        )
        with scoped_acquisition_cancellation(cancel_event.is_set):
            findings = root._scan_visual_by_scene(
                scan_video_path,
                video_id,
                work_dir,
                coarse_scan_enabled=coarse_scan_enabled,
                coarse_scan_interval_seconds=coarse_scan_interval_seconds,
                qwen_model=qwen_model,
                qwen_summaries_per_segment=qwen_summaries_per_segment,
                qwen_segment_join_gap_seconds=qwen_segment_join_gap_seconds,
                video_duration_seconds=video_duration_seconds,
                cached_scenes=cached_scenes,
                scene_cache_callback=lambda scenes: save_cached_scenes(
                    video_hash,
                    threshold,
                    [
                        {
                            "start_seconds": scene.start_seconds,
                            "end_seconds": scene.end_seconds,
                            "index": scene.index,
                        }
                        for scene in scenes
                    ],
                ),
                progress_callback=lambda fraction, message: queue.put(
                    {"progress": (fraction, message)}
                ),
                should_cancel=cancel_event.is_set,
                video_model=video_model,
                provenance_callback=lambda state: queue.put({"enrichment": state}),
                **({"checkpoint_dir": Path(checkpoint_dir_value)} if checkpoint_dir_value else {}),
            )
        queue.put({"findings": [finding.model_dump(mode="json") for finding in findings]})
    except AcquisitionCancelled:
        queue.put({"cancelled": True})
    except Exception as exc:
        queue.put({"error": str(exc)})


def _scan_audio_isolated(
    audio_path: str,
    video_id: str,
    audio_model: str | None = None,
    audio_filter: str | None = None,
    deny_words: list[str] | None = None,
    transcript_cache_path_value: str | None = None,
    progress_callback: Callable[[float, str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    qwen_model: str | None = None,
    provenance_callback: Callable[[str], None] | None = None,
    audio_padding_seconds: float | None = None,
    apple_speech_locale: str | None = None,
) -> list[FindingCreate]:
    """Execute audio scanning in a spawned process and return findings."""
    from scanner.pipeline import runner as root

    ctx = mp.get_context("spawn")
    queue: mp.Queue = ctx.Queue()
    cancel_event = ctx.Event()
    proc = ctx.Process(
        target=root._scan_audio_worker,
        args=(
            audio_path,
            video_id,
            audio_model,
            audio_filter,
            deny_words,
            transcript_cache_path_value,
            queue,
            cancel_event,
            qwen_model,
            audio_padding_seconds,
            apple_speech_locale,
        ),
    )
    proc.start()

    return _collect_isolated_findings(
        proc, queue, cancel_event, progress_callback, should_cancel,
        kind="audio", provenance_callback=provenance_callback,
    )


def _terminate_worker(proc: mp.Process) -> None:
    """Stop an isolated scanner and, when possible, all its descendants."""
    from scanner.pipeline import runner as root

    root.terminate_isolated_process(proc)


def _scan_visual_isolated(
    video_path: str,
    video_id: str,
    work_dir: Path,
    coarse_scan_enabled: bool,
    coarse_scan_interval_seconds: float | None,
    qwen_model: str,
    qwen_summaries_per_segment: int,
    qwen_segment_join_gap_seconds: float,
    video_duration_seconds: float,
    video_hash: str,
    video_model: str | None,
    progress_callback: Callable[[float, str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    provenance_callback: Callable[[str], None] | None = None,
    checkpoint_dir: Path | None = None,
) -> list[FindingCreate]:
    """Run a heavyweight visual model in a short-lived process and relay progress."""
    from scanner.pipeline import runner as root

    ctx = mp.get_context("spawn")
    queue: mp.Queue = ctx.Queue()
    cancel_event = ctx.Event()
    proc = ctx.Process(
        target=root._scan_visual_worker,
        args=(
            video_path,
            video_id,
            str(work_dir),
            coarse_scan_enabled,
            coarse_scan_interval_seconds,
            qwen_model,
            qwen_summaries_per_segment,
            qwen_segment_join_gap_seconds,
            video_duration_seconds,
            video_hash,
            video_model,
            queue,
            cancel_event,
            *((str(checkpoint_dir),) if checkpoint_dir is not None else ()),
        ),
    )
    proc.start()

    return _collect_isolated_findings(
        proc, queue, cancel_event, progress_callback, should_cancel,
        kind="visual", provenance_callback=provenance_callback,
    )


def _collect_isolated_findings(
    proc: mp.Process,
    queue: mp.Queue,
    cancel_event,
    progress_callback: Callable[[float, str], None] | None,
    should_cancel: Callable[[], bool] | None,
    *,
    kind: str,
    provenance_callback: Callable[[str], None] | None = None,
) -> list[FindingCreate]:
    """Relay child progress and return findings, honoring either cancellation source.

    Any exception raised while relaying (such as an authority refusal from
    ``should_cancel`` or ``progress_callback``) stops the child before propagating.
    """
    from scanner.pipeline import runner as root

    result: dict[str, object] | None = None
    deadline: float | None = None
    cancellation_deadline: float | None = None
    try:
        while result is None:
            if should_cancel and should_cancel():
                cancel_event.set()
                if cancellation_deadline is None:
                    cancellation_deadline = time.monotonic() + _CANCELLATION_GRACE_SECONDS
            if cancellation_deadline is not None and time.monotonic() >= cancellation_deadline:
                root._terminate_worker(proc)
                raise ScanCancelledError("Scan cancelled")
            try:
                message = queue.get(timeout=0.2)
            except Empty:
                if proc.is_alive():
                    continue
                if deadline is None:
                    # The queue feeder may still be flushing after its child exits.
                    deadline = time.monotonic() + 2.0
                elif time.monotonic() >= deadline:
                    break
                continue

            if "progress" in message:
                progress = message["progress"]
                if progress_callback and isinstance(progress, tuple) and len(progress) == 2:
                    progress_callback(float(progress[0]), str(progress[1]))
            elif "enrichment" in message:
                if provenance_callback:
                    provenance_callback(str(message["enrichment"]))
            elif "findings" in message or "error" in message or "cancelled" in message:
                result = message
    except BaseException:
        # A refused authority recheck or failed callback must not orphan the child.
        cancel_event.set()
        if proc.is_alive():
            root._terminate_worker(proc)
        raise

    proc.join(timeout=2)
    if cancellation_deadline is not None:
        if proc.is_alive():
            root._terminate_worker(proc)
        raise ScanCancelledError("Scan cancelled")
    if result is None:
        raise RuntimeError(f"{kind.title()} scan process exited with code {proc.exitcode}")
    if "cancelled" in result:
        raise ScanCancelledError("Scan cancelled")
    if "error" in result:
        description = "Audio scan" if kind == "audio" else "Visual scan process"
        raise RuntimeError(f"{description} failed: {result['error']}")
    findings = result.get("findings", [])
    if not isinstance(findings, list):
        raise RuntimeError(f"{kind.title()} scan process returned invalid findings")
    return [FindingCreate(**finding) for finding in findings]
