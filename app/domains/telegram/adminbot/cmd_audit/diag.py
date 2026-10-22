"""The /diag command — run every read-only handler in-process."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from .format import format_diag
from .probe import probe_pair
from .targets import SPECS, resolve

TOTAL_BUDGET_S = 50.0
PER_COMMAND_S = 8.0


async def _run_one(name: str, args: tuple[str, ...], budget: float) -> tuple[str, str, int, str]:
    """Run one handler under a timeout; return (name, status, ms, detail)."""
    sink: list[str] = []
    update, ctx = probe_pair(sink, list(args))
    limit = max(min(PER_COMMAND_S, budget), 0.1)
    started = time.perf_counter()
    try:
        handler = resolve(name)
        await asyncio.wait_for(handler(update, ctx), timeout=limit)
        status = "PASS"
        detail = ""
    except TimeoutError:
        status, detail = "TIMEOUT", f">{limit:.0f}s"
    except Exception as exc:
        status, detail = "FAIL", f"{type(exc).__name__}: {str(exc)[:90]}"
    return name, status, int((time.perf_counter() - started) * 1000), detail


async def cmd_diag(update: Any, ctx: Any) -> None:
    """Probe every read-only command and reply with a PASS/FAIL table."""
    if update.message is None:
        return
    msg = await update.message.reply_text("🩺 Running command diagnostics…")
    started = time.monotonic()
    rows: list[tuple[str, str, int, str]] = []
    for name, args in SPECS:
        remaining = TOTAL_BUDGET_S - (time.monotonic() - started)
        if remaining <= 0.2:
            rows.append((name, "SKIP", 0, "budget spent"))
            continue
        rows.append(await _run_one(name, args, remaining))
    await msg.edit_text(format_diag(rows, time.monotonic() - started))
