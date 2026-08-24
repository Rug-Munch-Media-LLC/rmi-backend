"""Wallet Activity provider — polls ClickHouse token_transfers for watched wallets.

ClickHouse table: wallet_memory.token_transfers
Query: any transfer involving watched wallet addresses since last cursor.

Source: Own indexed transfer data — zero external dependency.

Note: This provider needs the *watch list* from active subscriptions.
The engine passes cursor that includes which addresses to watch.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.domains.scanner.engines._logging import get_logger
from app.domains.scanner.webhooks.models import WebhookEventType
from app.domains.scanner.webhooks.providers.utils import get_clickhouse

log = get_logger(__name__)


class WalletActivityProvider:
    """Polls token_transfers for activity on watched wallets."""

    event_type: WebhookEventType = WebhookEventType.WALLET_ACTIVITY
    poll_interval: int = 30  # seconds

    async def poll(self, cursor: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Query ClickHouse for transfers involving watched addresses."""
        since = (cursor or {}).get("since", "2020-01-01 00:00:00")
        # Engine injects the watch list as "wallet_addresses"; "addresses" is
        # kept as a legacy alias.
        watch_addresses: list[str] = (cursor or {}).get("wallet_addresses") or (cursor or {}).get("addresses", [])

        if not watch_addresses:
            return []

        ch = get_clickhouse()
        if ch is None:
            log.warning("clickhouse_unavailable wallet_activity_provider")
            return []

        # Build parameterised IN clause (numeric placeholders — address values
        # stay as bound params, never as SQL text)
        placeholders = ", ".join(f"%({i})s" for i in range(len(watch_addresses)))
        params: dict[str, str] = {str(i): a for i, a in enumerate(watch_addresses)}
        params["since"] = since

        try:
            rows = ch.execute(
                f"""
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
                  AND (from_address IN ({placeholders})
                       OR to_address IN ({placeholders}))
                ORDER BY block_timestamp ASC
                LIMIT 100
                """,  # noqa: S608 - verified bound/allowlisted SQL construction  # nosec B608 - param-bound query; f-string only interpolates allowlisted literals/placeholders
                params,
            )
        except (ValueError, ConnectionError, RuntimeError, httpx.HTTPError) as exc:
            log.warning("wallet_activity_query_failed", error=str(exc))
            return []

        events: list[dict[str, Any]] = []
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
        return "webhook:cursor:wallet_activity"
