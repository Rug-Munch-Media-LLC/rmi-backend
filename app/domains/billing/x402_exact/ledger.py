"""x402 exact — usage ledger.

Every settled call is appended to a JSONL ledger (payer, resource, amount,
tx hash, network, request id, timestamp) and mirrored to a Redis list for
cheap tail reads. The ledger is the audit trail for revenue and disputes.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.logging import get_logger

log = get_logger(__name__)

LEDGER_PATH = Path(os.getenv("X402_LEDGER_PATH", "/var/lib/rmi/x402-exact-ledger.jsonl"))
_REDIS_KEY = "x402:exact:ledger"


def _append_line(path: Path, line: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line)


async def record(entry: dict[str, Any]) -> dict[str, Any]:
    """Append one settled-call entry. Returns the stored row."""
    row = {"ts": datetime.now(UTC).isoformat(), **entry}
    try:
        line = json.dumps(row, separators=(",", ":")) + "\n"
        await asyncio.to_thread(_append_line, LEDGER_PATH, line)
    except OSError as exc:
        log.error(f"x402_exact_ledger_write_failed: {exc}")

    try:
        import redis.asyncio as redis_lib

        client = redis_lib.Redis(
            host=os.getenv("REDIS_HOST", "localhost"),
            port=int(os.getenv("REDIS_PORT", "6379")),
            password=os.getenv("REDIS_PASSWORD") or None,
            decode_responses=True,
        )
        await client.lpush(_REDIS_KEY, json.dumps(row, separators=(",", ":")))
        await client.ltrim(_REDIS_KEY, 0, 4999)
    except (ImportError, ConnectionError, OSError, ValueError) as exc:
        log.warning(f"x402_exact_ledger_redis_failed: {exc}")
    return row


def entries(limit: int = 50) -> list[dict[str, Any]]:
    """Tail the JSONL ledger, newest first."""
    if not LEDGER_PATH.exists():
        return []
    rows: list[dict[str, Any]] = []
    with LEDGER_PATH.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return list(reversed(rows[-limit:]))
