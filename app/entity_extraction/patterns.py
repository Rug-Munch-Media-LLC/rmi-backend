from __future__ import annotations
r"""
Entity Extraction - Pattern Definitions
========================================
Regex patterns and keyword sets for crypto entity recognition.

Entity types:
  - evm_address      : 0x[0-9a-fA-F]{40}
  - solana_address   : [1-9A-HJ-NP-Za-km-z]{32,44}
  - token_symbol     : $[A-Z]{2,10}
  - chain_name       : ethereum, solana, base, ...
  - protocol_name    : uniswap, aave, curve, ...
  - scam_keyword     : rug pull, honeypot, flash loan attack, ...
  - tx_hash          : 0x[0-9a-f]{64}
  - ens_domain       : [a-z0-9]+\.eth
"""

import re

CHAIN_NAMES: set[str] = {
    "ethereum", "solana", "base", "bsc", "arbitrum",
    "polygon", "optimism", "avalanche", "fantom",
}

PROTOCOL_NAMES: set[str] = {
    "uniswap", "aave", "compound", "curve", "lido",
    "makerdao", "pancakeswap", "sushiswap", "raydium",
    "jupiter", "orca",
}

SCAM_KEYWORDS: set[str] = {
    "rug pull", "honeypot", "flash loan attack", "drain",
    "exploit", "hack", "phishing", "scam", "wash trading",
}

# Compiled regex patterns (tx_hash BEFORE evm_address to avoid overlap)
TX_HASH_RE = re.compile(r"\b0x[0-9a-fA-F]{64}\b")
EVM_ADDRESS_RE = re.compile(r"\b0x[0-9a-fA-F]{40}\b")
SOLANA_ADDRESS_RE = re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b")
TOKEN_SYMBOL_RE = re.compile(r"\$[A-Z]{2,10}")
ENS_DOMAIN_RE = re.compile(r"\b[a-z0-9]+\.eth\b")
