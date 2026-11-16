"""Dedicated scan worker launch and persisted queue scheduler."""

from __future__ import annotations

from datetime import UTC, datetime

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
    if identity is None:
        root.cleanup_worker_control_endpoint(scan_id, token)
    return bool(identity and root.PROCESS_CONTROL.force_stop(identity))


def _recover_failed_scan_launch(db: Session, scan_id: str, error: str) -> None:
    """Settle a reservation when no dedicated worker survived the launch."""
    from scanner import api as root

    db.expire_all()
    if root.is_scan_cancellation_requested(db, scan_id):
        root.update_scan_status(db, scan_id, "cancelled")
        queue_item = root.get_scan_queue_item_by_scan_id(db, scan_id)
        if queue_item:
            root.remove_scan_queue_item(db, queue_item.queue_id)
        return
    queue_item = root.get_scan_queue_item_by_scan_id(db, scan_id)
    if queue_item and queue_item.status == "orphaned" and queue_item.error_message:
        # The worker can fail and close its endpoint before discovery finishes.
        # Keep its concrete pipeline/startup error instead of replacing it with
        # the controller's less informative observation that proof was unavailable.
        return
    root.mark_scan_orphaned(
        db,
        scan_id,
        "Orphaned scan — worker could not start; ready to resume",
        error,
    )


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
        identity = root.PROCESS_CONTROL.discover_identity(
            scan_id,
            token,
            launcher_pid=process.pid,
            process_group=process.pid if root.os.name == "posix" else None,
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
            root._stop_discovered_worker(identity)
            root._recover_failed_scan_launch(db, scan_id, "Worker process registration failed")
            return False
        root.logger.info("Dedicated scan worker for %s is ready (pid %s)", scan_id, identity.pid)
        return True
    except Exception as exc:
        if identity is not None:
            root._stop_discovered_worker(identity)
        elif process is not None:
            try:
                process.terminate()
            except OSError:
                pass
        root.logger.exception("Could not launch dedicated Editarr worker for %s", scan_id)
        root._recover_failed_scan_launch(db, scan_id, str(exc))
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
                if active_item and active_item.status in {"scanning", "cancelling"}:
                    worker = root.scan_worker_process(db, active_item.queue_id)
                    worker_is_alive = bool(
                        worker
                        and root._worker_identity(active_item.id, worker[0], worker[1], worker[2])
                    )
                    if not worker_is_alive:
                        if worker:
                            root.cleanup_worker_control_endpoint(active_item.id, worker[2])
                        settle_stopped_scan_worker(
                            db, active_item.id, token=worker[2] if worker else None
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
            root._launch_scan_worker(next_item.id)
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
