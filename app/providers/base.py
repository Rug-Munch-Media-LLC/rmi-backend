"""Shared plumbing for the unified provider mixins.

Extracted verbatim from ``app/unified_provider.py``: the lazily constructed
third-party connectors and the chain-cache accessor. One client per instance,
built on first use only — importing this module has no side effects.
"""

from __future__ import annotations

from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)


class ConnectorsMixin:
    """Lazy connector construction; an unavailable client degrades to ``None``.

    A connector slot holds ``None`` (not built yet), the client, or ``False``
    once a failed import has been recorded.
    """

    _birdeye: Any
    _solscan: Any
    _gmgn: Any

    def _init_connectors(self) -> None:
        self._birdeye = None
        self._solscan = None
        self._gmgn = None

    @property
    def birdeye(self) -> Any:
        if self._birdeye is None:
            try:
                from app.birdeye_client import BirdeyeClient

                self._birdeye = BirdeyeClient()
            except ImportError:
                self._birdeye = False
        return self._birdeye if self._birdeye is not False else None

    @property
    def solscan(self) -> Any:
        if self._solscan is None:
            try:
                from app.free_solscan_client import FreeSolscanClient

                self._solscan = FreeSolscanClient()
            except ImportError:
                self._solscan = False
        return self._solscan if self._solscan is not False else None

    @property
    def gmgn(self) -> Any:
        if self._gmgn is None:
            try:
                from app.integrations.gmgn_client import GMGNClient

                self._gmgn = GMGNClient()
            except ImportError:
                self._gmgn = False
        return self._gmgn if self._gmgn is not False else None


class CacheMixin:
    """Chain-cache accessor shared by every provider mixin."""

    def _get_cache(self) -> Any:
        from app.chain_cache import get_chain_cache

        return get_chain_cache()


class ProviderBase(ConnectorsMixin, CacheMixin):
    """Common base for the feature mixins; no behaviour of its own."""
