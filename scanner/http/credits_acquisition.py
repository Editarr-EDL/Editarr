"""Acquire an optional credits model for its durable initiating account."""

import logging
from threading import current_thread
from time import monotonic

from scanner import api as root
from scanner.model_acquisition import AcquisitionCancelled, AcquisitionError
from scanner.modeling.model_acquisition_authority import scoped_job_acquisition
from scanner.persistence.repository.repository_queue import (
    recover_interrupted_credits_model_acquisitions,
)
from scanner.persistence.repository.scan_jobs import fail_refused_scan_job
from scanner.repository import (
    get_scan,
    get_scan_queue_item_by_scan_id,
    is_scan_cancellation_requested,
    remove_scan_queue_item,
    update_credits_detection_configuration,
    update_scan_progress,
    update_scan_status,
)
from scanner.security.scan_jobs.authority import JobGate, JobRefusedError

logger = logging.getLogger(__name__)


def run_credits_model_acquisition(gate: JobGate, qwen_model: str) -> None:
    """Recheck the initiator before acquisition, progress, publication, and requeue."""
    factory, scan_id = gate.factory, gate.scan_id
    service = root.get_acquisition_service()
    last_progress: float | None = None

    def cancelled() -> bool:
        gate.require(download=True)
        with factory() as db:
            return is_scan_cancellation_requested(db, scan_id)

    def progress(downloaded: int, total: int) -> None:
        nonlocal last_progress
        now = monotonic()
        if last_progress is not None and now - last_progress < 0.25 and downloaded < total:
            return
        percent = 5.0 if total <= 0 else min(85.0, 5.0 + downloaded / total * 80.0)
        with factory() as db:
            gate.run(
                db,
                update_scan_progress,
                scan_id,
                {
                    "percent": percent,
                    "stage": "acquiring_model",
                    "message": "Acquiring optional Qwen model",
                },
                download=True,
            )
            last_progress = now

    def requeue(db) -> bool:
        scan = get_scan(db, scan_id)
        if scan is None or scan.credits_detection is None:
            return False
        if is_scan_cancellation_requested(db, scan_id):
            raise AcquisitionCancelled(qwen_model)
        update_credits_detection_configuration(
            db,
            scan_id,
            scan.credits_detection.configuration,
            status="queued",
            progress_message="Queued",
        )
        return True

    with factory() as db:
        try:
            gate.require(download=True)
            with scoped_job_acquisition(gate):
                service.acquire(qwen_model, progress=progress, cancelled=cancelled)
            if gate.run(db, requeue, download=True):
                root._start_scan_queue_worker_if_needed()
        except JobRefusedError as refused:
            db.rollback()
            fail_refused_scan_job(db, scan_id, refused.refusal.message)
        except AcquisitionCancelled:
            db.rollback()
            update_scan_status(db, scan_id, "cancelled")
            queue = get_scan_queue_item_by_scan_id(db, scan_id)
            if queue is not None:
                remove_scan_queue_item(db, queue.queue_id)
        except (AcquisitionError, OSError, ValueError) as exc:
            db.rollback()
            logger.warning("Optional Qwen acquisition failed for scan %s: %s", scan_id, exc)
            update_scan_status(db, scan_id, "failed", "Optional model acquisition failed")
            queue = get_scan_queue_item_by_scan_id(db, scan_id)
            if queue is not None:
                remove_scan_queue_item(db, queue.queue_id)
        except Exception:
            db.rollback()
            logger.exception("Optional Qwen acquisition stopped unexpectedly for scan %s", scan_id)
            # Publish the retryable state and retire this thread together, so a consent
            # retry is either refused or finds no live task and starts a replacement.
            with root._model_task_lock():
                recover_interrupted_credits_model_acquisitions(db, scan_id)
                _retire_current_task(scan_id)
        finally:
            with root._model_task_lock():
                _retire_current_task(scan_id)


def _retire_current_task(scan_id: str) -> None:
    """Drop this thread's registration without removing a replacement started later."""
    key = f"credits:{scan_id}"
    if root._model_tasks().get(key) is current_thread():
        root._model_tasks().pop(key, None)
