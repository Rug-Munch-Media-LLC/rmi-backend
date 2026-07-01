"""
RMI Earnings Tracker — Monitor all payment wallets and revenue sources.

Tracks x402 payment wallets across chains, fetches balances,
logs earnings by tool/chain/facilitator, and provides dashboards.

Payment wallets:
  Base:    0x1E3AC01d0fdb976179790BDD02823196A92705C9
  Solana:  From gateway config (solana worker)
"""

import logging
import os
from datetime import UTC, datetime

import httpx

logger = logging.getLogger("earnings")

# Payment wallets we track
PAYMENT_WALLETS = {
    "base": {
        "address": "0x1E3AC01d0fdb976179790BDD02823196A92705C9",
        "chain": "base",
        "chain_id": 8453,
        "token": "USDC",
        "gateway": "x402-base",
    },
    "solana": {
        "address": os.getenv("X402_SOLANA_WALLET", "PAY_TO_SOLANA_NOT_SET"),
        "chain": "solana",
        "token": "USDC",
        "gateway": "x402-sol",
    },
}

# Revenue by source tracking
_revenue_log: list[dict] = []
_earnings_cache: dict = {"updated": 0, "data": {}}


async def fetch_wallet_earnings() -> dict:
    """Fetch current balances and recent transactions for all payment wallets."""
    results = {}
    total_usd = 0.0

    for name, wallet in PAYMENT_WALLETS.items():
        if wallet["chain"] == "solana":
            balance = await _fetch_solana_balance(wallet["address"])
        else:
            balance = await _fetch_evm_balance(wallet["address"], wallet["chain_id"])

        results[name] = {
            "address": wallet["address"][:10] + "...",
            "chain": wallet["chain"],
            "token": wallet["token"],
            "balance": balance.get("balance", 0),
            "balance_usd": balance.get("balance_usd", 0),
            "transactions": balance.get("recent_txs", 0),
        }
        total_usd += balance.get("balance_usd", 0)

    results["total_usd"] = round(total_usd, 2)
    results["updated"] = datetime.now(UTC).isoformat()
    return results


async def _fetch_evm_balance(address: str, chain_id: int) -> dict:
    """Fetch EVM wallet balance via Blockscout."""
    key = os.getenv("BLOCKSCOUT_API_KEY", "")
    if not key:
        return {"balance": 0, "balance_usd": 0}

    try:
        async with httpx.AsyncClient(timeout=15) as c:
            # Get native balance
            r = await c.get(
                f"https://api.blockscout.com/{chain_id}/api/v2/addresses/{address}",
                params={},
                headers={"Accept": "application/json"},
            )
            if r.status_code == 200:
                data = r.json()
                coin_balance = float(data.get("coin_balance", 0)) / 1e18 if data.get("coin_balance") else 0

                # Get recent transactions count
                r2 = await c.get(
                    f"https://api.blockscout.com/{chain_id}/api/v2/addresses/{address}/transactions",
                    params={"filter": "to"},
                    headers={"Accept": "application/json"},
                )
                tx_count = len(r2.json().get("items", [])) if r2.status_code == 200 else 0

                return {
                    "balance": coin_balance,
                    "balance_usd": round(coin_balance * 2500, 2),  # ETH ~$2500
                    "recent_txs": tx_count,
                }
    except Exception as e:
        logger.debug(f"EVM balance fetch failed: {e}")

    return {"balance": 0, "balance_usd": 0}


async def _fetch_solana_balance(address: str) -> dict:
    """Fetch Solana wallet balance via Helius."""
    key = os.getenv("HELIUS_API_KEY", "")
    if not key or address == "PAY_TO_SOLANA_NOT_SET":
        return {"balance": 0, "balance_usd": 0}

    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(
                f"https://mainnet.helius-rpc.com/?api-key={key}",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getBalance",
                    "params": [address],
                },
            )
            if r.status_code == 200:
                sol_balance = r.json().get("result", {}).get("value", 0) / 1e9
                # Approximate SOL price
                return {
                    "balance": round(sol_balance, 4),
                    "balance_usd": round(sol_balance * 80, 2),
                }
    except Exception as e:
        logger.debug(f"Solana balance fetch failed: {e}")

    return {"balance": 0, "balance_usd": 0}


def log_revenue(tool_id: str, amount_usd: float, chain: str, facilitator: str):
    """Log a revenue event for tracking by source."""
    _revenue_log.append(
        {
            "tool": tool_id,
            "amount_usd": amount_usd,
            "chain": chain,
            "facilitator": facilitator,
            "timestamp": datetime.now(UTC).isoformat(),
        }
    )
    # Keep last 1000 events
    if len(_revenue_log) > 1000:
        _revenue_log.pop(0)


def get_revenue_by_source() -> dict:
    """Aggregate revenue by tool, chain, and facilitator."""
    by_tool = {}
    by_chain = {}
    by_facilitator = {}
    total = 0.0

    for event in _revenue_log:
        amt = event["amount_usd"]
        total += amt
        by_tool[event["tool"]] = by_tool.get(event["tool"], 0) + amt
        by_chain[event["chain"]] = by_chain.get(event["chain"], 0) + amt
        by_facilitator[event["facilitator"]] = by_facilitator.get(event["facilitator"], 0) + amt

    return {
        "total": round(total, 4),
        "events": len(_revenue_log),
        "by_tool": dict(sorted(by_tool.items(), key=lambda x: x[1], reverse=True)[:10]),
        "by_chain": by_chain,
        "by_facilitator": by_facilitator,
    }


def get_earnings_report() -> dict:
    """Full earnings report for the dashboard."""
    return {
        "wallets": PAYMENT_WALLETS,
        "revenue_by_source": get_revenue_by_source(),
        "earnings_history": _revenue_log[-20:],  # Last 20 events
        "updated": datetime.now(UTC).isoformat(),
    }
