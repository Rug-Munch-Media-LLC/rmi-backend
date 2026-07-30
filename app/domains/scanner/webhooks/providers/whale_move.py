"""Whale Move provider — polls ClickHouse token_transfers for large transfers.

ClickHouse table: wallet_memory.token_transfers
Query: transfers above a USD value threshold since last cursor.
Default threshold: $10,000 equivalent (configurable per subscription filter).

Source: Own indexed transfer data — zero external dependency.
"""

from __future__ import annotations

from app.core.logging import get_logger
from app.domains.scanner.webhooks.models import WebhookEventType
from app.domains.scanner.webhooks.providers.utils import get_clickhouse

log = get_logger(__name__)


class WhaleMoveProvider:
    """Polls token_transfers for large-value transfers."""

    event_type: WebhookEventType = WebhookEventType.TOKEN_WHALE_MOVE
    poll_interval: int = 60  # seconds

    # Default minimum value in USD to consider a "whale" move
    DEFAULT_MIN_VALUE: float = 10_000.0

    async def poll(self, cursor: dict | None = None) -> list[dict]:
        """Query ClickHouse for large transfers since last cursor."""
        since = (cursor or {}).get("since", "2020-01-01 00:00:00")
        ch = get_clickhouse()
        if ch is None:
            log.warning("clickhouse_unavailable whale_move_provider")
            return []

        try:
            rows = ch.execute(
                """
                SELECT
                    chain_id,
                    block_timestamp,
                    transaction_hash,
                    from_address,
                    to_address,
                    token_address,
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
            log.warning("whale_move_query_failed", error=str(exc))
            return []

        events: list[dict] = []
        for row in rows:
            events.append(
                {
                    "chain": row[0],
                    "block_timestamp": str(row[1]) if row[1] else "",
                    "transaction_hash": row[2],
                    "from_address": row[3],
                    "to_address": row[4],
                    "token_address": row[5],
                    "value_decimal": float(row[6]) if row[6] else 0.0,
                    "transfer_type": row[7],
                }
            )

        return events

    async def cursor_key(self) -> str:
        return "webhook:cursor:whale_move"
