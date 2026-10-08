"""Analytics constants — protocols, time periods."""

from __future__ import annotations

SUPPORTED_PROTOCOLS = ["nostr", "farcaster", "bluesky", "mastodon", "direct"]

SECONDS_IN: dict[str, int] = {
    "1h": 3600,
    "24h": 86400,
    "7d": 604800,
    "30d": 2592000,
    "90d": 7776000,
}
