"""Dedicated scan worker launch and persisted queue scheduler."""

from __future__ import annotations

from datetime import UTC, datetime
from subprocess import Popen

from sqlalchemy.orm import Session

from scanner.runtime.process_control import WorkerIdentity

from . import background


def _worker_identity(
    scan_id: str, pid: int | None, pgid: int | None, token: str | None
) -> WorkerIdentity | None:
    """Return a live identity only after token-authenticated IPC proof."""
    from scanner import api as root

    return root.PROCESS_CONTROL.identity(scan_id, pid, pgid, token)


def _terminate_scan_worker(
    scan_id: str, pid: int | None, pgid: int | None, token: str | None
) -> bool:
    """Stop only a worker whose live identity matches the persisted token."""
    from scanner import api as root

    identity = root._worker_identity(scan_id, pid, pgid, token)
    return bool(identity and root.PROCESS_CONTROL.force_stop(identity))


def _cleanup_settled_scan_attempt(scan_id: str, token: str | None, status: str) -> None:
    """Retire a stopped worker endpoint and discard checkpoints after cancellation."""
    from scanner import api as root
    from scanner.pipeline.visual_checkpoints import discard_visual_checkpoints

    root.cleanup_worker_control_endpoint(scan_id, token)
    if status == "cancelled":
        discard_visual_checkpoints(root.settings.data_dir, scan_id)


def _recover_failed_scan_launch(
    db: Session,
    scan_id: str,
    error: str,
    *,
    token: str,
    process: Popen[bytes] | None,
    identity: WorkerIdentity | None,
) -> None:
    """Drain a failed launch before releasing its worker reservation."""
    from scanner import api as root
    from scanner.persistence.repository.repository_queue import settle_stopped_scan_worker
    from scanner.persistence.repository.scan_worker_registration import (
        record_unconfirmed_scan_worker_process,
    )

    db.rollback()
    if identity is not None:
        # Registration itself can fail after authentication. Retain the proven PID
        # in place of our provisional launcher, unless another PID was registered.
        record_unconfirmed_scan_worker_process(
            db, scan_id, token=token, pid=identity.pid, pgid=identity.process_group,
            replace_launcher_pid=process.pid if process else None,
        )
        root._stop_discovered_worker(identity)
    elif process is not None:
        record_unconfirmed_scan_worker_process(
            db, scan_id, token=token, pid=process.pid,
            pgid=process.pid if root.os.name == "posix" else None,
        )

    queue_item = root.get_scan_queue_item_by_scan_id(db, scan_id)
    worker = root.scan_worker_process(db, queue_item.queue_id) if queue_item else None
    if worker is None or worker[2] != token:
        return
    pid, group, _, _status = worker
    if identity is None:
        # The interpreter can register itself just as discovery times out.
        registered = root._worker_identity(scan_id, pid, group, token)
        if registered is not None:
            root._stop_discovered_worker(registered)
        elif process is not None and pid == process.pid and process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass
            if process.poll() is None:
                return
    if root.PROCESS_CONTROL.attempt_may_be_running(
        scan_id, pid, group, token, data_dir=root.settings.data_dir
    ):
        return
    settled = settle_stopped_scan_worker(
        db, scan_id, token=token, expected_process=(pid, group), launch_error=error
    )
    if settled:
        _cleanup_settled_scan_attempt(scan_id, token, settled)


def _stop_discovered_worker(identity: WorkerIdentity) -> None:
    """Settle a proven worker without ever targeting its transient launcher PID."""
    from scanner import api as root

    try:
        stopped = root.PROCESS_CONTROL.request_stop(identity)
        if stopped and root.PROCESS_CONTROL.wait_for_exit(identity, root.GRACEFUL_STOP_TIMEOUT):
            return
        root.PROCESS_CONTROL.force_stop(identity)
    except Exception:
        root.logger.exception("Could not settle dedicated Editarr worker for %s", identity.scan_id)


def _admit_scan_job(scan_id: str) -> bool:
    """Decide a queued job for its initiator now; a refused job ends and leaves the queue."""
    from scanner import api as root
    from scanner.persistence.repository.scan_jobs import fail_refused_scan_job
    from scanner.security.scan_jobs.authority import JobRefused, decide_job

    db = root.get_session_factory()()
    try:
        verdict = decide_job(db, scan_id, now=datetime.now(UTC))
        if isinstance(verdict, JobRefused):
            fail_refused_scan_job(db, scan_id, verdict.message)
            root.logger.info("Scan %s refused before launch: %s", scan_id, verdict.reason.value)
            return False
        return verdict is not None
    except Exception:
        root.logger.exception("Could not authorize scan %s before launch", scan_id)
        root.SCAN_QUEUE_STOP_EVENT.wait(1)
        return False
    finally:
        db.close()


