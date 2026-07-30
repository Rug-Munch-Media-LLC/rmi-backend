"""Scanner Webhooks — zero-dependency event-based webhook system.

Architecture
────────────
Poll-based webhook engine that uses only:

  1. Our own ClickHouse data (token_transfers, deployer_history, wallet_profiles)
  2. Free public RPCs (eth_blockNumber, sol_getSlot)

No external WebSocket subscriptions, no paid APIs, no third-party webhook
delivery services.

Event Sources (all free / self-hosted):
  - token.deployed        → ClickHouse deployer_history table
  - token.first_swap      → ClickHouse token_transfers ∩ DEX router addresses
  - token.price_change    → own_price_oracle (in-memory or ClickHouse)
  - token.whale_move      → ClickHouse token_transfers (value > threshold)
  - token.liquidity_change→ ClickHouse DEX pool events
  - wallet.activity       → ClickHouse token_transfers by watched address
  - chain.block           → Free RPC polling (eth_blockNumber / sol_getSlot)

Data Flow::
  WebhookEngine.start()
    ├── _poll_token_deployments()  → ClickHouse → _dispatch()
    ├── _poll_whale_moves()        → ClickHouse → _dispatch()
    ├── _poll_wallet_activity()    → ClickHouse → _dispatch()
    ├── _poll_price_changes()      → Oracle     → _dispatch()
    ├── _poll_liquidity()          → ClickHouse → _dispatch()
    └── _poll_new_blocks()         → Free RPC   → _dispatch()
         │
         └── WebhookDeliveryService.deliver()
               ├── httpx POST to subscriber URL
               ├── Retry × 3 (10s, 30s, 60s backoff)
               └── Log delivery outcome to Redis
"""

from __future__ import annotations

from app.domains.scanner.webhooks.delivery import WebhookDeliveryService
from app.domains.scanner.webhooks.engine import WebhookEngine
from app.domains.scanner.webhooks.models import (
    WebhookDelivery,
    WebhookEvent,
    WebhookEventType,
    WebhookFilters,
    WebhookSubscription,
)
from app.domains.scanner.webhooks.repository import WebhookRepository
from app.domains.scanner.webhooks.router import router

__all__ = [
    "WebhookDelivery",
    "WebhookDeliveryService",
    "WebhookEngine",
    "WebhookEvent",
    "WebhookEventType",
    "WebhookFilters",
    "WebhookRepository",
    "WebhookSubscription",
    "router",
]
