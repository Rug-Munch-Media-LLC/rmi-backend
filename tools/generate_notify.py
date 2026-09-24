"""Generate notification producers for health, AI tasks, and migration.

Each module holds one function and stays under 50 lines. Producers push
Alertmanager-shaped payloads onto the existing alerts queue, so the current
dedupe window and delivery path remain the single route to Telegram.

Payload contract (from alerts_feed.format_alert):
    {"status": "firing", "labels": {"alertname": ..., "severity": ...},
     "annotations": {"summary": ...}, "startsAt": iso8601}

Run from the repo root:  python3 tools/generate_notify.py
"""

from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
PKG = ROOT / "app" / "domains" / "telegram" / "adminbot" / "notify"
BUDGET = 50

FILES: dict[str, str] = {}

FILES["__init__.py"] = '''"""Notification producers: health, AI task results, migration events."""
'''

FILES["queues.py"] = '''"""Queue names and the push helper."""

from __future__ import annotations

import json
import os
from typing import Any

SYSTEM = "rmi:alerts:system"
SECURITY = "rmi:alerts:security"


async def push(queue: str, alert: dict[str, Any]) -> None:
    """Push one alert onto an alerts queue.

    Goes through Redis so the existing dedupe window applies; a failure here is
    the caller's to handle, because a lost alert must be visible.
    """
    import redis.asyncio as aioredis

    from app.domains.telegram.adminbot.redis_listener import _build_redis_url

    base = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    url = _build_redis_url(base, os.environ.get("REDIS_PASSWORD"))
    # RESP2: redis-py 8.x + DragonflyDB password auth otherwise fails the
    # HELLO/AUTH ordering. Same workaround the alerts listener uses.
    client = aioredis.from_url(url, decode_responses=True, protocol=2)
    try:
        await client.rpush(queue, json.dumps(alert))
    finally:
        await client.aclose()
'''

FILES["payload.py"] = '''"""Build Alertmanager-shaped alert payloads."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def build(
    name: str,
    severity: str,
    summary: str,
    description: str = "",
    status: str = "firing",
) -> dict[str, Any]:
    """Return an alert the existing formatter renders correctly.

    The formatter reads labels.alertname and labels.severity, and puts
    annotations.summary first, so the headline goes in summary.
    """
    return {
        "status": status,
        "labels": {"alertname": name, "severity": severity},
        "annotations": {"summary": summary, "description": description},
        "startsAt": datetime.now(UTC).isoformat(timespec="seconds"),
    }
'''

FILES["health_read.py"] = '''"""Read one health sample from /proc and the filesystem."""

from __future__ import annotations

import os
import shutil

from .health_mem import mem_pct


def sample() -> dict[str, float]:
    """Return smoothed load, CPU count, disk usage and memory usage.

    ``load15`` is used for alerting so a short-lived embed/API burst does not
    page the operator; ``load1`` remains available for diagnostics and
    backwards-compatible callers.
    """
    load1 = 0.0
    load15 = 0.0
    try:
        with open("/proc/loadavg") as handle:
            fields = handle.read().split()
        load1 = float(fields[0])
        load15 = float(fields[2]) if len(fields) > 2 else load1
    except (OSError, ValueError, IndexError):
        pass
    usage = shutil.disk_usage("/")
    return {
        "load1": load1,
        "load15": load15,
        "cpu_count": float(max(os.cpu_count() or 1, 1)),
        "disk_pct": usage.used / usage.total * 100 if usage.total else 0.0,
        "mem_pct": mem_pct(),
    }
'''

FILES["health_mem.py"] = '''"""Memory used as a percentage, from /proc/meminfo."""

from __future__ import annotations


def mem_pct() -> float:
    """Return memory in use as a percentage, 0.0 when unavailable."""
    values: dict[str, int] = {}
    try:
        with open("/proc/meminfo") as handle:
            for line in handle:
                if ":" not in line:
                    continue
                name, rest = line.split(":", 1)
                values[name] = int("".join(c for c in rest if c.isdigit()) or 0)
    except (OSError, ValueError):
        return 0.0
    total = values.get("MemTotal", 0)
    available = values.get("MemAvailable", 0)
    if total <= 0:
        return 0.0
    return (total - available) / total * 100
'''

FILES["metric_label.py"] = '''"""Human label for a health metric."""

from __future__ import annotations

NAMES: dict[str, str] = {
    "load1": "load (1m)",
    "load15": "load (15m)",
    "disk_pct": "disk",
    "mem_pct": "memory",
}


def label(metric: str) -> str:
    """Return the name to show for a metric."""
    return NAMES.get(metric, metric)
'''

