"""Shared helpers for the chat tool-use layer (``app/domains/chat_tools``).

Pure extraction / coercion helpers used by the intent classifier and the
per-tool executors. No app imports — safe to import from any submodule
without introducing an import cycle.
"""

from __future__ import annotations

import re
from typing import Any

# Mirrors the address/symbol regexes in app/routers/chat.py (kept local to
# avoid a circular import — chat.py imports the chat_tools package).
_EVM_ADDRESS_RE = re.compile(r"\b0x[a-fA-F0-9]{40}\b")
_SOLANA_ADDRESS_RE = re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b")
_SYMBOL_RE = re.compile(r"\$([A-Za-z][A-Za-z0-9]{1,10})\b")

# Natural token names → display symbol (price extraction without a $ prefix).
_TOKEN_NAME_TO_SYMBOL: dict[str, str] = {
    "bitcoin": "BTC",
    "btc": "BTC",
    "ethereum": "ETH",
    "eth": "ETH",
    "solana": "SOL",
    "sol": "SOL",
    "xrp": "XRP",
    "ripple": "XRP",
    "binance coin": "BNB",
    "bnb": "BNB",
    "cardano": "ADA",
    "ada": "ADA",
    "dogecoin": "DOGE",
    "doge": "DOGE",
    "avalanche": "AVAX",
    "avax": "AVAX",
    "polkadot": "DOT",
    "dot": "DOT",
    "polygon": "MATIC",
    "matic": "MATIC",
    "chainlink": "LINK",
    "link": "LINK",
    "shiba inu": "SHIB",
    "shib": "SHIB",
    "pepe": "PEPE",
    "tether": "USDT",
    "usdt": "USDT",
    "usd coin": "USDC",
    "usdc": "USDC",
    "litecoin": "LTC",
    "ltc": "LTC",
    "cosmos": "ATOM",
    "atom": "ATOM",
    "tron": "TRX",
    "trx": "TRX",
    "near": "NEAR",
    "aptos": "APT",
    "apt": "APT",
    "arbitrum": "ARB",
    "arb": "ARB",
    "optimism": "OP",
    "op": "OP",
    "sui": "SUI",
}


def _extract_price_symbol(message: str) -> str | None:
    """Extract a display symbol: ``$TICKER`` mention, else a known token name."""
    m = _SYMBOL_RE.search(message)
    if m:
        return m.group(1).upper()
    low = message.lower()
    for name, sym in _TOKEN_NAME_TO_SYMBOL.items():
        if re.search(rf"\b{re.escape(name)}\b", low):
            return sym
    return None


def _extract_first_address(message: str) -> str | None:
    """First EVM or Solana address in ``message``, or None."""
    m = _EVM_ADDRESS_RE.search(message)
    if m:
        return m.group(0)
    m = _SOLANA_ADDRESS_RE.search(message)
    return m.group(0) if m else None


def _chain_for_address(address: str) -> str:
    """Heuristic chain id for an address: solana base58 vs EVM 0x."""
    return "solana" if _SOLANA_ADDRESS_RE.search(address) else "ethereum"


def _to_float(v: Any) -> float | None:
    try:
        f = float(v)
        return f if f == f else None  # NaN guard
    except (TypeError, ValueError):
        return None
