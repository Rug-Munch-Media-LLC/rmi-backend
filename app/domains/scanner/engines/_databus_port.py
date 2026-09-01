"""DataBus provider port — the scanner's market-data escape hatch.

Phase 2 extraction seam: engines reach DataBus provider functions ONLY
through this module. Pre-split, the adapter binds the in-process
provider modules (fast, no HTTP). Post-split, set_port() swaps in an
HTTP client against the backend gateway — zero engine changes.

Sanctioned surface (add capabilities here as needed):
    dexscreener_price(chain, address)
    coingecko_market(chain, address)
    geckoterminal_market(chain, address)
    paraswap_quote(...)
    oneinch_quote(...)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass(frozen=True)
class _ProviderAdapter:
    _dex_price: Callable[..., Any]
    _cg_market: Callable[..., Any]
    _gt_market: Callable[..., Any]
    _paraswap: Callable[..., Any]
    _oneinch: Callable[..., Any]

    def dexscreener_price(self, **kwargs: Any) -> Any:
        # 2026-09-01: pass-through — callers use mint/token/address kwargs
        # (the underlying provider accepts all of them); the old rigid
        # (chain, address) signature raised "unexpected keyword argument"
        # and silently killed the tier.
        return self._dex_price(**kwargs)

    def coingecko_market(self, **kwargs: Any) -> Any:
        return self._cg_market(**kwargs)

    def geckoterminal_market(self, **kwargs: Any) -> Any:
        return self._gt_market(**kwargs)

    def paraswap_quote(self, *a: Any, **k: Any) -> Any:
        return self._paraswap(*a, **k)

    def oneinch_quote(self, *a: Any, **k: Any) -> Any:
        return self._oneinch(*a, **k)


_adapter: _ProviderAdapter | None = None


def default_port() -> _ProviderAdapter:
    global _adapter
    if _adapter is None:
        from app.domains.databus.providers.chains import (
            dexscreener as _ds,
            free_security as _fs,
        )

        _adapter = _ProviderAdapter(
            _dex_price=_ds._dexscreener_price,
            _cg_market=_fs._free_market_coingecko,
            _gt_market=_fs._free_market_geckoterminal,
            _paraswap=_fs._free_market_paraswap_quote,
            _oneinch=_fs._free_market_oneinch_quote,
        )
    return _adapter


def set_port(port: _ProviderAdapter) -> None:
    global _adapter
    _adapter = port


def reset_port() -> None:
    global _adapter
    _adapter = None
