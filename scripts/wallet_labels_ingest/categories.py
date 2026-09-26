#!/usr/bin/env python3
"""Keyword → label-category inference shared by every enrichment source."""

from __future__ import annotations

__all__ = ["infer_label_category"]

# Ordered most-specific first: the first matching group wins.
CATEGORY_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("exchange", ("exchange", "cex", "binance", "coinbase", "kraken", "okx")),
    ("scam", ("scam", "phish", "hack", "exploit", "fraud", "rug", "drainer", "malicious")),
    ("mixer", ("mixer", "tornado", "wasabi", "coinjoin")),
    ("sanctioned", ("sanction", "ofac")),
    ("defi", ("defi", "aave", "uniswap", "curve", "compound", "lido", "maker")),
    ("dex", ("dex", "swap", "lp", "liquidity")),
    ("bridge", ("bridge", "hop", "across", "synapse", "stargate", "wormhole")),
    ("mev", ("mev", "bot", "flashbot", "sandwich", "searcher")),
    ("multisig", ("multisig", "gnosis", "safe")),
    ("defi_admin", ("admin", "timelock", "governance", "treasury")),
    ("mining_pool", ("mining", "pool", "miner")),
    ("gambling", ("gambling", "casino", "bet")),
    ("ransomware", ("ransomware", "ransom")),
    ("ponzi", ("ponzi", "pyramid")),
)


def infer_label_category(text: str) -> str:
    """Infer a label category from free-form text; defaults to ``wallet``/``entity``."""
    if not text:
        return "wallet"
    t = text.lower().strip()
    for category, words in CATEGORY_KEYWORDS:
        if any(w in t for w in words):
            return category
    return "entity"
