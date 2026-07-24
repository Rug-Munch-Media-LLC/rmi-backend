#!/usr/bin/env python3
"""
RugMunchBot -- Scanning engines.

Token security scanner and wallet forensics analyzer.
Both functions fetch data from multiple sources and return structured results.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.logging import get_logger
from app.domains.telegram.rugmunchbot.config import (
    BACKEND_URL,
    DEXSCREENER,
    GOPLUS,
    HONEYPOT,
)
from app.domains.telegram.rugmunchbot.formatters import detect_chain, is_evm, is_sol
from app.domains.telegram.rugmunchbot.states import get_sol_price

logger = get_logger("rmi_bot")

async def scan_token(address: str, chain: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "address": address,
        "chain": chain or detect_chain(address),
        "name": "Unknown",
        "symbol": "???",
        "price": 0,
        "mcap": 0,
        "fdv": 0,
        "volume_24h": 0,
        "liquidity": 0,
        "price_change_1h": 0,
        "price_change_24h": 0,
        "price_change_7d": 0,
        "holders": 0,
        "pair_address": "",
        "dex": "",
        "created_at": None,
        "risk_score": 0,
        "threats": [],
        "contract_verified": None,
        "honeypot": None,
        "buy_tax": None,
        "sell_tax": None,
        "lp_locked": None,
        "mintable": None,
        "can_blacklist": None,
        "owner_renounced": None,
        "top_holders": [],
        "bundle_detected": False,
        "fresh_wallet_pct": 0,
        "exchange_pct": 0,
        "volume_real_ratio": None,
        "dev_wallet": None,
        "dev_other_tokens": [],
        "errors": [],
        "socials": {},
    }

    async with httpx.AsyncClient(timeout=15) as client:
        # DexScreener
        try:
            r = await client.get(f"{DEXSCREENER}/tokens/{address}")
            if r.status_code == 200:
                data = r.json()
                pairs = data.get("pairs", [])
                if pairs:
                    p = pairs[0]
                    result["name"] = p.get("baseToken", {}).get("name", "Unknown")
                    result["symbol"] = p.get("baseToken", {}).get("symbol", "???")
                    result["price"] = float(p.get("priceUsd", 0))
                    result["mcap"] = float(p.get("marketCap") or p.get("fdv") or 0)
                    result["fdv"] = float(p.get("fdv") or result["mcap"])
                    vol = p.get("volume", {})
                    result["volume_24h"] = float(vol.get("h24") or 0)
                    liq = p.get("liquidity", {})
                    result["liquidity"] = float(liq.get("usd") or 0)
                    pc = p.get("priceChange", {})
                    result["price_change_1h"] = pc.get("h1") or 0
                    result["price_change_24h"] = pc.get("h24") or 0
                    result["price_change_7d"] = pc.get("d7") or 0
                    result["pair_address"] = p.get("pairAddress", "")
                    result["dex"] = p.get("dexId", "")
                    result["chain"] = p.get("chainId", result["chain"])
                    result["created_at"] = p.get("pairCreatedAt")
                    socials = p.get("info", {}).get("socials", [])
                    result["socials"] = {s.get("type"): s.get("url") for s in socials}
        except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
            result["errors"].append(f"DexScreener: {e}")

        # GoPlus Security (EVM)
        if is_evm(address):
            try:
                chain_id_map = {
                    "ethereum": "1", "bsc": "56", "polygon": "137",
                    "arbitrum": "42161", "avalanche": "43114", "base": "8453",
                    "optimism": "10", "fantom": "250",
                }
                cid = chain_id_map.get(result["chain"].lower(), "1")
                r = await client.get(f"{GOPLUS}/token_security/{cid}?contract_addresses={address}")
                if r.status_code == 200:
                    sec = r.json().get("result", {}).get(address.lower(), {})
                    if sec:
                        result["contract_verified"] = sec.get("is_open_source") == "1"
                        result["honeypot"] = sec.get("is_honeypot") == "1"
                        result["buy_tax"] = float(sec.get("buy_tax") or 0)
                        result["sell_tax"] = float(sec.get("sell_tax") or 0)
                        result["mintable"] = sec.get("is_mintable") == "1"
                        result["can_blacklist"] = (
                            sec.get("is_blacklisted") == "1"
                            or sec.get("can_take_back_ownership") == "1"
                        )
                        result["owner_renounced"] = sec.get("is_owner_renounced") != "0"
                        lp_locks = sec.get("lp_holders", [])
                        locked_pct = sum(
                            float(h.get("percent", 0)) for h in lp_locks if h.get("is_locked") == 1
                        )
                        result["lp_locked"] = locked_pct
                        holders_data = sec.get("holders", [])
                        result["holders"] = len(holders_data) if holders_data else 0
                        for h in holders_data[:10]:
                            result["top_holders"].append({
                                "address": h.get("address", ""),
                                "pct": float(h.get("percent") or 0) * 100,
                                "is_contract": h.get("is_contract") == 1,
                                "tag": h.get("tag", ""),
                            })
                        if result["honeypot"]:
                            result["threats"].append("HONEYPOT DETECTED")
                            result["risk_score"] += 40
                        if not result["contract_verified"]:
                            result["threats"].append("Contract not verified")
                            result["risk_score"] += 15
                        if result["mintable"]:
                            result["threats"].append("Mintable supply")
                            result["risk_score"] += 20
                        if result["can_blacklist"]:
                            result["threats"].append("Owner can blacklist")
                            result["risk_score"] += 15
                        if not result["owner_renounced"]:
                            result["threats"].append("Ownership not renounced")
                            result["risk_score"] += 10
                        if result["buy_tax"] and result["buy_tax"] > 0.1:
                            result["threats"].append(f"High buy tax: {result['buy_tax'] * 100:.1f}%")
                            result["risk_score"] += 10
                        if result["sell_tax"] and result["sell_tax"] > 0.1:
                            result["threats"].append(f"High sell tax: {result['sell_tax'] * 100:.1f}%")
                            result["risk_score"] += 15
                        if locked_pct < 0.5 and result["mcap"] > 10000:
                            result["threats"].append(f"Low LP lock: {locked_pct * 100:.0f}%")
                            result["risk_score"] += 15
            except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
                result["errors"].append(f"GoPlus: {e}")

        # Honeypot.is (EVM fallback)
        if is_evm(address) and result["honeypot"] is None:
            try:
                r = await client.get(f"{HONEYPOT}/IsHoneypot?address={address}")
                if r.status_code == 200:
                    hp = r.json()
                    result["honeypot"] = hp.get("isHoneypot", False)
                    if result["honeypot"]:
                        result["threats"].append("HONEYPOT (honeypot.is)")
                        result["risk_score"] += 40
            except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
                result["errors"].append(f"Honeypot.is: {e}")

        # RMI Backend
        try:
            r = await client.get(f"{BACKEND_URL}/api/v1/databus/token/{address}", timeout=10)
            if r.status_code == 200:
                rmi = r.json()
                if rmi.get("bundle_detected"):
                    result["bundle_detected"] = True
                    result["threats"].append("Bundle activity detected")
                    result["risk_score"] += 20
                if rmi.get("fresh_wallet_pct"):
                    result["fresh_wallet_pct"] = rmi["fresh_wallet_pct"]
                    if rmi["fresh_wallet_pct"] > 50:
                        result["threats"].append(f"High fresh wallet ratio: {rmi['fresh_wallet_pct']:.0f}%")
                        result["risk_score"] += 10
                if rmi.get("dev_wallet"):
                    result["dev_wallet"] = rmi["dev_wallet"]
        except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
            result["errors"].append(f"RMI Backend: {e}")

    # Volume authenticity
    if result["volume_24h"] > 0 and result["mcap"] > 0:
        ratio = result["volume_24h"] / result["mcap"]
        result["volume_real_ratio"] = ratio
        if ratio > 5:
            result["threats"].append(f"Suspicious volume/mcap ratio: {ratio:.1f}x")
            result["risk_score"] += 15

    # Liquidity check
    if result["mcap"] > 0 and result["liquidity"] > 0:
        liq_ratio = result["liquidity"] / result["mcap"]
        if liq_ratio < 0.02:
            result["threats"].append("Very low liquidity vs mcap")
            result["risk_score"] += 20

    result["risk_score"] = min(result["risk_score"], 100)
    return result

async def analyze_wallet(address: str, chain: str | None = None) -> dict:
    result = {
        "address": address,
        "chain": chain or detect_chain(address),
        "balance_usd": 0,
        "tx_count": 0,
        "first_seen": None,
        "wallet_age_days": 0,
        "top_tokens": [],
        "is_fresh": False,
        "is_exchange": False,
        "is_contract": False,
        "risk_score": 0,
        "flags": [],
        "recent_txs": [],
        "funding_source": None,
        "connected_wallets": [],
        "pnl_estimate": None,
        "errors": [],
    }
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            r = await client.get(f"{BACKEND_URL}/api/v1/databus/wallet/{address}", timeout=10)
            if r.status_code == 200:
                data = r.json()
                for k in [
                    "balance_usd", "tx_count", "first_seen", "is_exchange",
                    "is_contract", "risk_score", "flags", "funding_source", "pnl_estimate",
                ]:
                    if k in data:
                        result[k] = data[k]
                result["top_tokens"] = data.get("top_tokens", [])[:10]
                result["recent_txs"] = data.get("recent_txs", [])[:5]
                result["connected_wallets"] = data.get("connected_wallets", [])[:5]
                if result["first_seen"]:
                    try:
                        first = datetime.fromisoformat(result["first_seen"].replace("Z", "+00:00"))
                        result["wallet_age_days"] = (datetime.now(UTC) - first).days
                        result["is_fresh"] = result["wallet_age_days"] < 7
                    except (httpx.HTTPError, ValueError, TypeError, KeyError) as e :
                        logger.debug("analyze_wallet_error", exc_info=True)
                        logger.warning(f"Failed to parse first_seen for {address}: {e}")
                return result
        except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
            logger.warning(f"analyze_wallet RMI backend failed for {address}: {e}")
        if is_sol(address):
            try:
                r = await client.get(f"https://api.solana.fm/v0/accounts/{address}")
                if r.status_code == 200:
                    data = r.json()
                    sol_price = await get_sol_price()
                    result["balance_usd"] = float(data.get("balance", 0)) / 1e9 * sol_price
                    result["tx_count"] = data.get("transaction_count", 0)
            except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
                result["errors"].append(f"Solana.fm: {e}")
    return result
