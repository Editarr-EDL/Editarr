"""Live initiator authority for model acquisition.

Acquisition runs for the gate bound to it: a scan's `JobGate` in workers and
their children, or a request's `OperationGate` in the server. Unbound
acquisition runs as the owner who started a CLI or worker process; inside
`request_server` it is refused, so work a request started can never download
or install without its initiator.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache
from typing import TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    from scanner.security.scan_jobs.authority import JobGate
    from scanner.security.scan_jobs.operation_gate import OperationGate

    Gate = JobGate | OperationGate

_DOWNLOADS = "EDITARR_WORKER_MODEL_DOWNLOADS"
_SCAN_ID = "EDITARR_WORKER_SCAN_ID"
_JOB: ContextVar[Gate | None] = ContextVar("model_acquisition_job", default=None)
DOWNLOADS_REFUSED_DETAIL = "the account that started this scan may not download models"
UNBOUND_DETAIL = "model acquisition in the server needs the account that started it"
_serving_requests = False
ResultT = TypeVar("ResultT")


def refuse_model_downloads() -> None:
    """Keep legacy worker callers on installed models only."""
    os.environ[_DOWNLOADS] = "refused"


def _downloads_refused() -> bool:
    return os.environ.get(_DOWNLOADS) == "refused"


@contextmanager
def request_server() -> Iterator[None]:
    """Refuse unbound acquisition in this process while it serves HTTP requests."""
    global _serving_requests
    previous, _serving_requests = _serving_requests, True
    try:
        yield
    finally:
        _serving_requests = previous


@contextmanager
def scoped_job_acquisition(gate: Gate, *, children: bool = False) -> Iterator[None]:
    """Bind acquisition to one job without changing concurrent API threads."""
    token = _JOB.set(gate)
    previous = os.environ.get(_SCAN_ID)
    if children:
        os.environ[_SCAN_ID] = gate.scan_id
    try:
        yield
    finally:
        _JOB.reset(token)
        if children:
            if previous is None:
                os.environ.pop(_SCAN_ID, None)
            else:
                os.environ[_SCAN_ID] = previous


def _job() -> Gate | None:
    gate = _JOB.get()
    if gate is not None:
        return gate
    scan_id = os.environ.get(_SCAN_ID)
    if scan_id is None:
        return None
    from scanner.db import get_session_factory

    return _inherited_job(get_session_factory(), scan_id)


@lru_cache(maxsize=1)
def _inherited_job(factory, scan_id):
    from scanner.security.scan_jobs.authority import JobGate

    return JobGate(factory, scan_id)


def require_acquisition(*, download: bool = False) -> None:
    """Use live job authority, including download permission only for missing models."""
    from .model_acquisition_manifest import AcquisitionError

    gate = _job()
    if gate is None and _serving_requests:
        raise AcquisitionError("download_not_permitted", UNBOUND_DETAIL)
    allowed = gate.require() if gate is not None else None
    refused = not allowed.may_download_models if allowed is not None else _downloads_refused()
    if download and refused:
        raise AcquisitionError("download_not_permitted", DOWNLOADS_REFUSED_DETAIL)


def publish_acquisition(operation: Callable[[], ResultT]) -> ResultT:
    """Publish installed files under the same lock that decides live authority."""
    gate = _job()
    if gate is None:
        require_acquisition(download=True)
        return operation()
    with gate.factory() as db:
        return gate.run(db, lambda _session: operation(), download=True)
