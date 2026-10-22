"""Build and persist one audit record per inbound command."""

from __future__ import annotations

import contextlib
import json
import time
from typing import Any

from .writer import append_line

STATUSES = frozenset({"ok", "error", "timeout", "denied"})
MAX_MSG = 200


def record_command(
    *,
    user_id: int | str | None,
    command: str,
    args_count: int = 0,
    duration_ms: int = 0,
    status: str = "ok",
    error_type: str | None = None,
    error_msg: str | None = None,
    source: str = "handler",
    ts: float | None = None,
) -> dict[str, Any]:
    """Append one command record and return it. Never raises."""
    record: dict[str, Any] = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts if ts is not None else time.time())),
        "user_id": user_id,
        "command": (command or "")[:64],
        "args_count": max(int(args_count), 0),
        "duration_ms": max(int(duration_ms), 0),
        "status": status if status in STATUSES else "error",
        "error_type": (error_type or "")[:64],
        "error_msg": (error_msg or "")[:MAX_MSG],
        "source": source,
    }
    with contextlib.suppress(Exception):  # persistence must never break dispatch
        append_line(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
    return record
