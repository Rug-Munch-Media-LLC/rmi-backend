"""Normalized trade schema shared by every live-trades source.

One canonical shape so REST, WS and the frontend never branch on provider.
Supports three onchain naming conventions (chain/network, tx/tx_hash/signature).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.core.logging import get_logger
from app.trades.numeric import as_number

logger = get_logger(__name__)

# Chains we serve. GeckoTerminal network ids (note: eth, not ethereum).
SUPPORTED_CHAINS = frozenset({"solana", "eth", "base", "bsc", "arbitrum", "polygon", "optimism", "avalanche"})

# Provider-specific network naming -> GeckoTerminal network id.
CHAIN_ALIASES = {
    "ethereum": "eth",
    "ether": "eth",
    "sol": "solana",
    "binance": "bsc",
    "binance-smart-chain": "bsc",
    "bnb": "bsc",
    "arb": "arbitrum",
    "matic": "polygon",
    "optimism": "optimism",
    "avax": "avalanche",
}


class Trade(BaseModel):
    """One real onchain swap. Every field is sourced, never synthesized."""

    chain: str
    token: str
    tx: str
    side: str = Field(pattern="^(buy|sell)$")
    amount_token: float = 0.0
    amount_usd: float = 0.0
    price_usd: float = 0.0
    wallet: str = ""
    maker: str = ""
    timestamp: str = ""
    timestamp_ms: int = 0
    pool: str = ""
    source: str = ""
    is_whale: bool = False


def canon_chain(chain: str) -> str:
    """Map a caller's chain label onto the provider network id."""
    c = (chain or "").strip().lower()
    return CHAIN_ALIASES.get(c, c)


def normalize_geckoterminal_trade(
    row: dict[str, Any],
    *,
    chain: str,
    token: str,
    pool: str,
    base_token: str,
) -> Trade | None:
    """Map one GeckoTerminal trade row to the canonical schema.

    Returns None when the row carries no tx hash — an unaddressable trade is
    unusable and must never be emitted.
    """
    a = row.get("attributes") or {}
    tx = a.get("tx_hash") or ""
    if not tx:
        return None

    token_l = token.lower()
    from_tok = (a.get("from_token_address") or "").lower()
    # GT ``kind`` is relative to the pool's base token; invert for quote buyers.
    wants_base = token_l == (base_token or "").lower()
    kind = (a.get("kind") or "").lower()
    if wants_base or not base_token:
        side = kind
        amount_token = as_number(a.get("from_token_amount")) if from_tok == token_l else as_number(a.get("to_token_amount"))
    else:
        side = "buy" if kind == "sell" else "sell"
        amount_token = as_number(a.get("to_token_amount")) if from_tok != token_l else as_number(a.get("from_token_amount"))
    if side not in ("buy", "sell"):
        return None

    ts = a.get("block_timestamp") or ""
    wallet = a.get("tx_from_address") or ""
    return Trade(
        chain=chain,
        token=token,
        tx=tx,
        side=side,
        amount_token=amount_token,
        amount_usd=as_number(a.get("volume_in_usd")),
        price_usd=as_number(a.get("price_from_in_usd")) if from_tok == token_l else as_number(a.get("price_to_in_usd")),
        wallet=wallet,
        maker=wallet,
        timestamp=ts,
        timestamp_ms=_iso_to_ms(ts),
        pool=pool,
        source="geckoterminal",
    )


def _iso_to_ms(ts: str) -> int:
    from datetime import datetime

    try:
        return int(datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp() * 1000)
    except (TypeError, ValueError) as exc:
        logger.debug("trade_timestamp_unparsable", error=type(exc).__name__)
        return 0
