"""Redis-backed Background Task Queue - retry with exponential backoff, visibility."""

from __future__ import annotations

import asyncio
import json
import os
import time
from typing import TYPE_CHECKING, Any

from app.core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Callable

logger = get_logger(__name__)
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/2")

TASKS = {}


def register_task(name: str, fn: Callable) -> None:
    """Register a background task handler."""
    TASKS[name] = fn


async def enqueue(name: str, payload: dict[str, Any], delay: int = 0, max_retries: int = 3) -> None:
    """Enqueue a background task."""
    import redis

    try:
        r = redis.from_url(REDIS_URL)
        task = json.dumps(
            {
                "name": name,
                "payload": payload,
                "retries": 0,
                "max_retries": max_retries,
                "created_at": time.time(),
            }
        )
        if delay:
            r.zadd("rmi:task_queue:delayed", {task: time.time() + delay})
        else:
            r.lpush("rmi:task_queue", task)
        logger.debug(f"Task enqueued: {name}")
    except Exception as e:
        logger.warning(f"Task enqueue failed: {e}")


async def process_tasks() -> None:
    """Process tasks from the queue. Run as background loop."""
    import redis

    try:
        r = redis.from_url(REDIS_URL, decode_responses=True)
    except (ConnectionError, OSError):
        return

    while True:
        try:
            # Pop from queue
            task_data = r.brpop("rmi:task_queue", timeout=5)
            if not task_data:
                continue

            task = json.loads(task_data[1])
            name = task["name"]
            payload = task["payload"]
            retries = task["retries"]
            max_retries = task["max_retries"]

            handler = TASKS.get(name)
            if not handler:
                logger.warning(f"No handler for task: {name}")
                continue

            try:
                if asyncio.iscoroutinefunction(handler):
                    await handler(payload)
                else:
                    handler(payload)
                logger.debug(f"Task completed: {name}")
            except Exception as e:
                retries += 1
                if retries <= max_retries:
                    delay = 2**retries  # exponential backoff: 2, 4, 8 seconds
                    task["retries"] = retries
                    r.zadd("rmi:task_queue:delayed", {json.dumps(task): time.time() + delay})
                    logger.warning(f"Task {name} failed (attempt {retries}/{max_retries}), retrying in {delay}s")
                else:
                    # Dead letter queue
                    r.lpush(
                        "rmi:task_queue:dead",
                        json.dumps({"task": task, "error": str(e), "failed_at": time.time()}),
                    )
                    logger.error(f"Task {name} permanently failed after {max_retries} retries")
        except Exception as e:
            logger.error(f"Task processor error: {e}")
            await asyncio.sleep(1)


async def get_queue_stats() -> dict[str, Any]:
    """Get task queue statistics."""
    import redis

    try:
        r = redis.from_url(REDIS_URL)
        return {
            "pending": r.llen("rmi:task_queue"),
            "delayed": r.zcard("rmi:task_queue:delayed"),
            "dead": r.llen("rmi:task_queue:dead"),
        }
    except (ConnectionError, OSError):
        return {"error": "redis unavailable"}
