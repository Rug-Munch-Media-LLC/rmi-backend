"""
Multi-chain portfolio scanner.
Scans the same wallet address across all supported chains.
"""

from __future__ import annotations
import time

from app.adapters.binance_web3 import CHAIN_IDS, CHAIN_NAMES, get_wallet_holdings
from app.analyzer.portfolio import build_portfolio

SCAN_DELAY = 0.3  # seconds between chain requests

def scan_all_chains(address: str) -> dict:
    """
    Scan a wallet across all supported chains.

    Returns:
        {
          "chains": {
            "BSC": {"total_value": float, "token_count": int, "change_24h_pct": float},
            ...
          },
          "grand_total": float,
          "grand_change_24h_usd": float,
          "grand_change_24h_pct": float,
          "errors": [...],
        }
    """
    result = {
        "chains": {},
        "grand_total": 0.0,
        "grand_change_24h_usd": 0.0,
        "grand_change_24h_pct": 0.0,
        "errors": [],
    }

    grand_yesterday = 0.0

    for chain_key, chain_id in CHAIN_IDS.items():
        try:
            holdings = get_wallet_holdings(address, chain_id)
            portfolio = build_portfolio(holdings)

            if portfolio["total_value"] > 0:
                chain_name = CHAIN_NAMES.get(chain_id, chain_key.upper())
                result["chains"][chain_name] = {
                    "total_value": portfolio["total_value"],
                    "token_count": portfolio["token_count"],
                    "change_24h_usd": portfolio["change_24h_usd"],
                    "change_24h_pct": portfolio["change_24h_pct"],
                }
                result["grand_total"] += portfolio["total_value"]
                result["grand_change_24h_usd"] += portfolio["change_24h_usd"]
                grand_yesterday += portfolio["total_value"] - portfolio["change_24h_usd"]

            time.sleep(SCAN_DELAY)

        except Exception as e:
            result["errors"].append(f"{chain_key.upper()}: {e}")

    if grand_yesterday > 0:
        result["grand_change_24h_pct"] = (result["grand_change_24h_usd"] / grand_yesterday) * 100

    return result
