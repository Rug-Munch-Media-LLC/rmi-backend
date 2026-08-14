"""Scheduled channel posters — JobQueue-based, env-configurable cadence.

Registered from ``bot.py`` next to the existing background jobs. Each job
reads its channel chat id + enabled flag at tick time (so env overrides apply
without a restart after ``config.reset_channel_cache()``), fetches live
content, formats it, and sends to the channel. All jobs fail open: missing
chat id, backend outage, or Telegram errors log and skip — the JobQueue keeps
running.

Cadence (env-overridable, defaults):
* news     → every 4h  (``RMI_CHANNEL_NEWS_INTERVAL_HOURS``)
* alpha    → every 2h  (``RMI_CHANNEL_ALPHA_INTERVAL_HOURS``) — whale moves
            are collected on the same cadence so whale events surface in the
            digest within one interval (plus the existing event-driven rails)
* alerts   → every 30m (``RMI_CHANNEL_ALERTS_INTERVAL_MINUTES``)
* tg_news  → every 1h  (``RMI_CHANNEL_TG_NEWS_INTERVAL_MINUTES``) — ingests
            public crypto-news Telegram channels via the headless client,
            persists new articles to the website news table, then posts a
            fresh digest (see ``channels/telegram_news.py``)

Master switch: ``RMI_CHANNELS_ENABLED`` (default on). Per-channel switches:
``RMI_NEWS_CHANNEL_ENABLED`` / ``RMI_ALERTS_CHANNEL_ENABLED`` /
``RMI_ALPHA_CHANNEL_ENABLED`` / ``RMI_TG_NEWS_CHANNEL_ENABLED`` (default on).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from telegram.constants import ParseMode
from telegram.error import TelegramError

from app.domains.telegram.rugmunchbot.channels.content import (
    fetch_alpha_content,
    fetch_risk_alerts,
    fetch_trending_news,
)
from app.domains.telegram.rugmunchbot.channels.formatters import (
    format_alerts_post,
    format_alpha_post,
    format_news_post,
)
from app.domains.telegram.rugmunchbot.channels.telegram_news import (
    TG_NEWS_ENABLED,
    TG_NEWS_INTERVAL_MINUTES,
    post_tg_news_update,
)
from app.domains.telegram.rugmunchbot.config import (
    ALERTS_CHANNEL_ENABLED,
    ALERTS_CHANNEL_INTERVAL_MINUTES,
    ALERTS_CHANNEL_LIMIT,
    ALPHA_CHANNEL_ENABLED,
    ALPHA_CHANNEL_INTERVAL_HOURS,
    ALPHA_CHANNEL_LIMIT,
    CHANNEL_JOBS_ENABLED,
    NEWS_CHANNEL_ENABLED,
    NEWS_CHANNEL_INTERVAL_HOURS,
    NEWS_CHANNEL_LIMIT,
    _resolve_channel_id,
)
from app.domains.telegram.rugmunchbot.support.logging import get_logger

if TYPE_CHECKING:
    from telegram.ext import Application, ContextTypes

logger = get_logger("rmi_bot.channels")

# First-run delays so the channels go live shortly after boot (not silent for
# a full interval): news 45s, alerts 75s, alpha 105s, tg_news 135s.
_FIRST_RUN_SECONDS = {"news": 45, "alerts": 75, "alpha": 105, "tg_news": 135}

# Bounded in-memory dedupe: don't re-post the same item on the next tick.
_posted_keys: dict[str, set[str]] = {}
_MAX_POSTED = 500


def _chat_id(channel: str) -> str | None:
    """Resolve a channel chat id (gopass → env → fallback), None when unknown."""
    return _resolve_channel_id(channel)


def _remember(channel: str, key: str) -> bool:
    """Return True when ``key`` is new for ``channel`` (and record it)."""
    seen = _posted_keys.setdefault(channel, set())
    if key in seen:
        return False
    seen.add(key)
    if len(seen) > _MAX_POSTED:
        _posted_keys[channel] = set(list(seen)[-_MAX_POSTED // 2 :])
    return True


def _dedupe_key(prefix: str, item: dict[str, Any]) -> str:
    url = (item.get("url") or "").strip()
    if url:
        return f"{prefix}:{url}"
    title = (item.get("title") or item.get("symbol") or "").strip()
    addr = (item.get("token_address") or item.get("address") or "").strip()
    return f"{prefix}:{addr or title}"


async def _safe_send(bot: Any, chat_id: str, text: str) -> bool:
    """Send one HTML message to a channel. Returns True on success, fails open."""
    try:
        await bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
        return True
    except TelegramError as exc:
        logger.warning("channels:send failed chat=%s err=%s", chat_id, exc)
    except (ValueError, TypeError, KeyError, OSError) as exc:
        logger.warning("channels:send error chat=%s err=%s", chat_id, exc)
    except Exception as exc:  # a poster must never crash the JobQueue
        logger.warning("channels:send unexpected chat=%s err=%s", chat_id, exc)
    return False


# ── News job ──────────────────────────────────────────────────────────────


async def post_news_update(ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Post the news digest to the news channel. Returns items sent (0-1)."""
    chat_id = _chat_id("news")
    if not chat_id:
        return 0
    items = await fetch_trending_news(NEWS_CHANNEL_LIMIT)
    fresh = [item for item in items if _remember("news", _dedupe_key("news", item))]
    if not fresh:
        logger.debug("channels:news no fresh headlines")
        return 0
    ok = await _safe_send(ctx.bot, chat_id, format_news_post(fresh))
    if ok:
        logger.info("channels:news posted %s headline(s)", len(fresh))
    return 1 if ok else 0


