"""Normalized live trades for any token, any supported chain.

Real swaps only. DexScreener publishes no trades endpoint — see
``service.py`` for why its aggregates are never turned into trades.
"""

from app.trades.schema import SUPPORTED_CHAINS, Trade, canon_chain
from app.trades.service import TradeResult, get_token_trades

__all__ = ["SUPPORTED_CHAINS", "Trade", "TradeResult", "canon_chain", "get_token_trades"]
