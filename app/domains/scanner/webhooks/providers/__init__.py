"""Event providers — each polls a free data source for events.

Every provider follows the same interface::

    class XxxProvider:
        event_type: WebhookEventType = WebhookEventType.XXX
        poll_interval: int = 30  # seconds

        async def poll(self, cursor: dict | None = None) -> list[dict]:
            ...

        async def cursor_key(self) -> str:
            ...

Providers are registered in PROVIDER_REGISTRY and consumed by WebhookEngine.
"""

from __future__ import annotations

from app.domains.scanner.webhooks.providers.first_swap import FirstSwapProvider
from app.domains.scanner.webhooks.providers.liquidity_change import LiquidityChangeProvider
from app.domains.scanner.webhooks.providers.new_block import NewBlockProvider
from app.domains.scanner.webhooks.providers.price_change import PriceChangeProvider
from app.domains.scanner.webhooks.providers.token_deployed import TokenDeployedProvider
from app.domains.scanner.webhooks.providers.wallet_activity import WalletActivityProvider
from app.domains.scanner.webhooks.providers.whale_move import WhaleMoveProvider

# Registry: all providers in order.  The engine iterates this list
# and starts a background poll loop for each.
PROVIDER_REGISTRY: list[type] = [
    TokenDeployedProvider,
    WhaleMoveProvider,
    WalletActivityProvider,
    NewBlockProvider,
    FirstSwapProvider,
    PriceChangeProvider,
    LiquidityChangeProvider,
]

__all__ = [
    "PROVIDER_REGISTRY",
    "FirstSwapProvider",
    "LiquidityChangeProvider",
    "NewBlockProvider",
    "PriceChangeProvider",
    "TokenDeployedProvider",
    "WalletActivityProvider",
    "WhaleMoveProvider",
]