def _launch_scan_worker(scan_id: str) -> bool:
    """Reserve a queued scan and start it through the ownership adapter."""
    from scanner import api as root
    from scanner.persistence.repository.scan_worker_registration import (
        record_unconfirmed_scan_worker_process,
    )

    if (
        root.SCAN_QUEUE_STOP_EVENT.is_set()
        or (root.settings.data_dir / "restore-reconciliation.json").exists()
    ):
        return False
    if not root._admit_scan_job(scan_id):
        return False
    token = str(root.uuid.uuid4())
    factory = root.get_session_factory()
    db = factory()
    process = None
    identity = None
    try:
        if not root.reserve_scan_worker(db, scan_id, token=token):
            root.logger.debug("Scan worker reservation was unavailable for %s", scan_id)
            return False
        root.logger.info("Launching dedicated scan worker for %s", scan_id)
        environment = root.os.environ.copy()
        # A child must use the controller's resolved configuration, including a
        # public --data-dir override that is otherwise absent from its argv.
        environment["EDITARR_DATA_DIR"] = str(root.settings.data_dir)
        environment["EDITARR_DATABASE_URL"] = str(root.settings.database_url)
        process = root.PROCESS_CONTROL.launch_worker(
            root.scan_worker_command(scan_id, token), scan_id, token, environment=environment
        )
        process_group = process.pid if root.os.name == "posix" else None
        record_unconfirmed_scan_worker_process(
            db, scan_id, token=token, pid=process.pid, pgid=process_group,
        )
        identity = root.PROCESS_CONTROL.discover_identity(
            scan_id,
            token,
            launcher_pid=process.pid,
            process_group=process_group,
            launcher_process=process,
        )
        if identity is None:
            raise RuntimeError("Worker did not establish a token-authenticated identity")
        root.PROCESS_CONTROL.ensure_worker_job(identity)
        if not root.record_scan_worker_process(
            db,
            scan_id,
            token=token,
            pid=identity.pid,
            pgid=identity.process_group,
        ):
            raise RuntimeError("Worker process registration failed")
        root.logger.info("Dedicated scan worker for %s is ready (pid %s)", scan_id, identity.pid)
        return True
    except Exception as exc:
        root.logger.exception("Could not launch dedicated Editarr worker for %s", scan_id)
        root._recover_failed_scan_launch(
            db, scan_id, str(exc), token=token, process=process, identity=identity
        )
        return False
    finally:
        db.close()


def _scan_queue_worker() -> None:
    """Poll the persisted queue and launch available scans until asked to stop."""
    from scanner import api as root
    from scanner.persistence.repository.repository_queue import settle_stopped_scan_worker

    try:
        while not root.SCAN_QUEUE_STOP_EVENT.is_set():
            if (root.settings.data_dir / "restore-reconciliation.json").exists():
                root.SCAN_QUEUE_STOP_EVENT.wait(1)
                continue
            factory = root.get_session_factory()
            db = factory()
            try:
                active_item = root.get_active_scan_queue_item(db)
                next_item = root.get_next_scan_queue(db)
                if active_item and active_item.status in {"scanning", "cancelling", "pausing"}:
                    worker = root.scan_worker_process(db, active_item.queue_id)
                    identity = (
                        root._worker_identity(active_item.id, *worker[:3]) if worker else None
                    )
                    worker_is_alive = bool(identity) or bool(
                        worker and root.PROCESS_CONTROL.attempt_may_be_running(
                            active_item.id, *worker[:3], data_dir=root.settings.data_dir
                        )
                    )
                    if active_item.status == "pausing" and identity:
                        root.PROCESS_CONTROL.request_stop(identity)
                    if not worker_is_alive:
                        settled = settle_stopped_scan_worker(
                            db,
                            active_item.id,
                            token=worker[2] if worker else None,
                            expected_process=worker[:2] if worker else (None, None),
                        )
                        if settled:
                            _cleanup_settled_scan_attempt(
                                active_item.id, worker[2] if worker else None, settled
                            )
                        active_item = root.get_active_scan_queue_item(db)
                        next_item = root.get_next_scan_queue(db)
            except Exception:
                root.logger.exception("Scan queue scheduler could not read the queue")
                if root.SCAN_QUEUE_STOP_EVENT.wait(1):
                    break
                continue
            finally:
                db.close()
            if active_item:
                root.SCAN_QUEUE_STOP_EVENT.wait(1)
                continue
            if not next_item:
                # The event is only an optimization: periodically recheck the
                # database so work committed by another API process, or after a
                # missed in-memory notification, cannot remain queued forever.
                root.SCAN_QUEUE_WAKE_EVENT.wait(timeout=1)
                root.SCAN_QUEUE_WAKE_EVENT.clear()
                continue
            if root.SCAN_QUEUE_STOP_EVENT.is_set():
                break
            try:
                root._launch_scan_worker(next_item.id)
            except Exception:
                # Launch recovery itself can fail (for example during maintenance);
                # keep the scheduler alive so the queue is retried.
                root.logger.exception("Scan queue scheduler could not launch %s", next_item.id)
                if root.SCAN_QUEUE_STOP_EVENT.wait(1):
                    break
    finally:
        with root.SCAN_QUEUE_WORKER_LOCK:
            root.SCAN_QUEUE_WORKER_RUNNING = False
            root.SCAN_QUEUE_WORKER_THREAD = None


def _start_scan_queue_worker_if_needed() -> None:
    """Start one queue scheduler thread when no live scheduler is running."""
    from scanner import api as root

    with root.SCAN_QUEUE_WORKER_LOCK:
        if root.SCAN_QUEUE_STOP_EVENT.is_set():
            return
        root.SCAN_QUEUE_WAKE_EVENT.set()
        if root.SCAN_QUEUE_WORKER_THREAD and root.SCAN_QUEUE_WORKER_THREAD.is_alive():
            root.SCAN_QUEUE_WORKER_RUNNING = True
            return
        root.SCAN_QUEUE_WORKER_RUNNING = True
        try:
            # Start while holding the lock so another enqueue cannot mistake a
            # newly-created, not-yet-started thread for a dead scheduler.
            root.SCAN_QUEUE_WORKER_THREAD = background.start_thread(
                "editarr-scan-queue", background.SystemWork.SCAN_DISPATCH, root._scan_queue_worker
            )
            root.logger.info("Scan queue scheduler started")
        except Exception:
            root.SCAN_QUEUE_WORKER_RUNNING = False
            root.SCAN_QUEUE_WORKER_THREAD = None
            raise
