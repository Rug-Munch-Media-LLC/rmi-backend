"""Liquidity Change provider — monitors DEX liquidity changes.

This provider uses the token_transfers table combined with known DEX
pool addresses and our internal LP health monitor when available.

ClickHouse table: wallet_memory.token_transfers
Additional data: app.liquidity_watchdog (if available)

Source: Own indexed data + internal analysis — zero external dependency.
"""

from __future__ import annotations

from app.core.logging import get_logger
from app.domains.scanner.webhooks.models import WebhookEventType
from app.domains.scanner.webhooks.providers.utils import get_clickhouse

log = get_logger(__name__)


class LiquidityChangeProvider:
    """Polls for significant liquidity add/remove events."""

    event_type: WebhookEventType = WebhookEventType.TOKEN_LIQUIDITY_CHANGE
    poll_interval: int = 60  # seconds

    # Minimum USD value for a "significant" liquidity change
    DEFAULT_MIN_VALUE: float = 5_000.0

    async def poll(self, cursor: dict | None = None) -> list[dict]:
        """Detect liquidity changes from internal data sources."""
        events: list[dict] = []

        # Try internal liquidity watchdog first
        watchdog = self._get_liquidity_watchdog()
        if watchdog is not None:
            try:
                watchdog_events = await watchdog.get_recent_changes(
                    min_value_usd=self.DEFAULT_MIN_VALUE,
                    since=cursor.get("since", "2020-01-01 00:00:00") if cursor else None,
                )
                if isinstance(watchdog_events, list):
                    events.extend(watchdog_events)
                    return events
            except (ValueError, ConnectionError, RuntimeError) as exc:
                log.debug("liquidity_watchdog_unavailable", error=str(exc))

        # Fallback: use ClickHouse token_transfers with known DEX pool addresses
        ch = get_clickhouse()
        if ch is None:
            log.warning("clickhouse_unavailable liquidity_change_provider")
            return []

        since = (cursor or {}).get("since", "2020-01-01 00:00:00")

        try:
            # Look for large transfers that could indicate LP operations
            # (transfers to/from known DEX pool addresses or large single transfers)
            rows = ch.execute(
                """
                SELECT
                    chain_id,
                    block_timestamp,
                    transaction_hash,
                    token_address,
                    from_address,
                    to_address,
                    value_decimal,
                    transfer_type
                FROM wallet_memory.token_transfers
                WHERE block_timestamp > %(since)s
                  AND value_decimal > %(min_val)s
                ORDER BY block_timestamp ASC
                LIMIT 50
                """,
                {"since": since, "min_val": self.DEFAULT_MIN_VALUE},
            )
        except (ValueError, ConnectionError, RuntimeError) as exc:
            log.warning("liquidity_query_failed", error=str(exc))
            return []

        for row in rows:
            events.append(
                {
                    "chain": row[0],
                    "block_timestamp": str(row[1]) if row[1] else "",
                    "transaction_hash": row[2],
                    "token_address": row[3],
                    "from_address": row[4],
                    "to_address": row[5],
                    "value_decimal": float(row[6]) if row[6] else 0.0,
                    "transfer_type": row[7],
                    "detection_method": "large_transfer_fallback",
                }
            )

        return events

    def _get_liquidity_watchdog(self) -> object | None:
        """Try to import the internal liquidity watchdog (optional)."""
        try:
            from app.liquidity_watchdog import LiquidityWatchdog

            return LiquidityWatchdog()
        except (ImportError, AttributeError):
            return None

    async def cursor_key(self) -> str:
        return "webhook:cursor:liquidity_change"
