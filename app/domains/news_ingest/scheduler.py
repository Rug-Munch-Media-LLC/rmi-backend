"""Scheduled Telegram channel ingest.

Two layers:
  * Pure scheduling helpers (``daily_sweep_times``, ``sweep_schedule``,
    ``next_sweep_times``) — the deterministic sweep clock used by tests and
    the daily pipeline.
  * The runtime scheduler (``run_ingest_cycle`` / ``start_ingest_scheduler``)
    that wires the ingest pipeline (``read_channel_messages`` →
    ``channel_message_to_article`` → Qdrant ``news_v2``) to a periodic
    background cycle so monitored channels are swept even when the daily
    content pipeline isn't running.

Channel list comes from the ``TELEGRAM_MONITORED_CHANNELS`` env var
(comma-separated @usernames, leading ``@`` optional). When unset, the cycle
falls back to :data:`app.domains.news_ingest.channels.DEFAULT_MONITORED_CHANNELS`.
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, time, timedelta, tzinfo
from typing import Any, Final
from zoneinfo import ZoneInfo

from app.core.logging import get_logger
from app.domains.news_ingest.ingest import (
    DEFAULT_DEDUP_COLLECTION,
    filter_seen_articles,
    mark_articles_ingested,
    sweep_channels,
)
from app.feed_quality_pipeline.pipeline import run_quality_pipeline

logger = get_logger(__name__)

#: Articles scoring below this are dropped before the Qdrant upsert.
QUALITY_GATE_THRESHOLD = 0.3

# ── Pure sweep clock (original stub API) ────────────────────────────────────

# Default sweep clock (UTC hours). A few times per day keeps Telegram rate
# limits low while still catching breaking news.
daily_sweep_times: Final[list[int]] = [9, 13, 17, 21]
DEFAULT_SWEEPS_PER_DAY: Final[int] = len(daily_sweep_times)
DEFAULT_TZ: Final[tzinfo] = ZoneInfo("UTC")


def sweep_schedule(n_per_day: int) -> list[time]:
    """Evenly spaced times of day for *n_per_day* sweeps (0-23h, no tz).

    Examples: ``n=4`` → [00:00, 06:00, 12:00, 18:00]; ``n=1`` → [00:00].
    Clamped to at most 24 entries; non-positive input → empty list.
    """
    if n_per_day <= 0:
        return []
    n = min(n_per_day, 24)
    spacing = 24 / n
    return [
        time(
            hour=int(i * spacing),
            minute=int((i * spacing % 1) * 60),
        )
        for i in range(n)
    ]


def next_sweep_times(n_per_day: int, tz: tzinfo, *, now: datetime | None = None) -> list[datetime]:
    """Next *n_per_day* sweep datetimes (aware, in *tz*) strictly after *now*.

    Deterministic: identical inputs produce identical output. When *now* is
    omitted the current UTC time is used. Naive *now* values are assumed UTC.
    """
    now = now if now is not None else datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    now = now.astimezone(tz)

    schedule = sweep_schedule(n_per_day)
    if not schedule:
        return []

    results: list[datetime] = []
    day = now.date()
    while len(results) < len(schedule):
        for scheduled in schedule:
            candidate = datetime.combine(day, scheduled, tzinfo=tz)
            if candidate > now:
                results.append(candidate)
                if len(results) == len(schedule):
                    break
        day += timedelta(days=1)
    return results


# ── Runtime ingest scheduler ────────────────────────────────────────────────

#: Qdrant collection the normalized articles are embedded into.
NEWS_COLLECTION = "news_v2"

#: Env var holding the comma-separated list of monitored channel usernames.
CHANNELS_ENV = "TELEGRAM_MONITORED_CHANNELS"

_DEFAULT_INTERVAL_MINUTES = 15


def channels_from_env(env: str | None = None) -> list[dict[str, Any]]:
    """Parse ``TELEGRAM_MONITORED_CHANNELS`` into source-config dicts.

    Comma-separated usernames; whitespace and leading ``@`` are stripped.
    Empty/missing env → :data:`DEFAULT_MONITORED_CHANNELS` so the scheduler
    always has channels to monitor.
    """
    raw = env if env is not None else os.getenv(CHANNELS_ENV, "")
    channels: list[dict[str, Any]] = []
    for part in (raw or "").split(","):
        username = part.strip().lstrip("@")
        if username:
            channels.append({"username": username, "category": "crypto_news"})
    if not channels:
        from app.domains.news_ingest.channels import DEFAULT_MONITORED_CHANNELS

        return [dict(entry) for entry in DEFAULT_MONITORED_CHANNELS]
    return channels


async def run_ingest_cycle(
    client: Any | None = None,
    *,
    limit: int = 50,
    channels: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """One ingest sweep: read channels → normalize → embed into Qdrant.

    Fail-open per channel (``sweep_channels`` contract). Articles already
    recorded in the shared Redis dedup sets are skipped; dedup keys are
    marked only after a successful Qdrant upsert so a transient outage
    never permanently hides an un-persisted story.

    Returns a stats dict for monitoring/logging.
    """
    if channels is None:
        channels = channels_from_env()
        if not channels:
            # Fall back to the enabled news-kind registry channels.
            try:
                from app.domains.content.daily_pipeline import news_channels_from_registry

                channels = news_channels_from_registry()
            except Exception as exc:  # pragma: no cover — registry is optional
                logger.debug("news_ingest_registry_fallback_failed err=%s", str(exc)[:160])
                channels = []

    if client is None:
        try:
            from app.domains.content.daily_pipeline import _build_telegram_client

            client = await _build_telegram_client()
        except Exception as exc:  # pragma: no cover — fail-open
            logger.warning("news_ingest_client_unavailable err=%s", str(exc)[:160])
            client = None

    articles = await sweep_channels(client, channels, limit=limit)
    fresh, skipped = filter_seen_articles(articles, DEFAULT_DEDUP_COLLECTION)

    # Quality gate: validate → dedup → score before persisting. Fail-open —
    # a pipeline failure must never block ingestion entirely.
    quality_scores: dict[int, float] = {}
    if fresh:
        try:
            scored = await run_quality_pipeline(
                [
                    {
                        "_idx": i,
                        "id": article.content_hash or article.source_url or article.title or str(i),
                        "title": article.title or "",
                        "content": (article.body or article.title or "")[:2000],
                        "url": article.source_url or "",
                        "author": article.source_channel or "",
                        "published": article.posted_at.isoformat() if article.posted_at else "",
                        "categories": [article.category] if article.category else [],
                        "source": "telegram",
                    }
                    for i, article in enumerate(fresh)
                ],
                provider_group="news",
                source="telegram",
            )
            quality_scores = {d["_idx"]: float(d.get("quality_score", 0.0)) for d in scored}
            kept = [
                (i, article) for i, article in enumerate(fresh) if quality_scores.get(i, 0.0) >= QUALITY_GATE_THRESHOLD
            ]
            dropped = len(fresh) - len(kept)
            fresh = [article for _, article in kept]
            # Re-key scores to the filtered list positions for the metadata payload.
            quality_scores = {new_i: quality_scores[old_i] for new_i, (old_i, _) in enumerate(kept)}
            if dropped:
                logger.info(
                    "news_ingest_quality_gate dropped=%d kept=%d threshold=%.2f",
                    dropped,
                    len(fresh),
                    QUALITY_GATE_THRESHOLD,
                )
        except Exception as exc:  # fail-open
            logger.warning("news_ingest_quality_pipeline_failed err=%s", str(exc)[:160])

    ingested = 0
    errors: list[str] = []
    now = datetime.now(UTC).isoformat()
    if fresh:
        from app.integrations.rugbrain_client import rugbrain_ingest

        persisted: list[Any] = []
        for idx, article in enumerate(fresh):
            fingerprint = article.content_hash or article.source_url or article.title or ""
            doc_id = f"tg_news:{article.source_channel}:{fingerprint[:64]}"
            content = (article.body or article.title or "")[:2000]
            metadata = {
                "kind": "news_article",
                "title": (article.title or "")[:200],
                "source_channel": article.source_channel or "",
                "source_url": article.source_url or "",
                "category": article.category,
                "posted_at": article.posted_at.isoformat() if article.posted_at else None,
                "ingestion": "telegram_scheduled",
                "indexed_at": now,
                "quality_score": round(quality_scores.get(idx, 0.0), 4),
            }
            try:
                ok = await rugbrain_ingest(
                    content=content,
                    collection=NEWS_COLLECTION,
                    doc_id=doc_id,
                    metadata=metadata,
                    source="telegram",
                )
            except Exception as exc:
                ok = False
                errors.append(f"{doc_id}: {exc!s}"[:200])
            if ok:
                ingested += 1
                persisted.append(article)
            else:
                errors.append(f"{doc_id}: ingest_failed")
        if persisted:
            mark_articles_ingested(persisted, DEFAULT_DEDUP_COLLECTION)

    try:
        from app.domains.content.content_stats import record_channel_errors, record_fetch

        await record_fetch("telegram", ingested=ingested, errors=len(errors))
        per_channel: dict[str, int] = {}
        for err in errors:
            # error strings look like "tg_news:{channel}:{fingerprint}: ..."
            parts = err.split(":", 2)
            channel = parts[1] if len(parts) > 1 and parts[0] == "tg_news" else "unknown"
            per_channel[channel] = per_channel.get(channel, 0) + 1
        if per_channel:
            await record_channel_errors(per_channel)
    except Exception as exc:  # stats are best-effort
        logger.debug("content_stats_write_failed err=%s", str(exc)[:160])

    stats = {
        "status": "ok",
        "channels": len(channels),
        "swept": len(articles),
        "fresh": len(fresh),
        "skipped_seen": skipped,
        "ingested": ingested,
        "errors": errors[:5],
        "indexed_at": now if fresh else None,
    }
    logger.info(
        "news_ingest_cycle channels=%d swept=%d fresh=%d ingested=%d skipped=%d",
        len(channels),
        len(articles),
        len(fresh),
        ingested,
        skipped,
    )
    return stats


async def start_ingest_scheduler(
    interval_minutes: int = _DEFAULT_INTERVAL_MINUTES,
    *,
    first_delay_seconds: int = 60,
) -> asyncio.Task[None]:
    """Run :func:`run_ingest_cycle` on an interval as a background task.

    The first cycle runs after *first_delay_seconds* (lets the app settle),
    then every *interval_minutes*. Returns the created task so the caller
    can cancel it on shutdown.
    """
    interval = max(1, int(interval_minutes)) * 60

    async def _loop() -> None:
        await asyncio.sleep(first_delay_seconds)
        while True:
            try:
                await run_ingest_cycle()
            except Exception as exc:  # never kill the loop
                logger.warning("news_ingest_cycle_failed err=%s", str(exc)[:200])
            await asyncio.sleep(interval)

    task = asyncio.create_task(_loop())
    logger.info("news_ingest_scheduler_started interval_min=%d", interval)
    return task


async def stop_ingest_scheduler(task: asyncio.Task[None] | None) -> None:
    """Cancel the scheduler task (best-effort, never raises)."""
    if task is None:
        return
    try:
        task.cancel()
        logger.info("news_ingest_scheduler_cancelled")
    except Exception as exc:
        logger.debug("news_ingest_scheduler_cancel_failed err=%s", exc)
