"""Wallet Monitor — Configuration & Redis connection."""
from __future__ import annotations

import os
from typing import Any

CONFIG = {
    "ALERT_TTL_SECONDS": int(os.getenv("WALLET_ALERT_TTL", "86400")),
    "WATCHLIST_KEY": "wallet:watchlist",
    "WALLET_LABELS_KEY": "wallet:labels",
    "WALLET_HISTORY_KEY_PREFIX": "wallet:history",
    "WALLET_TX_VELOCITY_WINDOW": int(os.getenv("WALLET_VELOCITY_WINDOW", "300")),
    "WALLET_BURST_THRESHOLD": int(os.getenv("WALLET_BURST_THRESHOLD", "10")),
    "LARGE_TRANSFER_USD": float(os.getenv("WALLET_LARGE_TRANSFER_USD", "10000")),
    "WEBHOOK_TIMEOUT": int(os.getenv("WEBHOOK_TIMEOUT", "10")),
}

WS_URLS = {
    "solana": os.getenv("HELIUS_WS", ""),
    "ethereum": os.getenv("ALCHEMY_ETH_WS", ""),
    "base": os.getenv("ALCHEMY_BASE_WS", ""),
    "arbitrum": os.getenv("ALCHEMY_ARB_WS", ""),
    "optimism": os.getenv("ALCHEMY_OPT_WS", ""),
}

RPC_URLS = {
    "ethereum": os.getenv("ETH_RPC", ""),
    "base": os.getenv("BASE_RPC", ""),
    "arbitrum": os.getenv("ARB_RPC", ""),
    "optimism": os.getenv("OPT_RPC", ""),
    "solana": os.getenv("SOLANA_RPC", ""),
}

SCAMMER_SET_KEY = "threatintel:blocklist"

DEX_ROUTERS = {
    "uniswap_v2": "0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D",
    "uniswap_v3": "0xE592427A0AEce92De3Edee1F18E0157C05861564",
    "sushiswap": "0xd9e1cE17f2641f24aE83637ab66a2cca9C378B9F",
    "pancakeswap": "0x10ED43C718714eb63d5aA57B78B54704E256024E",
    "raydium": "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8",
    "orca": "9W959DqEETiGZocYWCQPaJ6sBfUzHM7t2vNtdVzJ3rHc",
    "jupiter": "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4",
}

APPROVAL_TOPIC = "0x8c5be1e5ebec7d5bd14f71427d1e84f3dd0314c0f7b2291e5b200ac8c7c3b925"
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

LABEL_PATTERNS = {
    "dex_trader": {"min_dex_txs": 5, "time_window_hours": 24},
    "nft_flipper": {"min_nft_txs": 5, "time_window_hours": 24},
    "whale": {"min_transfer_usd": 500_000, "time_window_hours": 24},
    "airdrop_farmer": {"min_token_receives": 10, "time_window_hours": 24},
    "contract_deployer": {"min_contract_creates": 3, "time_window_hours": 72},
    "mixer_user": {"min_interactions_with_mixer": 2, "time_window_hours": 72},
}

# Redis
try:
    import redis.asyncio as redis_async
except ImportError:
    redis_async = None

_redis: Any = None


async def get_redis() -> Any:
    global _redis
    if _redis is None and redis_async is not None:
        try:
            _redis = redis_async.Redis(
                host=os.getenv("REDIS_HOST", "127.0.0.1"),
                port=int(os.getenv("REDIS_PORT", "6379")),
                password=os.getenv("REDIS_PASSWORD") or None,
                db=int(os.getenv("REDIS_DB", "0")),
                decode_responses=True,
            )
        except (ConnectionError, OSError):
            _redis = None
    return _redis
