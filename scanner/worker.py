"""Dedicated, restart-tolerant process for one Editarr scan."""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
from contextlib import ExitStack, contextmanager
from functools import partial
from threading import Event, Thread

from scanner.runtime.runtime_paths import bootstrap_entrypoint_runtime_paths

# Source workers are launched independently of scanner.cli, so initialize before
# scanner.db/config and pipeline imports. Frozen workers use the internal role.
bootstrap_entrypoint_runtime_paths()

from scanner.runtime.runtime_logging import (  # noqa: E402
    configure_runtime_logging,
    redirect_standard_streams,
)
from scanner.settings.config import settings  # noqa: E402
from scanner.settings.diagnostics import debug_logging_enabled  # noqa: E402

# Establish diagnostics before importing the database or pipeline. A cold native
# launch may fail during those imports, before it can publish its control endpoint.
_native_launcher = (
    os.environ.get("EDITARR_NATIVE_LAUNCHER") == "1" or getattr(sys, "frozen", False)
) and os.environ.get("EDITARR_NATIVE_FOREGROUND") != "1"
configure_runtime_logging(
    settings.log_dir,
    process_role="worker" if _native_launcher else None,
    debug_enabled=debug_logging_enabled(settings.data_dir),
)
if _native_launcher:
    redirect_standard_streams(settings.log_dir, "worker")

from scanner.db import get_session_factory  # noqa: E402
from scanner.modeling.model_acquisition_authority import scoped_job_acquisition  # noqa: E402
from scanner.modeling.model_acquisition_service import (  # noqa: E402
    DOWNLOADS_REFUSED_DETAIL,
    refuse_model_downloads,
)
from scanner.persistence.migrations.preflight import (  # noqa: E402
    require_owner_setup,
    require_supported_schema,
)
from scanner.persistence.repository.scan_jobs import fail_refused_scan_job  # noqa: E402
from scanner.pipeline.processes import ScanCancelledError  # noqa: E402
from scanner.repository import (  # noqa: E402
    clear_scan_worker,
    complete_credits_detection,
    create_scan_findings_batch,
    get_scan_local_path,
    get_scan_queue_item_by_scan_id,
    heartbeat_scan_worker,
    is_scan_cancellation_requested,
    mark_scan_checkpoint,
    mark_scan_orphaned,
    record_scan_stage,
    remove_scan_queue_item,
    scan_checkpoint_stages,
    update_scan_duration,
    update_scan_progress,
    update_scan_status,
)
from scanner.runtime.maintenance import DatabaseMaintenance, MaintenanceUnavailable  # noqa: E402
from scanner.runtime.process_control import (  # noqa: E402
    WorkerControlServer,
    assign_current_worker_job,
)
from scanner.runtime.scan_version import (  # noqa: E402
    STALE_AUDIO_SELECTION,
    ScanVersionChanged,
    prepare_scan_digest,
    require_scan_digest,
    scan_digest_progress,
)
from scanner.runtime.worker_quit import FullApplicationQuitRequested  # noqa: E402
from scanner.security.scan_jobs.authority import (  # noqa: E402
    REFUSAL_MESSAGES,
    JobGate,
    JobRefusal,
    JobRefusedError,
)

logger = logging.getLogger(__name__)


def run_scan(*args, **kwargs):
    """Load the pipeline only after worker identity and heartbeat are available."""
    from scanner.pipeline.audio_tracks import StaleAudioSelectionError
    from scanner.pipeline.runner import run_scan as run_pipeline

    try:
        return run_pipeline(*args, **kwargs)
    except StaleAudioSelectionError as exc:
        raise ScanVersionChanged(STALE_AUDIO_SELECTION) from exc


def run_credits_scan(*args, **kwargs):
    """Load credits analysis only inside an identified dedicated worker."""
    from scanner.credits.credits_scan import run_credits_scan as run_pipeline

    return run_pipeline(*args, **kwargs)


