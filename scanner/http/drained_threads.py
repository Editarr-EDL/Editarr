"""Run blocking work in the threadpool without abandoning it on cancellation."""

import asyncio

from starlette.concurrency import run_in_threadpool


async def drained_thread(function, *args, **kwargs):
    """Do not close the caller's Session while a cancelled worker is still using it."""
    work = asyncio.create_task(run_in_threadpool(function, *args, **kwargs))
    try:
        return await asyncio.shield(work)
    except asyncio.CancelledError:
        await asyncio.gather(work, return_exceptions=True)
        raise
