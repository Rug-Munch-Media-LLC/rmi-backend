"""Token Deployed provider — polls ClickHouse deployer_history for new tokens.

ClickHouse table: wallet_memory.deployer_history
Query: new rows since last cursor (deployed_at timestamp)
Source: Own indexer data — zero external dependency.
"""

from __future__ import annotations

from app.core.logging import get_logger
from app.domains.scanner.webhooks.models import WebhookEventType
from app.domains.scanner.webhooks.providers.utils import get_clickhouse

log = get_logger(__name__)


class TokenDeployedProvider:
    """Polls deployer_history for newly deployed tokens."""

    event_type: WebhookEventType = WebhookEventType.TOKEN_DEPLOYED
    poll_interval: int = 30  # seconds

    async def poll(self, cursor: dict | None = None) -> list[dict]:
        """Query ClickHouse for tokens deployed since the last cursor.

        Returns a list of event payload dicts.
        """
        since = (cursor or {}).get("since", "2020-01-01 00:00:00")
        ch = get_clickhouse()
        if ch is None:
            log.warning("clickhouse_unavailable token_deployed_provider")
            return []

        try:
            rows = ch.execute(
                """
                SELECT
                    deployer_address,
                    chain_id,
                    token_address,
                    token_name,
                    token_symbol,
                    deployed_at
                FROM wallet_memory.deployer_history
                WHERE deployed_at > %(since)s
                ORDER BY deployed_at ASC
                LIMIT 50
                """,
                {"since": since},
            )
        except (ValueError, ConnectionError, RuntimeError) as exc:
            log.warning("token_deployed_query_failed", error=str(exc))
            return []

        events: list[dict] = []
        for row in rows:
            events.append(
                {
                    "chain": row[1],
                    "token_address": row[2],
                    "token_name": row[3] or "",
                    "token_symbol": row[4] or "",
                    "deployer_address": row[0],
                    "deployed_at": str(row[5]) if row[5] else "",
                }
            )

        return events

    async def cursor_key(self) -> str:
        """Redis key for persisting the poll cursor."""
        return "webhook:cursor:token_deployed"
