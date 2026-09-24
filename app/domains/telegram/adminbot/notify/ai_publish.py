"""Publish the outcome of an AI task the owner dispatched."""

from __future__ import annotations

from .payload import build
from .queues import SYSTEM, push

MAX_DETAIL = 800


async def publish(task: str, ok: bool, detail: str = "") -> None:
    """Publish a completion or failure notice for an AI task.

    The detail is truncated before queueing: an unbounded agent response would
    otherwise become a chat message the owner cannot read.
    """
    head = "completed" if ok else "failed"
    body = detail.strip()
    await push(
        SYSTEM,
        build(
            "AITask",
            "info" if ok else "warning",
            f"AI task {head}: {task}",
            body[:MAX_DETAIL],
        ),
    )
