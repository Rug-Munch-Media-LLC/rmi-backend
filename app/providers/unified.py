"""The composed :class:`UnifiedProvider` — one class, one instance, no duplicated state."""

from __future__ import annotations

from app.providers.holders import HoldersMixin
from app.providers.token_full import TokenFullMixin
from app.providers.wallet_txs import WalletTxMixin


class UnifiedProvider(HoldersMixin, WalletTxMixin, TokenFullMixin):
    """Multi-source chain data with fallback cascade.

    ``TokenFullMixin`` already composes pricing, DEX sources and enrichment, so
    the MRO covers every provider responsibility exactly once.
    """

    def __init__(self) -> None:
        self._init_connectors()
