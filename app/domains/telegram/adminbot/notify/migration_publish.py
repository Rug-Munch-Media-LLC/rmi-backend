"""Publish the 8B migration completion event exactly once."""

from __future__ import annotations

import asyncio

from .migration_counts import counts
from .migration_marker import write_marker
from .migration_marker_check import already_announced
from .payload import build
from .queues import SYSTEM, push


async def publish_if_complete() -> bool:
    """Publish once when every source collection has a full 8B copy.

    The marker file makes this idempotent, so a restart cannot re-announce a
    migration that already completed. The marker write is moved off the event
    loop because it is blocking file I/O.
    """
    migrated, total = counts()
    if not total or migrated < total or already_announced():
        return False
    await push(
        SYSTEM,
        build("Migration", "info", f"✅ 8B migration complete: {migrated:,} vectors at 4096-dim"),
    )
    await asyncio.to_thread(write_marker, str(migrated))
    return True
