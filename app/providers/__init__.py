"""Chain data providers: the unified fallback cascade, split by responsibility.

``app.unified_provider`` remains a thin re-export shim until 2026-12-01.
"""

from __future__ import annotations

from app.providers.base import CacheMixin, ConnectorsMixin, ProviderBase
from app.providers.dex_sources import DexSourcesMixin
from app.providers.enrichment import EnrichmentMixin
from app.providers.holders import HoldersMixin
from app.providers.pricing import PricingMixin
from app.providers.registry import get_unified_provider
from app.providers.token_full import TokenFullMixin
from app.providers.unified import UnifiedProvider
from app.providers.wallet_txs import WalletTxMixin

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
]
