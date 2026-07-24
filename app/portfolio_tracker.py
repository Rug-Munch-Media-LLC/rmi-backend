"""Stub portfolio_tracker - multi-wallet portfolio aggregation"""

from __future__ import annotations

from typing import Any


def get_portfolio(wallet: str, chain: str = "base") -> dict[str, Any]:
    """Get portfolio for a single wallet."""
    return {
        "wallet": wallet,
        "chain": chain,
        "assets": [],
        "total_value_usd": 0,
    }

def get_multi_wallet_portfolio(wallets: list[str], chain: str = "base") -> dict[str, Any]:
    """Get portfolio for multiple wallets."""
    return {
        "wallets": wallets,
        "chain": chain,
        "portfolios": [],
        "total_value_usd": 0,
    }

def get_portfolio_history(wallet: str, chain: str = "base", days: int = 7) -> dict[str, Any]:
    """Get portfolio history."""
    return {
        "wallet": wallet,
        "chain": chain,
        "days": days,
        "history": [],
    }
