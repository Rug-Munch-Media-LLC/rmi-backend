"""Per-chain trade source registry — the dependency-injection seam.

``service.get_token_trades`` receives a source map instead of resolving module
globals, so tests inject fakes and callers can substitute a source without
patching import-time bindings. Production wiring lives in ``default_sources()``
and is built at call time.

Order is per chain because the sources are per chain:
  * solana        -> geckoterminal -> solanatracker -> birdeye -> gmgn
  * eth/base/...  -> geckoterminal -> birdeye -> gmgn
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from app.trades.birdeye import fetch_birdeye_trades
from app.trades.gmgn import fetch_gmgn_trades
from app.trades.gt_pools import resolve_pool
from app.trades.gt_trades import fetch_pool_trades
from app.trades.schema import Trade
from app.trades.solanatracker import fetch_solanatracker_trades

SourceFn = Callable[[str, str, int], Awaitable[list[Trade]]]
SourceMap = dict[str, list[tuple[str, SourceFn]]]

EVM_CHAINS = ("eth", "base", "bsc", "arbitrum", "polygon", "optimism", "avalanche")


async def _gt(network: str, token: str, limit: int) -> list[Trade]:
    ref = await resolve_pool(network, token)
    if ref is None:
        return []
    return await fetch_pool_trades(
        chain=network,
        pool=ref.pool,
        token=token,
        base_token=ref.base_token,
        limit=limit,
    )


async def _st(network: str, token: str, limit: int) -> list[Trade]:
    return await fetch_solanatracker_trades(chain=network, token=token, limit=limit)


async def _be(network: str, token: str, limit: int) -> list[Trade]:
    return await fetch_birdeye_trades(chain=network, token=token, limit=limit)


async def _gm(network: str, token: str, limit: int) -> list[Trade]:
    return await fetch_gmgn_trades(chain=network, token=token, limit=limit)


def default_sources() -> SourceMap:
    """Production wiring: known chains mapped to adapters in fallback order."""
    return {
        "solana": [
            ("geckoterminal", _gt),
            ("solanatracker", _st),
            ("birdeye", _be),
            ("gmgn", _gm),
        ],
        **{net: [("geckoterminal", _gt), ("birdeye", _be), ("gmgn", _gm)] for net in EVM_CHAINS},
    }
