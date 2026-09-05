"""Default Telegram monitored channels for the news ingest scheduler.

Used when ``TELEGRAM_MONITORED_CHANNELS`` is not set so the scheduled
ingest cycle always has a channel list to sweep. Entries follow the
``unified_news_sources.json`` schema (``username`` + ``category``).
"""

from __future__ import annotations

from typing import Any, Final

DEFAULT_MONITORED_CHANNELS: Final[list[dict[str, Any]]] = [
    {"username": "lookonchain", "category": "on-chain"},
    {"username": "whale_alert", "category": "whales"},
    {"username": "Cointelegraph", "category": "news"},
    {"username": "CoinDesk", "category": "news"},
    {"username": "TheBlockCrypto", "category": "news"},
    {"username": "WatcherGuru", "category": "news"},
    {"username": "CryptoPanic", "category": "alerts"},
    {"username": "peckshield", "category": "security"},
    {"username": "SlowMist_Team", "category": "security"},
    {"username": "CertiKAlert", "category": "security"},
    {"username": "rugpullscanner", "category": "scams"},
]
