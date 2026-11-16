"""The one way an HTTP handler starts work that outlives its request.

Every submission names its authority. A request's `OperationGate` or a scan's
`JobGate` carries the initiator: the work runs with that gate bound, so model
acquisition rechecks it, and the work itself rechecks it before execution and
before each consequential write. `SystemWork` names the few submissions no
user's authority governs and why. `tests/test_background_submissions.py`
refuses any other way to start background work under `scanner/http`.
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import nullcontext
from enum import StrEnum
from threading import Thread

from scanner.modeling.model_acquisition_authority import scoped_job_acquisition
from scanner.security.scan_jobs.authority import JobGate
from scanner.security.scan_jobs.operation_gate import OperationGate


class SystemWork(StrEnum):
    SCAN_DISPATCH = "scan_dispatch"
    """Launches queued scans; each job's `JobGate` decides its own initiator."""

    SETUP_RESTORE = "setup_restore"
    """No account exists yet; restore activation rechecks the empty installation."""


Authority = JobGate | OperationGate | SystemWork


def _bound(authority: Authority, work: Callable[[], object]) -> Callable[[], object]:
    if not isinstance(authority, JobGate | OperationGate | SystemWork):
        raise TypeError(f"background work needs an initiator gate or SystemWork, not {authority!r}")

    def run():
        scope = (
            nullcontext()
            if isinstance(authority, SystemWork)
            else scoped_job_acquisition(authority)
        )
        with scope:
            return work()

    return run


def start_thread(name: str, authority: Authority, work: Callable[[], object]) -> Thread:
    """Start one daemon thread running `work` under `authority`."""

    thread = Thread(target=_bound(authority, work), name=name, daemon=True)
    thread.start()
    return thread


def submit(pool: ThreadPoolExecutor, authority: Authority, work: Callable[[], object]) -> Future:
    """Queue `work` on `pool` under `authority`."""

    return pool.submit(_bound(authority, work))