# ── Alerts job ────────────────────────────────────────────────────────────


async def post_alert_update(ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Post the scanner risk digest to the alerts channel. Returns 0-1."""
    chat_id = _chat_id("alerts")
    if not chat_id:
        return 0
    alerts = await fetch_risk_alerts(ALERTS_CHANNEL_LIMIT)
    fresh = [item for item in alerts if _remember("alerts", _dedupe_key("alerts", item))]
    if not fresh:
        logger.debug("channels:alerts no fresh risk alerts")
        return 0
    ok = await _safe_send(ctx.bot, chat_id, format_alerts_post(fresh))
    if ok:
        logger.info("channels:alerts posted %s risk alert(s)", len(fresh))
    return 1 if ok else 0


# ── Alpha job ─────────────────────────────────────────────────────────────


async def post_alpha_update(ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Post the alpha digest (whale moves + smart money + launches). Returns 0-1."""
    chat_id = _chat_id("alpha")
    if not chat_id:
        return 0
    content = await fetch_alpha_content(
        chain="solana",
        whale_limit=max(1, ALPHA_CHANNEL_LIMIT),
        smart_limit=ALPHA_CHANNEL_LIMIT,
        launch_limit=ALPHA_CHANNEL_LIMIT,
    )
    whale_fresh = [m for m in content["whale_moves"] if _remember("alpha:whale", _dedupe_key("whale", m))]
    smart_fresh = [w for w in content["smart_money"] if _remember("alpha:smart", _dedupe_key("smart", w))]
    launch_fresh = [item for item in content["launches"] if _remember("alpha:launch", _dedupe_key("launch", item))]
    if not (whale_fresh or smart_fresh or launch_fresh):
        logger.debug("channels:alpha no fresh signals")
        return 0
    ok = await _safe_send(
        ctx.bot,
        chat_id,
        format_alpha_post(whale_fresh, smart_fresh, launch_fresh),
    )
    if ok:
        logger.info(
            "channels:alpha posted whales=%s smart=%s launches=%s",
            len(whale_fresh),
            len(smart_fresh),
            len(launch_fresh),
        )
    return 1 if ok else 0


# ── Registration ──────────────────────────────────────────────────────────


def register_channel_jobs(app: Application[Any, Any, Any, Any, Any, Any]) -> None:
    """Register the channel poster jobs on the PTB JobQueue (fail-open).

    Skips a channel when its job is disabled or its chat id is unset, so a
    half-configured deployment still gets the channels it can post to.
    """
    if not CHANNEL_JOBS_ENABLED:
        logger.info("channels:job scheduling disabled (RMI_CHANNELS_ENABLED=false)")
        return
    job_queue = app.job_queue
    if job_queue is None:
        logger.info("channels:no job queue — skipping channel jobs")
        return

    if NEWS_CHANNEL_ENABLED and _chat_id("news"):
        job_queue.run_repeating(
            post_news_update,
            interval=NEWS_CHANNEL_INTERVAL_HOURS * 3600,
            first=_FIRST_RUN_SECONDS["news"],
            name="channel_news",
        )
    if ALERTS_CHANNEL_ENABLED and _chat_id("alerts"):
        job_queue.run_repeating(
            post_alert_update,
            interval=ALERTS_CHANNEL_INTERVAL_MINUTES * 60,
            first=_FIRST_RUN_SECONDS["alerts"],
            name="channel_alerts",
        )
    if ALPHA_CHANNEL_ENABLED and _chat_id("alpha"):
        job_queue.run_repeating(
            post_alpha_update,
            interval=ALPHA_CHANNEL_INTERVAL_HOURS * 3600,
            first=_FIRST_RUN_SECONDS["alpha"],
            name="channel_alpha",
        )
    if TG_NEWS_ENABLED and _chat_id("news"):
        job_queue.run_repeating(
            post_tg_news_update,
            interval=TG_NEWS_INTERVAL_MINUTES * 60,
            first=_FIRST_RUN_SECONDS["tg_news"],
            name="channel_tg_news",
        )
    logger.info("channels:channel jobs registered (news/alerts/alpha/tg_news)")


__all__ = [
    "post_alert_update",
    "post_alpha_update",
    "post_news_update",
    "register_channel_jobs",
]
