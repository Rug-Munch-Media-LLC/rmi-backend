"""State + storage paths for Telegram public-preview ingestion.

Layout (default ``<repo>/data/telegram_news/``)::

    <channel>.jsonl        one JSON post per line (append-only, deduped)
    <channel>.state.json   last_id / etag / channel profile / counters
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from app.core.logging import get_logger

logger = get_logger("tg_news")


def news_dir(root: str | Path | None = None) -> Path:
    base = root or os.getenv("TELEGRAM_NEWS_DIR") or Path(__file__).resolve().parents[3] / "data" / "telegram_news"
    return Path(base)


def channel_path(channel: str, root: str | Path | None = None) -> Path:
    return news_dir(root) / f"{channel.lstrip('@')}.jsonl"


def state_path(channel: str, root: str | Path | None = None) -> Path:
    return news_dir(root) / f"{channel.lstrip('@')}.state.json"


def load_state(channel: str, root: str | Path | None = None) -> dict[str, Any]:
    """Load persisted state; a missing/corrupt file yields an empty state."""
    path = state_path(channel, root)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError) as exc:
        logger.debug("no usable state for %s: %s", channel, exc)
        return {}


def save_state(channel: str, state: dict[str, Any], root: str | Path | None = None) -> None:
    """Atomically persist state (tmp file + replace), fail-open on IO errors."""
    path = state_path(channel, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)
