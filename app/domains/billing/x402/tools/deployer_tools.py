"""x402 deployer-side tools — insider tracker, whale accumulation detector.

Phase 3A of AUDIT-2026-Q3.md (split from app/routers/x402_tools.py).

Endpoints mounted on sub_router:
  POST /api/v1/x402-tools/insider
  POST /api/v1/x402-tools/whale_accumulation

Wallet-side tools (wallet, smartmoney, cluster, whale, portfolio_tracker,
copy_trade_finder) live in tools/wallet_tools.py.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException

from app.domains.billing.x402.shared import (
    GenericRequest,
    InsiderRequest,
    fetch_with_fallback,
    record_x402_payment,
    rpc_call,
)

sub_router = APIRouter(tags=["x402-deployer-tools"])

# ═══════════════════════════════════════════════════════════════
# TOOL 8: Insider Tracker ($1.50)
# ═══════════════════════════════════════════════════════════════

async def _insider_tracking(creator: str, chain: str) -> dict:
    """Track creator wallet activity across all tokens."""
    result = {"chain": chain, "creator_address": creator, "sources_used": []}

    # Layer 1: DexScreener - find all tokens by this creator
    try:
        data, _ = await fetch_with_fallback(
            [f"https://api.dexscreener.com/latest/dex/search?q={creator}"], timeout=8
        )
        if data and data.get("pairs"):
            tokens = {}
            for pair in data["pairs"]:
                addr = pair.get("baseToken", {}).get("address")
                if addr and addr not in tokens:
                    tokens[addr] = {
                        "symbol": pair.get("baseToken", {}).get("symbol"),
                        "price": pair.get("priceUsd", 0),
                        "liquidity": pair.get("liquidity", {}).get("usd", 0),
                    }
            result["associated_tokens"] = tokens
            result["token_count"] = len(tokens)
            result["sources_used"].append("dexscreener")
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException):
        pass

    # Layer 2: Solana RPC - wallet activity
    if chain == "solana":
        try:
            balance = await rpc_call("solana", "getBalance", [creator])
            if balance is not None:
                result["creator_balance_sol"] = balance / 1e9
                result["sources_used"].append("solana_balance")
        except (httpx.HTTPError):
            pass

        try:
            sigs = await rpc_call("solana", "getSignaturesForAddress", [creator, {"limit": 50}])
            if sigs:
                result["recent_txs"] = len(sigs)
                result["sources_used"].append("solana_txs")
        except (ConnectionError, OSError, TimeoutError):
            pass

    # Layer 3: Etherscan/BaseScan
    if chain in ["base", "ethereum"]:
        try:
            explorer = "basescan.org" if chain == "base" else "etherscan.io"
            key = os.getenv("BASESCAN_KEY" if chain == "base" else "ETHERSCAN_KEY", "")
            if key:
                data, _ = await fetch_with_fallback(
                    [
                        f"https://api.{explorer}/api?module=account&action=txlist&address={creator}&startblock=0&endblock=99999999&page=1&offset=10&sort=desc&apikey={key}"
                    ]
                )
                if data and data.get("result"):
                    result["explorer_txs"] = len(data["result"])
                    result["sources_used"].append(f"{chain}_explorer")
        except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException):
            pass

    # Risk assessment
    risk = "low"
    if result.get("token_count", 0) > 10:
        risk = "high"
        result["flags"] = ["serial_deployer"]
    elif result.get("token_count", 0) > 5:
        risk = "medium"
        result["flags"] = ["multiple_deployments"]
    elif result.get("recent_txs", 0) > 100:
        risk = "medium"
        result["flags"] = ["high_activity"]
    else:
        result["flags"] = []

    result["risk_level"] = risk
    return result

@sub_router.post("/insider")
async def insider_tracker(req: InsiderRequest) -> dict[str, str]:
    """Dev/team wallet tracking across all their tokens."""
    try:
        result = await _insider_tracking(req.creator_address, req.chain)
        await record_x402_payment("insider", "0.10", req.creator_address)
        return {
            "tool": "Insider Tracker",
            "version": "2.0",
            "timestamp": datetime.now(UTC).isoformat(),
            **result,
            "guarantee": "Data delivered or auto-refund via x402 receipt",
        }
    except (ValueError, TypeError) as e :
        raise HTTPException(status_code=500, detail=str(e)) from e

# ═══════════════════════════════════════════════════════════════
# TOOL 41: Whale Accumulation Pattern Detector ($0.10)
# ═══════════════════════════════════════════════════════════════

@sub_router.post("/whale_accumulation")
async def whale_accumulation(req: GenericRequest) -> dict[str, str]:
    """Detect stealth accumulation by large holders before price impact.
    Identifies quiet buying patterns, OTC accumulation signals, and wallet
    funding sequences that precede major positions.
    """
    try:
        from app.whale_accumulation import detect_accumulation

        address = req.address or req.token or req.query or ""
        if not address:
            raise HTTPException(status_code=400, detail="Provide token address")

        result = await detect_accumulation(address, req.chain)
        await record_x402_payment("whale_accumulation", "0.10", address)
        return {
            "tool": "Whale Accumulation Detector",
            "version": "1.0",
            "timestamp": datetime.now(UTC).isoformat(),
            **result,
            "guarantee": "Data delivered or auto-refund via x402 receipt",
        }
    except HTTPException:
        raise
    except (ValueError, TypeError) as e :
        raise HTTPException(status_code=500, detail=str(e)) from e
