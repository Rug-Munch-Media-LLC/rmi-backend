"""Gather every self-test probe concurrently."""

from __future__ import annotations

import asyncio
from typing import Any

from .lanes import _lane_states
from .queues import _queue_depths
from .resources import _resources
from .units import _unit_states


async def run_selftest() -> dict[str, Any]:
    """Gather all checks concurrently. Never raises."""
    from app.domains.telegram.adminbot import engines

    units, lanes, queues = await asyncio.gather(_unit_states(), _lane_states(), _queue_depths())
    return {
        "units": units,
        "lanes": lanes,
        "queues": queues,
        "engines": engines.available_ids(),
        "engines_total": len(engines.ENGINES),
        "resources": _resources(),
    }
