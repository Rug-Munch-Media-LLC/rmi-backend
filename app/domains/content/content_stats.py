"""Content pipeline ingestion stats — Redis-backed per-source counters.

The ingestion pipelines (X/nitter in ``app.caching_shield.social_feed``,
RSS in ``app.caching_shield.rss_sources``, Telegram sweeps in
``app.domains.news_ingest.scheduler``) write rolling stats here; the admin
endpoint ``/api/v1/admin/content/health`` reads them back.

Keys (all rolling 24h TTL unless noted):
  content:stats:{source}:last_fetch     — ISO-8601 timestamp of last fetch
  content:stats:{source}:ingested_24h   — rolling 24h ingested counter
  content:stats:{source}:errors         — rolling 24h error counter
  content:stats:{source}:mode           — fetch mode (x_api | nitter | rss_fallback)
  content:stats:telegram:channel_errors — hash: channel -> error count (24h TTL)

All writers/readers fail-open: stats are best-effort and must never break
ingestion or the health endpoint.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

STATS_TTL_SECONDS = 86_400  # 24h rolling window
KEY_PREFIX = "content:stats"


def _key(source: str, field: str) -> str:
    return f"{KEY_PREFIX}:{source}:{field}"


async def _redis() -> Any | None:
    try:
        from app.core.redis import get_redis_async

        return get_redis_async()
    except Exception as exc:  # pragma: no cover — fail-open
        logger.debug("content_stats_redis_unavailable err=%s", str(exc)[:160])
        return None


async def record_fetch(
    source: str,
    *,
    ingested: int = 0,
    errors: int = 0,
    mode: str | None = None,
) -> None:
    """Record one fetch cycle for *source* (last_fetch + counters)."""
    client = await _redis()
    if client is None:
        return
    try:
        now = datetime.now(UTC).isoformat()
        await client.set(_key(source, "last_fetch"), now)
        if ingested:
            await client.incr(_key(source, "ingested_24h"), int(ingested))
            await client.expire(_key(source, "ingested_24h"), STATS_TTL_SECONDS)
        if errors:
            await client.incr(_key(source, "errors"), int(errors))
            await client.expire(_key(source, "errors"), STATS_TTL_SECONDS)
        if mode:
            await client.set(_key(source, "mode"), mode)
    except Exception as exc:  # fail-open
        logger.debug("content_stats_write_failed source=%s err=%s", source, str(exc)[:160])


async def record_channel_errors(channel_errors: dict[str, int]) -> None:
    """Increment per-channel error counts for the Telegram source."""
    client = await _redis()
    if client is None or not channel_errors:
        return
    try:
        key = _key("telegram", "channel_errors")
        for channel, count in channel_errors.items():
            if count:
                await client.hincrby(key, channel, int(count))
        await client.expire(key, STATS_TTL_SECONDS)
    except Exception as exc:  # fail-open
        logger.debug("content_stats_channel_errors_failed err=%s", str(exc)[:160])


async def get_source_stats(source: str) -> dict[str, Any]:
    """Read the stat keys for *source*; missing keys → None."""
    client = await _redis()
    if client is None:
        return {"last_fetch": None, "ingested_24h": None, "errors": None, "mode": None}
    try:
        last_fetch, ingested, errors, mode = await client.mget(
            _key(source, "last_fetch"),
            _key(source, "ingested_24h"),
            _key(source, "errors"),
            _key(source, "mode"),
        )
        return {
            "last_fetch": last_fetch,
            "ingested_24h": int(ingested) if ingested is not None else None,
            "errors": int(errors) if errors is not None else None,
            "mode": mode,
        }
    except Exception as exc:  # fail-open
        logger.debug("content_stats_read_failed source=%s err=%s", source, str(exc)[:160])
        return {"last_fetch": None, "ingested_24h": None, "errors": None, "mode": None}


async def get_channel_errors() -> dict[str, int]:
    """Read the per-channel Telegram error hash (empty dict when absent)."""
    client = await _redis()
    if client is None:
        return {}
    try:
        data = await client.hgetall(_key("telegram", "channel_errors"))
        return {k: int(v) for k, v in (data or {}).items()}
    except Exception as exc:  # fail-open
        logger.debug("content_stats_channel_errors_read_failed err=%s", str(exc)[:160])
        return {}
