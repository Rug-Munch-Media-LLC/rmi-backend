"""Price Change provider — polls our own price oracle for price movements.

This provider uses the in-memory price oracle (app.price_consensus) or
the CoinGecko/DexScreener data already cached in our system. No external
dependency at polling time — prices are already in our data layer.

The provider compares current prices against a rolling window to detect
movements exceeding the configured threshold.
"""

from __future__ import annotations

import time
from typing import Any

from app.core.logging import get_logger
from app.domains.scanner.webhooks.models import WebhookEventType

log = get_logger(__name__)

# Tokens to monitor (populated from active subscriptions)
DEFAULT_MONITOR_TOKENS: list[dict[str, str]] = []


class PriceChangeProvider:
    """Monitors our price oracle for significant price movements.

    This provider requires price data from our internal oracle.
    If the oracle is unavailable, it returns empty and logs a warning.
    """

    event_type: WebhookEventType = WebhookEventType.TOKEN_PRICE_CHANGE
    poll_interval: int = 120  # seconds — prices don't change that fast

    # Default thresholds
    DEFAULT_CHANGE_PCT: float = 5.0  # 5% change
    WINDOW_SECONDS: int = 300  # 5-minute rolling window

    async def poll(self, cursor: dict | None = None) -> list[dict]:
        """Check price oracle for tokens with significant price movement.

        Uses the in-process price_consensus module when available for
        real-time price data. Falls back gracefully if unavailable.
        """
        events: list[dict] = []

        # Get price oracle
        oracle = self._get_price_oracle()
        if oracle is None:
            log.debug("price_oracle_unavailable — skipping price_change poll")
            return []

        # Get tokens to monitor from cursor (populated by engine from subscriptions)
        tokens: list[dict[str, str]] = (cursor or {}).get("tokens", DEFAULT_MONITOR_TOKENS)
        if not tokens:
            return []

        min_change_pct = (cursor or {}).get("min_change_pct", self.DEFAULT_CHANGE_PCT)

        try:
            for token_info in tokens:
                token_address = token_info.get("address", "")
                chain = token_info.get("chain", "ethereum")

                if not token_address:
                    continue

                price_data = await self._get_price(oracle, chain, token_address)
                if price_data is None:
                    continue

                current_price = price_data.get("price", 0.0)
                price_5m_ago = price_data.get("price_5m_ago", current_price)

                if price_5m_ago > 0:
                    change_pct = abs((current_price - price_5m_ago) / price_5m_ago * 100)
                    if change_pct >= min_change_pct:
                        direction = "up" if current_price > price_5m_ago else "down"
                        events.append(
                            {
                                "chain": chain,
                                "token_address": token_address,
                                "price": current_price,
                                "price_before": price_5m_ago,
                                "change_pct": round(change_pct, 2),
                                "direction": direction,
                                "timestamp": time.time(),
                            }
                        )

        except (ValueError, ConnectionError, RuntimeError) as exc:
            log.warning("price_change_poll_failed", error=str(exc))

        return events

    def _get_price_oracle(self) -> Any | None:
        """Try to import the price consensus module (optional dependency)."""
        try:
            from app.price_consensus import get_price_with_history

            return get_price_with_history
        except (ImportError, AttributeError):
            return None

    async def _get_price(self, oracle: Any, chain: str, token_address: str) -> dict[str, float] | None:
        """Fetch current and historical price from our oracle."""
        try:
            result = await oracle(chain, token_address)
            if isinstance(result, dict):
                return result
        except (ValueError, TypeError, ConnectionError, RuntimeError):
            pass
        return None

    async def cursor_key(self) -> str:
        return "webhook:cursor:price_change"
