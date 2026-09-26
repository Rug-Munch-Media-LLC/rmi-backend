"""Active/inactive state for each watched systemd unit."""

from __future__ import annotations

import asyncio

from .constants import SERVICE_UNITS


async def _unit_states() -> list[tuple[str, str]]:
    """Return (unit, state) for every watched service."""
    async def one(unit: str) -> tuple[str, str]:
        proc = await asyncio.create_subprocess_exec(
            "systemctl", "is-active", unit,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            out = await proc.communicate()
            return unit, out[0].decode().strip() or "unknown"
        except Exception as exc:
            return unit, f"err ({str(exc)[:40]})"

    return list(await asyncio.gather(*(one(u) for u in SERVICE_UNITS)))
