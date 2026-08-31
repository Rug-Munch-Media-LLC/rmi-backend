"""Discovered-KOL store — new KOLs auto-added by the bot's X monitoring.

The bot (``rugmunchbot.channels.kol_discovery``) watches X for untracked
accounts pushing scanner-flagged risky tokens and auto-adds them with
status ``discovered`` + the token they shilled + tweet evidence. The bot
and this backend can share ONE JSON file via ``RMI_DISCOVERED_KOLS_PATH``
(zero-copy handoff); the backend default is its own bundled copy.

Read-only, fail-open: a missing/corrupt file degrades to an empty list.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from app.core.logging import get_logger

logger = get_logger("kol_discovered")

_DISCOVERED_PATH = Path(
    os.getenv(
        "RMI_DISCOVERED_KOLS_PATH",
        str(Path(__file__).resolve().parent / "discovered_kols.json"),
    )
)


def load_discovered_kols(path: str | Path | None = None) -> list[dict[str, Any]]:
    """Discovered-KOL records, newest first (fail-open ``[]``)."""
    p = Path(path) if path else _DISCOVERED_PATH
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.debug("kol_discovered:load_failed err=%s", str(exc)[:120])
        return []
    items = data.get("discovered") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return []
    return [e for e in items if isinstance(e, dict) and e.get("handle")]


def discovered_summary(limit: int = 100) -> dict[str, Any]:
    """Route-shaped summary: ``{discovered, total, source}``. Never raises."""
    records = load_discovered_kols()
    records.sort(key=lambda r: str(r.get("discovered_at") or ""), reverse=True)
    return {
        "discovered": records[: max(1, int(limit))],
        "total": len(records),
        "limit": max(1, int(limit)),
        "source": "discovered_kols",
    }


__all__ = ["discovered_summary", "load_discovered_kols"]
