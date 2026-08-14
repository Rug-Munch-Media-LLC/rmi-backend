"""
RugMunchBot — scheduled channel content (news / alerts / alpha).

Wire-up for the bot-owned Telegram channels (``config.CHANNELS``): each
channel gets its own content formatter and a JobQueue-based poster that runs
on an env-configurable cadence (``channels/scheduler.py``).

Channels
--------
* ``news``   — curated headlines from our backend ``/api/v1/news/trending``.
* ``alerts`` — scanner risk-alert digest (high-risk launches / honeypot+rug
               flags) from ``/api/v1/launches/alerts``. Event-driven scanner
               alerts already reach this channel via ``alert_hooks``; this
               job adds a REGULAR digest so the channel stays active.
* ``alpha``  — whale movements (helius WhaleWatcher via
               ``app.domains.alerts.whale_watch`` when populated) + smart
               money wallets (backend ``/api/v1/databus/premium/smart-money``)
               + new launches (``/api/v1/launches/feed``).

Every job fails open: a missing channel id, backend outage, or malformed
item logs and skips — it never crashes the bot's JobQueue.
"""

from app.domains.telegram.rugmunchbot.channels.scheduler import (
    post_alert_update,
    post_alpha_update,
    post_news_update,
    register_channel_jobs,
)

__all__ = [
    "post_alert_update",
    "post_alpha_update",
    "post_news_update",
    "register_channel_jobs",
]
