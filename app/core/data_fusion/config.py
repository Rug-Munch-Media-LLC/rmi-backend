"""
Data Fusion — Confidence Weights & Configuration
"""

from __future__ import annotations

# ── Confidence Weights ─────────────────────────────────────────────
SOURCE_WEIGHTS: dict[str, float] = {
    "goplus": 0.40,
    "goplus_security": 0.40,
    "consensus_rpc": 0.50,
    "helius_rpc": 0.45,
    "solana_rpc": 0.45,
    "alchemy_rpc": 0.45,
    "evm_rpc": 0.45,
    "etherscan": 0.30,
    "blockscout": 0.30,
    "solscan": 0.30,
    "solanatracker": 0.35,
    "arkham": 0.50,
    "moralis": 0.40,
    "bitquery": 0.40,
    "dexscreener": 0.25,
    "birdeye": 0.30,
    "coingecko": 0.35,
    "defillama": 0.30,
    "jupiter": 0.30,
    "mobula": 0.15,
    "sentinel_scanner": 0.45,
    "wallet_memory_bank": 0.40,
    "local_price_cache": 0.35,
    "local_rag": 0.30,
    "honeypot_is": 0.35,
    "rugpull_detector": 0.35,
    "governance_attack": 0.30,
    "flash_loan_detector": 0.30,
}

SOURCE_TTL: dict[str, int] = {
    "goplus": 300,
    "dexscreener": 60,
    "coingecko": 60,
    "birdeye": 60,
    "jupiter": 30,
    "consensus_rpc": 10,
    "helius_rpc": 10,
    "solana_rpc": 10,
    "arkham": 3600,
    "etherscan": 120,
    "local_price_cache": 30,
    "sentinel_scanner": 300,
    "wallet_memory_bank": 3600,
}

IQR_MULTIPLIER = 1.5
MAX_OUTLIER_PERCENTAGE = 0.3
