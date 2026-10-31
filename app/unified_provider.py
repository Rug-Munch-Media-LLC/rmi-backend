"""Unified Chain Data Provider - Multi-source with automatic fallback.

Priority: Helius → Birdeye → Solscan → GMGN → DexScreener → QuickNode → Blockchair
Rate-limited to ~5 req/sec total. Heavy caching. Never blocks on a single API.

The implementation moved to :mod:`app.providers` (one module per responsibility).
This module is a compatibility shim: every public symbol is re-exported
explicitly so ``from app.unified_provider import X`` keeps working.
"""

from __future__ import annotations

from app.observability.silent_failures import record_silent_failure as record_silent_failure

# REMOVE BY 2026-12-01 — compatibility shim for app.providers; point callers at
# app.providers (or app.providers.registry) and delete this file.
from app.providers import (
    UnifiedProvider as UnifiedProvider,
    get_unified_provider as get_unified_provider,
)
from app.providers.base import (
    CacheMixin as CacheMixin,
    ConnectorsMixin as ConnectorsMixin,
    ProviderBase as ProviderBase,
)
from app.providers.dex_sources import DexSourcesMixin as DexSourcesMixin
from app.providers.enrichment import EnrichmentMixin as EnrichmentMixin
from app.providers.holders import HoldersMixin as HoldersMixin
from app.providers.pricing import PricingMixin as PricingMixin
from app.providers.token_full import TokenFullMixin as TokenFullMixin
from app.providers.wallet_txs import WalletTxMixin as WalletTxMixin

__all__ = [
    "CacheMixin",
    "ConnectorsMixin",
    "DexSourcesMixin",
    "EnrichmentMixin",
    "HoldersMixin",
    "PricingMixin",
    "ProviderBase",
    "TokenFullMixin",
    "UnifiedProvider",
    "WalletTxMixin",
    "get_unified_provider",
    "record_silent_failure",
]
