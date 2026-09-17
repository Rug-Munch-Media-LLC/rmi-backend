"""Self-test — verify the whole empire from one command.

Checks (all independent, fail-soft, plain-text output):
  services   — systemd units up
  lanes      — llama.cpp + sidecar HTTP health
  pipeline   — alert queue depths (Redis)
  engines    — agent CLI availability
  resources  — disk + memory headroom

``run_selftest()`` returns a structured dict; ``format_selftest()`` renders
it (pure — unit-tested). ``/selftest`` + the refresh callback are wired in
bot.py and callbacks/_power.py.
"""

from __future__ import annotations

import asyncio
import shutil
from typing import TYPE_CHECKING, Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update

from app.core.logging import get_logger
from app.domains.telegram.adminbot.helpers import owner_only

if TYPE_CHECKING:
    from telegram.ext import ContextTypes

logger = get_logger("admin_bot.selftest")

SERVICE_UNITS: tuple[str, ...] = (
    "rugmunchbot",
    "rmiadminbot",
    "chat-bridge",
    "llm-guard",
    "hermes-gateway",
)

LANES: tuple[tuple[str, str], ...] = (
    ("rmi-backend api :8000", "http://127.0.0.1:8000/health"),
    ("snappy-2b :8010", "http://127.0.0.1:8010/health"),
    ("defiant-9b :8009", "http://127.0.0.1:8009/health"),
    ("ff711-heavy :8014", "http://127.0.0.1:8014/health"),
    ("embed :8004", "http://127.0.0.1:8004/health"),
    ("rerank :8003", "http://127.0.0.1:8003/health"),
    ("llm-guard :8016", "http://127.0.0.1:8016/health"),
    ("community-bridge :8095", "http://127.0.0.1:8095/v1/models"),
)

ALERT_QUEUES: tuple[str, ...] = ("rmi:alerts:system", "rmi:alerts:security")


async def _unit_states() -> list[tuple[str, str]]:
    """systemctl is-active for every service unit (concurrent)."""

    async def one(unit: str) -> tuple[str, str]:
        try:
            proc = await asyncio.create_subprocess_exec(
                "/usr/bin/systemctl",
                "is-active",
                unit,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
            return unit, out.decode().strip() or "unknown"
        except Exception as exc:
            return unit, f"err ({str(exc)[:40]})"

    return list(await asyncio.gather(*(one(u) for u in SERVICE_UNITS)))


async def _lane_states() -> list[tuple[str, bool, int]]:
    """HTTP health for every model lane (concurrent, 4s cap)."""
    import time

    import httpx

    async def one(client: httpx.AsyncClient, label: str, url: str) -> tuple[str, bool, int]:
        start = time.monotonic()
        try:
            resp = await client.get(url)
            return label, resp.status_code == 200, int((time.monotonic() - start) * 1000)
        except Exception:
            return label, False, -1

    async with httpx.AsyncClient(timeout=httpx.Timeout(6.0, connect=2.0)) as client:
        return list(await asyncio.gather(*(one(client, label, url) for label, url in LANES)))


async def _queue_depths() -> list[tuple[str, int]]:
    """Pending alert counts (-1 when Redis is unreachable)."""
    import os

    try:
        import redis.asyncio as aioredis

        from app.domains.telegram.adminbot.redis_listener import (
            _build_redis_url,
            _resolve_redis_password,
        )

        url = _build_redis_url(
            os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
            _resolve_redis_password(),
        )
        client = aioredis.from_url(url, decode_responses=True, protocol=2)
        try:
            return [(q, int(await client.llen(q))) for q in ALERT_QUEUES]
        finally:
            await client.aclose()
    except Exception as exc:
        logger.debug("queue depth check failed: %s", exc)
        return [(q, -1) for q in ALERT_QUEUES]


def _resources() -> dict[str, Any]:
    """Disk + memory headroom."""
    disk = shutil.disk_usage("/")
    mem_avail_gb = None
    try:
        with open("/proc/meminfo") as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    mem_avail_gb = round(int(line.split()[1]) / 1048576, 1)
                    break
    except Exception as exc:
        logger.debug("meminfo read failed: %s", exc)
    return {
        "disk_used_pct": round(disk.used / disk.total * 100),
        "disk_free_gb": round(disk.free / 1073741824, 1),
        "mem_avail_gb": mem_avail_gb,
    }


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


def format_selftest(results: dict[str, Any]) -> str:
    """Render the self-test card (pure — unit tested)."""
    lines: list[str] = ["🩺 SELF-TEST — talos", ""]
    issues = 0

    lines.append("Services")
    for unit, state in results.get("units", []):
        ok = state == "active"
        issues += 0 if ok else 1
        lines.append(f"{'✅' if ok else '❌'} {unit} — {state}")

    lines.append("")
    lines.append("Model lanes")
    for label, ok, ms in results.get("lanes", []):
        parked = "ff711" in label and not ok
        mark = "✅" if ok else ("💤" if parked else "❌")
        issues += 0 if (ok or parked) else 1
        suffix = f" — {ms}ms" if ok else (" — parked" if parked else " — down")
        lines.append(f"{mark} {label}{suffix}")

    lines.append("")
    lines.append("Alert pipeline")
    for queue, depth in results.get("queues", []):
        if depth < 0:
            issues += 1
            lines.append(f"❌ {queue} — redis unreachable")
        elif depth > 25:
            issues += 1
            lines.append(f"⚠️ {queue} — {depth} pending (consumer stalled?)")
        else:
            lines.append(f"✅ {queue} — {depth} pending")

    engine_ids = results.get("engines", [])
    lines.append("")
    lines.append(f"Engines available: {len(engine_ids)}/{results.get('engines_total', '?')} — " + ", ".join(engine_ids))

    res = results.get("resources", {})
    lines.append("")
    lines.append(
        f"Resources: disk {res.get('disk_used_pct')}% used ({res.get('disk_free_gb')}G free) · "
        f"mem {res.get('mem_avail_gb')}G avail"
    )

    lines.append("")
    if issues == 0:
        lines.append("Verdict: ✅ ALL SYSTEMS GO")
    else:
        lines.append(f"Verdict: ⚠️ {issues} issue(s) — see ❌ above")
    return "\n".join(lines)


def selftest_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🔄 Re-run", callback_data="selftest_run"),
                InlineKeyboardButton("⬅️ Main Menu", callback_data="main"),
            ]
        ]
    )


@owner_only
async def cmd_selftest(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Run the whole-empire self-test."""
    if update.message is None:
        return
    msg = await update.message.reply_text("🩺 Running self-test…")
    results = await run_selftest()
    await msg.edit_text(format_selftest(results), reply_markup=selftest_keyboard())