FILES["metric_number.py"] = '''"""Format a health metric with its unit."""

from __future__ import annotations


def number(metric: str, value: float) -> str:
    """Format a value: a percentage for the pct metrics, a plain load otherwise."""
    return f"{value:.0f}%" if metric.endswith("_pct") else f"{value:.1f}"
'''

FILES["health_severity.py"] = '''"""Rate a health sample as ok, warn or critical."""

from __future__ import annotations

import os

from .metric_label import label
from .metric_number import number

# Load is normalized by CPU count. These defaults avoid paging on a busy
# 16-core host whose 15-minute load is still below sustained saturation.
LOAD_WARN_PER_CPU = float(os.getenv("HEALTH_LOAD_WARN_PER_CPU", "1.5"))
LOAD_CRITICAL_PER_CPU = float(os.getenv("HEALTH_LOAD_CRITICAL_PER_CPU", "2.0"))


def _limits(sample: dict[str, float]) -> tuple[tuple[str, float, float], ...]:
    cpu_count = max(1.0, float(sample.get("cpu_count", 1.0)))
    load_metric = "load15" if "load15" in sample else "load1"
    return (
        ("disk_pct", 80.0, 92.0),
        ("mem_pct", 85.0, 95.0),
        (
            load_metric,
            max(8.0, cpu_count * LOAD_WARN_PER_CPU),
            max(16.0, cpu_count * LOAD_CRITICAL_PER_CPU),
        ),
    )


def _reason(metric: str, value: float, threshold: float, sample: dict[str, float]) -> str:
    text = f"{label(metric)} {number(metric, value)} (>= {number(metric, threshold)})"
    if metric in {"load1", "load15"}:
        cpu_count = max(1.0, float(sample.get("cpu_count", 1.0)))
        text += f" on {cpu_count:.0f} cores"
    return text


def rate(sample: dict[str, float]) -> tuple[str, str]:
    """Return (severity, reason); critical outranks warn.

    The worst signal is reported, not whichever threshold was checked first, so
    a box that is simultaneously low on disk and busy says so.  Load uses the
    15-minute average when available to suppress short-lived CPU bursts.
    """
    reason = ""
    for metric, warn_at, critical_at in _limits(sample):
        value = sample.get(metric, sample.get("load1", 0.0) if metric.startswith("load") else 0.0)
        if value >= critical_at:
            return "critical", _reason(metric, value, critical_at, sample)
        if value >= warn_at:
            reason = _reason(metric, value, warn_at, sample)
    return ("warn", reason) if reason else ("ok", "")
'''

FILES["health_publish.py"] = '''"""Publish a health alert when a host threshold is crossed."""

from __future__ import annotations

import os
import time

from .health_read import sample
from .health_severity import rate
from .payload import build
from .queues import SYSTEM, push

REPEAT_SECONDS = int(os.getenv("HEALTH_ALERT_REPEAT_SECONDS", "14400"))
_last_severity: str | None = None
_last_sent_at = 0.0


def reset_state() -> None:
    """Reset the in-process transition state (used by tests)."""
    global _last_severity, _last_sent_at
    _last_severity = None
    _last_sent_at = 0.0


async def publish() -> tuple[str, str]:
    """Publish only on severity transitions, plus a long repeat interval.

    A health sample is still returned to the caller, but repeated non-ok
    samples do not enqueue a new alert every five minutes. A recovery is sent
    once so the operator gets a clear end to the incident.
    """
    global _last_severity, _last_sent_at

    severity, reason = rate(sample())
    now = time.monotonic()

    if severity == "ok":
        if _last_severity not in (None, "ok"):
            await push(
                SYSTEM,
                build(
                    "HostHealth",
                    "ok",
                    "Host recovered",
                    "all monitored thresholds returned to normal",
                    status="resolved",
                ),
            )
            _last_severity = "ok"
            _last_sent_at = now
        return severity, reason

    if _last_severity == severity and (now - _last_sent_at) < REPEAT_SECONDS:
        return severity, reason

    await push(
        SYSTEM,
        build("HostHealth", severity, f"Host {severity}: {reason}", "threshold crossed on this host"),
    )
    _last_severity = severity
    _last_sent_at = now
    return severity, reason
'''

FILES["ai_publish.py"] = '''"""Publish the outcome of an AI task the owner dispatched."""

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
'''

