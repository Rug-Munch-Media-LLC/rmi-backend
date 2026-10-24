"""Cancellation-safe bridge from async code to the sync thread pool.

``loop.run_in_executor`` futures cannot be cancelled. When the awaiting
coroutine is cancelled (typically by ``asyncio.wait_for``), the worker thread
keeps running and its eventual exception is never retrieved, which asyncio
reports as ``Future exception was never retrieved`` (observed 50x/day in the
scan worker). This module retrieves that exception so cancellation stays quiet
while the thread finishes its work in the background.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
from typing import Any


def _consume(fut: asyncio.Future) -> None:
    """Retrieve a finished future's result so asyncio never warns about it."""
    if fut.cancelled():
        return
    with contextlib.suppress(BaseException):
        fut.exception()


async def run_in_thread(fn: Any, *args: Any, **kwargs: Any) -> Any:
    """Run ``fn`` in the default executor and await it safely.

    The returned future is shielded: cancelling the caller does not cancel the
    worker thread, and its late exception is consumed rather than leaked.
    """
    loop = asyncio.get_running_loop()
    fut = loop.run_in_executor(None, functools.partial(fn, *args, **kwargs))
    fut.add_done_callback(_consume)
    return await asyncio.shield(fut)
