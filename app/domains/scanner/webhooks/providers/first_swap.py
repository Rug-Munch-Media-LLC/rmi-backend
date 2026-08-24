"""First Swap provider — detects first DEX swap for a token.

Since we don't have a dedicated DEX data pipeline, this provider uses
the token_transfers table combined with known DEX router addresses to
detect the first swap. Known DEX routers are matched by address prefix
patterns.

ClickHouse table: wallet_memory.token_transfers
Query: first transfer involving a known DEX address for each token.

Source: Own indexed transfer data + known DEX router list — zero external
dependency.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.domains.scanner.engines._logging import get_logger
from app.domains.scanner.webhooks.models import WebhookEventType
from app.domains.scanner.webhooks.providers.utils import get_clickhouse

log = get_logger(__name__)

# Known DEX router address prefixes (lowercase)
# These match the first few characters of known DEX contracts
KNOWN_DEX_PREFIXES: list[str] = [
    # Uniswap V2/V3 routers
    "0x7a250d",  # Uniswap V2 Router
    "0x68b346",  # Uniswap V3 Router
    "0xe59242",  # Uniswap V3 SwapRouter
    # SushiSwap
    "0xd9e1cE",  # SushiSwap Router
    # PancakeSwap
    "0x10ed43",  # PancakeSwap V2 Router (BSC)
    "0x13f4ea",  # PancakeSwap V2 Router (BSC)
    # QuickSwap (Polygon)
    "0xa5e082",  # QuickSwap Router
    # Arbitrum
    "0x1b02da",  # SushiSwap on Arbitrum
    # Optimism
    "0x4c6004",  # Velodrome Router
    # Base
    "0x327df1",  # Aerodrome Router
]


class FirstSwapProvider:
    """Polls token_transfers for first DEX swap events."""

    event_type: WebhookEventType = WebhookEventType.TOKEN_FIRST_SWAP
    poll_interval: int = 60  # seconds

    async def poll(self, cursor: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Detect first swaps by finding transfers involving DEX routers."""
        since = (cursor or {}).get("since", "2020-01-01 00:00:00")
        seen_tokens: set[str] = set((cursor or {}).get("seen_tokens", []))

        ch = get_clickhouse()
        if ch is None:
            log.warning("clickhouse_unavailable first_swap_provider")
            return []

        try:
            # Find recent transfers where to_address starts with a known DEX prefix
            # This is a simplified heuristic — real DEX detection would check
            # event signatures, but this works for initial detection.
            rows = ch.execute(
                """
                SELECT
                    chain_id,
                    block_timestamp,
                    transaction_hash,
                    token_address,
                    from_address,
                    to_address,
                    value_decimal
                FROM wallet_memory.token_transfers
                WHERE block_timestamp > %(since)s
                  AND transfer_type = 'erc20'
                ORDER BY block_timestamp ASC
                LIMIT 200
                """,
                {"since": since},
            )
        except (ValueError, ConnectionError, RuntimeError, httpx.HTTPError) as exc:
            log.warning("first_swap_query_failed", error=str(exc))
            return []

        events: list[dict[str, Any]] = []
        for row in rows:
            token_addr = row[3].lower()
            if token_addr in seen_tokens:
                continue

            to_addr = (row[5] or "").lower()
            # Check if the "to" address matches any known DEX prefix
            is_dex = any(to_addr.startswith(prefix) for prefix in KNOWN_DEX_PREFIXES)

            if is_dex:
                seen_tokens.add(token_addr)
                events.append(
                    {
                        "chain": row[0],
                        "block_timestamp": str(row[1]) if row[1] else "",
                        "transaction_hash": row[2],
                        "token_address": row[3],
                        "from_address": row[4],
                        "to_address": row[5],
                        "value_decimal": float(row[6]) if row[6] else 0.0,
                        "detection_method": "dex_router_transfer",
                    }
                )

        return events

    async def cursor_key(self) -> str:
        return "webhook:cursor:first_swap"