FILES["migration_count.py"] = '''"""Exact point count for one Qdrant collection."""

from __future__ import annotations

import json
import urllib.error
import urllib.request


def exact_count(collection: str, qdrant: str) -> int:
    """Return the exact point count, 0 when the collection is unreadable.

    Exact counts are requested because the incremental field lags, and a lagging
    number would report a finished migration as incomplete.
    """
    request = urllib.request.Request(  # noqa: S310 - loopback qdrant only
        f"{qdrant}/collections/{collection}/points/count",
        data=json.dumps({"exact": True}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        body = json.loads(urllib.request.urlopen(request, timeout=60).read())  # noqa: S310 - loopback qdrant
        return int(body["result"]["count"])
    except (urllib.error.URLError, OSError, ValueError, KeyError, TypeError):
        return 0
'''

FILES["migration_counts.py"] = '''"""Count migrated versus source points across the corpus."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

QDRANT = os.environ.get("QDRANT_URL", "http://127.0.0.1:6334").rstrip("/")
EXEMPT = "agent_vectors"


def counts() -> tuple[int, int]:
    """Return (migrated, total) for the live corpus.

    Exact counts are requested because the incremental field lags, and a lagging
    number would report a finished migration as incomplete.
    """
    try:
        body = json.loads(urllib.request.urlopen(f"{QDRANT}/collections", timeout=30).read())  # noqa: S310 - loopback qdrant
        names = [c["name"] for c in body["result"]["collections"]]
    except (urllib.error.URLError, OSError, ValueError, KeyError):
        return 0, 0
    from .migration_count import exact_count

    migrated = sum(exact_count(n, QDRANT) for n in names if n.endswith("_8b"))
    total = sum(exact_count(n, QDRANT) for n in names if not n.endswith("_8b") and n != EXEMPT)
    return migrated, total

'''

FILES["migration_marker.py"] = '''"""Marker recording that migration completion was announced."""

from __future__ import annotations

import os

MARKER = "/root/.knowledge/.migration-notified"


def write_marker(value: str) -> None:
    """Record the announced vector count, so the alert is never sent twice."""
    os.makedirs(os.path.dirname(MARKER), exist_ok=True)
    with open(MARKER, "w", encoding="utf-8") as handle:
        handle.write(value)
'''

FILES["migration_marker_check.py"] = '''"""Whether migration completion was already announced."""

from __future__ import annotations

import os

from .migration_marker import MARKER


def already_announced() -> bool:
    """True when the completion alert has already been sent.

    Synchronous on purpose: a single stat, so the async caller does no blocking
    filesystem work on the event loop.
    """
    return os.path.exists(MARKER)
'''

FILES["migration_publish.py"] = '''"""Publish the 8B migration completion event exactly once."""

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
'''

FILES["watch.py"] = '''"""Periodic task publishing health and migration events."""

from __future__ import annotations

import asyncio
import contextlib
import logging

from .health_publish import publish as publish_health
from .migration_publish import publish_if_complete

logger = logging.getLogger("adminbot.notify")
INTERVAL = 300


async def watch() -> None:
    """Publish health and migration events for the life of the bot.

    Each producer is isolated: a failure in one logs and the loop continues, so
    a transient problem never stops notifications or the bot.
    """
    while True:
        try:
            level, reason = await publish_health()
            if level != "ok":
                logger.info("health %s: %s", level, reason)
        except Exception:  # a producer must not kill the loop
            logger.warning("health publish failed", exc_info=True)
        with contextlib.suppress(Exception):
            if await publish_if_complete():
                logger.info("migration completion published")
        await asyncio.sleep(INTERVAL)
'''


def write_all() -> int:
    """Write every module and assert the structural rules."""
    PKG.mkdir(parents=True, exist_ok=True)
    for name, body in FILES.items():
        (PKG / name).write_text(body)
    problems: list[str] = []
    for path in sorted(PKG.glob("*.py")):
        if path.name == "__init__.py":
            continue
        text = path.read_text()
        count = len(re.findall(r"^def |^async def ", text, re.M))
        lines = len(text.splitlines())
        if count != 1:
            problems.append(f"{path.name}: {count} functions (want 1)")
        if lines >= BUDGET:
            problems.append(f"{path.name}: {lines} lines (budget {BUDGET})")
    for problem in problems:
        print("  " + problem)
    if problems:
        return 1
    print(f"generated {len(FILES) - 1} notify modules, one function each, under {BUDGET} lines")
    return 0


if __name__ == "__main__":
    raise SystemExit(write_all())