@contextmanager
def _prevent_idle_system_sleep():
    """Keep macOS awake for a scan while still permitting display sleep."""
    if sys.platform != "darwin":
        yield
        return

    process: subprocess.Popen[bytes] | None = None
    try:
        process = subprocess.Popen(
            ["caffeinate", "-i", "-w", str(os.getpid())],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        logger.warning("Unable to prevent idle system sleep during scan", exc_info=True)

    try:
        yield
    finally:
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def _heartbeat_loop(scan_id: str, worker_token: str, stop_event: Event) -> None:
    """Keep an adopted worker visible while a long scanner stage is blocking."""
    factory = get_session_factory()
    while not stop_event.wait(1):
        try:
            db = factory()
            try:
                if not heartbeat_scan_worker(db, scan_id, worker_token):
                    return
            finally:
                db.close()
        except MaintenanceUnavailable:
            return
        except Exception:
            logger.exception("Scan worker heartbeat failed for %s; retrying", scan_id)


def run_scan_worker(scan_id: str, worker_token: str) -> int:
    """Keep a live worker in the maintenance barrier until its DB use ends."""
    with DatabaseMaintenance(settings.data_dir).worker_lease():
        require_supported_schema()
        require_owner_setup()
        return _run_scan_worker(scan_id, worker_token)


def _run_scan_worker(scan_id: str, worker_token: str) -> int:
    """Run one scan independently from the API server's lifetime."""
    logger.info("Starting dedicated scan worker for %s", scan_id)
    factory = get_session_factory()
    db = factory()
    heartbeat_stop = Event()
    quit_stop = Event()
    heartbeat_thread: Thread | None = None
    control_server: WorkerControlServer | None = None
    worker_job = None
    item = None
    acquisition_scope = ExitStack()
    try:
        item = get_scan_queue_item_by_scan_id(db, scan_id)
        if not item or not heartbeat_scan_worker(db, scan_id, worker_token):
            logger.warning("No matching worker reservation for scan %s", scan_id)
            return 0
        logger.info(
            "Scan %s accepted (audio=%s/%s, video=%s/%s)",
            scan_id,
            item.scan_audio,
            item.audio_model if item.scan_audio else "off",
            item.scan_video,
            item.video_model if item.scan_video else "off",
        )
        # A Windows source launch can begin under a venv redirector PID. Ensure
        # this actual interpreter is in the token-derived Job before exposing its
        # authenticated control endpoint to the launcher.
        worker_job = assign_current_worker_job(scan_id, worker_token)
        control_server = WorkerControlServer(scan_id, worker_token, quit_stop)
        control_server.start()
        logger.info("Worker control endpoint ready for scan %s", scan_id)
        heartbeat_thread = Thread(
            target=_heartbeat_loop,
            args=(scan_id, worker_token, heartbeat_stop),
            daemon=True,
        )
        heartbeat_thread.start()
        gate = JobGate(factory, scan_id)
        acquisition_scope.enter_context(scoped_job_acquisition(gate, children=True))
        allowed = gate.require()
        if allowed is not None and not allowed.may_download_models:
            refuse_model_downloads()
        local_path = get_scan_local_path(db, scan_id)
        if not local_path:
            logger.error("Scan %s cannot start because its source path is unavailable", scan_id)
            mark_scan_orphaned(db, scan_id, "Orphaned scan — source path is unavailable")
            return 1
        digest = prepare_scan_digest(
            db,
            scan_id,
            local_path,
            write=partial(gate.run, db),
            on_progress=scan_digest_progress(
                partial(gate.run, db, update_scan_progress, scan_id, worker_token=worker_token)
            ),
        )

        def update_status(session, item_id, status):
            if status == "complete":
                require_scan_digest(local_path, digest)
            return gate.run(session, update_scan_status, item_id, status)

        def save_credits(session, **result):
            require_scan_digest(local_path, digest)
            return gate.run(session, complete_credits_detection, **result)

        checkpoints = scan_checkpoint_stages(db, scan_id, require_provenance=True)
        logger.debug(
            "Scan %s resuming with completed checkpoints: %s", scan_id, sorted(checkpoints)
        )
        with _prevent_idle_system_sleep():
            if item.scan_mode == "credits_detection":
                assert item.credits_detection is not None
                run_credits_scan(
                    db,
                    local_path,
                    scan_id=item.id,
                    configuration=item.credits_detection.configuration,
                    video_duration_seconds=item.duration_seconds,
                    video_hash=item.video_hash,
                    status_updater=update_status,
                    result_writer=save_credits,
                    content_sha256=digest,
                    scan_metadata_updater=lambda session, *args: gate.run(
                        session, update_scan_duration, *args
                    ),
                    should_cancel=lambda: _should_stop_scan(db, item.id, quit_stop, gate),
                    progress_callback=partial(gate.run, db, update_scan_progress, item.id),
                    stage_callback=partial(
                        gate.run,
                        db,
                        _record_stage,
                        item.id,
                        worker_token,
                    ),
                )
            else:
                from scanner.modeling.model_audio_tracks import AudioSelection
                from scanner.persistence.repository.repository_audio_tracks import (
                    record_analyzed_audio_selection,
                )

                if item.scan_audio and item.requested_audio_selection is None:
                    raise ValueError("Audio input is unknown; choose a track and start a new scan")
                if item.scan_audio and item.analyzed_audio_selection is not None:
                    if item.analyzed_audio_selection != item.requested_audio_selection:
                        raise ValueError("Audio provenance changed; start a new scan")
                if (
                    item.scan_audio
                    and "audio_scan" in checkpoints
                    and item.analyzed_audio_selection is None
                ):
                    raise ValueError("Audio checkpoint provenance is unknown; start a new scan")
                run_scan(
                    db,
                    local_path,
                    coarse_scan_enabled=item.coarse_scan_enabled,
                    coarse_scan_interval_seconds=item.coarse_scan_interval,
                    qwen_model=item.qwen_model,
                    qwen_summaries_per_segment=item.qwen_summaries_per_segment,
                    qwen_segment_join_gap_seconds=item.qwen_segment_join_gap_seconds,
                    scan_audio=item.scan_audio,
                    audio_model=item.audio_model,
                    audio_filter=item.audio_filter,
                    audio_selection=(
                        item.requested_audio_selection.selection()
                        if item.requested_audio_selection
                        else None
                    ),
                    analyzed_audio_timing_callback=lambda selection, offset: gate.run(
                        db,
                        record_analyzed_audio_selection,
                        item.id,
                        AudioSelection.from_selection(selection),
                        playback_offset_seconds=offset,
                    ),
                    scan_video=item.scan_video,
                    video_model=item.video_model,
                    video_id=item.id,
                    video_duration_seconds=item.duration_seconds,
                    status_updater=update_status,
                    content_sha256=digest,
                    findings_writer=lambda session, video_id, findings: _write_findings(
                        gate, session, video_id, findings
                    ),
                    scan_metadata_updater=lambda session, *args: gate.run(
                        session, update_scan_duration, *args
                    ),
                    completed_stages=checkpoints,
                    checkpoint_callback=lambda stage: gate.run(
                        db, mark_scan_checkpoint, item.id, stage, execution_id=worker_token
                    ),
                    stage_callback=partial(
                        gate.run,
                        db,
                        _record_stage,
                        item.id,
                        worker_token,
                    ),
                    should_cancel=lambda: _should_stop_scan(db, item.id, quit_stop, gate),
                    progress_callback=partial(gate.run, db, update_scan_progress, item.id),
                )
    except JobRefusedError as refused:
        db.rollback()
        fail_refused_scan_job(db, scan_id, refused.refusal.message)
        logger.info("Scan %s stopped: %s", scan_id, refused.refusal.reason.value)
    except ScanVersionChanged as exc:
        db.rollback()
        fail_refused_scan_job(db, scan_id, str(exc))
        logger.info("Scan %s stopped because its source could not be verified", scan_id)
    except FullApplicationQuitRequested:
        # Full Quit retains the queue row and checkpoints so a future launch can
        # resume it.  Cancellation is intentionally a separate user request.
        if not DatabaseMaintenance(settings.data_dir).requested():
            mark_scan_orphaned(
                db,
                scan_id,
                "Interrupted by application Quit — ready to resume",
            )
        logger.info("Scan %s paused for shutdown or maintenance", scan_id)
    except MaintenanceUnavailable:
        db.rollback()
        logger.info("Scan %s paused while database maintenance is pending", scan_id)
    except ScanCancelledError:
        update_scan_status(db, scan_id, "cancelled")
        queue_item = get_scan_queue_item_by_scan_id(db, scan_id)
        if queue_item:
            remove_scan_queue_item(db, queue_item.queue_id)
        clear_scan_worker(db, scan_id)
        logger.info("Scan %s cancelled", scan_id)
    except Exception as exc:
        logger.exception("Dedicated Editarr worker failed for %s", scan_id)
        # The worker can arrive here because a database operation failed. Clear
        # that failed transaction before trying to persist the recovery state;
        # otherwise SQLAlchemy rejects the recovery write and masks the real
        # worker error with PendingRollbackError.
        db.rollback()
        if DOWNLOADS_REFUSED_DETAIL in str(exc):
            # Retrying cannot install the model, so end the job instead of orphaning it.
            fail_refused_scan_job(db, scan_id, REFUSAL_MESSAGES[JobRefusal.CAPABILITY_DENIED])
        elif item is not None and item.scan_mode == "credits_detection":
            update_scan_status(db, scan_id, "failed", str(exc))
            queue_item = get_scan_queue_item_by_scan_id(db, scan_id)
            if queue_item:
                remove_scan_queue_item(db, queue_item.queue_id)
            clear_scan_worker(db, scan_id)
        else:
            mark_scan_orphaned(
                db,
                scan_id,
                "Orphaned scan — worker stopped; ready to resume",
                str(exc),
            )
    else:
        queue_item = get_scan_queue_item_by_scan_id(db, scan_id)
        if queue_item:
            remove_scan_queue_item(db, queue_item.queue_id)
        clear_scan_worker(db, scan_id)
        logger.info("Scan %s completed", scan_id)
    finally:
        acquisition_scope.close()
        heartbeat_stop.set()
        if heartbeat_thread:
            heartbeat_thread.join(timeout=2)
        if control_server:
            control_server.close()
        if worker_job:
            worker_job.close()
        db.close()
    return 0


def _should_stop_scan(db, scan_id: str, quit_stop: Event, gate: JobGate) -> bool:
    if quit_stop.is_set() or DatabaseMaintenance(settings.data_dir).requested():
        raise FullApplicationQuitRequested
    gate.require()
    return is_scan_cancellation_requested(db, scan_id)


def _write_findings(gate: JobGate, db, scan_id: str, findings) -> None:
    """Write a batch only while the initiator may still run the job."""
    gate.run(db, create_scan_findings_batch, scan_id, findings)


def _record_stage(db, scan_id, execution_id, stage, state, effective):
    from scanner.pipeline.provenance import stage_model_identity

    duration_seconds = effective.get("duration_seconds")
    tail_window = effective.get("tail_window_seconds")
    if stage == "credits_detection" and isinstance(duration_seconds, (int, float)):
        intervals = (
            [
                {
                    "start_seconds": max(0.0, duration_seconds - tail_window),
                    "end_seconds": duration_seconds,
                }
            ]
            if isinstance(tail_window, (int, float)) and tail_window > 0
            else None
        )
    elif stage == "credits_cache":
        intervals = None
    else:
        intervals = (
            [{"start_seconds": 0.0, "end_seconds": duration_seconds}]
            if state == "complete"
            and stage in {"visual", "audio_extract", "audio_scan"}
            and isinstance(duration_seconds, (int, float))
            and duration_seconds > 0
            and not (
                stage == "visual"
                and (
                    effective.get("coarse_scan_enabled")
                    or effective.get("cached_scene_findings_reused")
                )
            )
            else None
        )
    record_scan_stage(
        db,
        scan_id,
        stage,
        execution_id,
        state,
        effective,
        **(
            stage_model_identity(stage, effective)
            if state not in {"reused", "no_inputs"}
            else {
                "model_id": None,
                "model_revision": None,
                "model_digest": None,
                "quantization": None,
            }
        ),
        intervals=intervals if state == "complete" else None,
        sampling_seconds=(
            effective.get("coarse_scan_interval_seconds")
            if stage == "visual" and effective.get("coarse_scan_enabled")
            else effective.get("frame_sample_interval_seconds")
            if stage == "credits_detection"
            else None
        ),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one Editarr scan worker")
    parser.add_argument("--scan-id", required=True)
    parser.add_argument("--worker-token", required=True)
    args = parser.parse_args()
    raise SystemExit(run_scan_worker(args.scan_id, args.worker_token))


if __name__ == "__main__":
    main()
