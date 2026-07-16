"""x402 token-side tools — risk, forensics, pulse, honeypot, rug predictor.

Phase 3A of AUDIT-2026-Q3.md (split from app/routers/x402_tools.py).

Endpoints mounted on sub_router:
  POST /api/v1/x402-tools/audit
  POST /api/v1/x402-tools/rugshield
  POST /api/v1/x402-tools/forensics
  POST /api/v1/x402-tools/pulse
  POST /api/v1/x402-tools/honeypot_check
  POST /api/v1/x402-tools/risk_monitor
  POST /api/v1/x402-tools/rug_pull_predictor
  POST /api/v1/x402-tools/liquidity_flow
  POST /api/v1/x402-tools/token_deep_dive
  POST /api/v1/x402-tools/token_comparison
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException

from app.domains.billing.x402.shared import (
    GenericRequest,
    MultiTokenRequest,
    TokenRequest,
    _audit_evm,
    _audit_solana,
    fetch_with_fallback,
    record_x402_payment,
    rpc_call,
)

sub_router = APIRouter(tags=["x402-token-tools"])


# ═══════════════════════════════════════════════════════════════
# TOOL 1: Deep Contract Audit ($0.50)
# ═══════════════════════════════════════════════════════════════


@sub_router.post("/audit")
async def deep_contract_audit(req: TokenRequest):
    """Full 100-point forensic scan. Returns risk score 0-100 with findings."""
    try:
        # Try primary first
        if req.chain == "solana":
            result = await _audit_solana(req.address)
        else:
            result = await _audit_evm(req.address, req.chain)

        # If primary returned no data, use full fallback chain
        if not result.get("sources_used"):
            from app.auth import get_redis as _get_redis
            from app.fallback_engine import try_all_fallbacks

            r = await _get_redis()
            fallback = await try_all_fallbacks(
                "audit", address=req.address, chain=req.chain, redis=r
            )
            result.update(fallback)
            result["data_source"] = "fallback"

        await record_x402_payment("audit", "0.05", req.address)
        return {
            "tool": "Deep Contract Audit",
            "version": "2.0",
            "timestamp": datetime.now(UTC).isoformat(),
            **result,
            "guarantee": "Data delivered or auto-refund via x402 receipt",
        }
    except Exception as e:
        # Last resort: try fallback before raising error
        try:
            from app.auth import get_redis as _get_redis
            from app.fallback_engine import try_all_fallbacks

            r = await _get_redis()
            fallback = await try_all_fallbacks(
                "audit", address=req.address, chain=req.chain, redis=r
            )
            if fallback.get("fallback_layer") != "none":
                await record_x402_payment("audit", "0.05", req.address)
                return {
                    "tool": "Deep Contract Audit",
                    "version": "2.0",
                    "timestamp": datetime.now(UTC).isoformat(),
                    **fallback,
                    "recovered_from_error": str(e),
                    "guarantee": "Data delivered or auto-refund via x402 receipt",
                }
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=str(e)) from e


# ═══════════════════════════════════════════════════════════════
# TOOL 5: Rug Shield ($0.25)
# ═══════════════════════════════════════════════════════════════


async def _rug_shield(address: str, chain: str) -> dict:
    """Quick pre-buy safety check - fast binary verdict."""
    result = {"chain": chain, "address": address, "sources_used": []}
    risk_score = 0
    flags = []

    # Layer 1: DexScreener (fastest)
    try:
        data, _ = await fetch_with_fallback(
            [f"https://api.dexscreener.com/latest/dex/tokens/{address}"], timeout=5
        )
        if data and data.get("pairs"):
            pair = data["pairs"][0]
            liq = pair.get("liquidity", {}).get("usd", 0)
            vol = pair.get("volume", {}).get("h24", 0)

            if liq < 1000:
                risk_score += 30
                flags.append("low_liq")
            if liq < 100:
                risk_score += 20
                flags.append("micro_liq")

            buyers = pair.get("txns", {}).get("h24", {}).get("buys", 0)
            sellers = pair.get("txns", {}).get("h24", {}).get("sells", 0)
            if sellers > buyers * 3:
                risk_score += 25
                flags.append("heavy_selling")

            result["price_usd"] = pair.get("priceUsd", 0)
            result["liquidity"] = liq
            result["volume_24h"] = vol
            result["sources_used"].append("dexscreener")
        else:
            risk_score += 40
            flags.append("no_dex_data")
    except Exception:
        risk_score += 20
        flags.append("dex_unavailable")

    # Layer 2: Solana RPC - quick account check
    if chain == "solana" and risk_score < 60:
        try:
            account = await rpc_call("solana", "getAccountInfo", [address, {"encoding": "base58"}])
            if not account or not account.get("value"):
                risk_score += 30
                flags.append("no_account")
            result["sources_used"].append("solana_rpc")
        except Exception:
            pass

    # Layer 3: CoinGecko verification
    try:
        data, _ = await fetch_with_fallback(
            [f"https://api.coingecko.com/api/v3/coins/{address}"], timeout=5
        )
        if data:
            result["coingecko_verified"] = True
            result["sources_used"].append("coingecko")
        else:
            result["coingecko_verified"] = False
    except Exception:
        pass

    risk_score = min(100, risk_score)
    verdict = "UNSAFE" if risk_score >= 60 else "CAUTION" if risk_score >= 30 else "SAFE"

    result["verdict"] = verdict
    result["risk_score"] = risk_score
    result["flags"] = flags
    return result


@sub_router.post("/rugshield")
async def rug_shield(req: TokenRequest):
    """Quick pre-buy safety check. Binary safe/unsafe in under 2 seconds."""
    try:
        result = await _rug_shield(req.address, req.chain)
        await record_x402_payment("rugshield", "0.02", req.address)
        return {
            "tool": "Rug Shield",
            "version": "2.0",
            "timestamp": datetime.now(UTC).isoformat(),
            **result,
            "guarantee": "Data delivered or auto-refund via x402 receipt",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


# ═══════════════════════════════════════════════════════════════
# TOOL 10: Token Pulse ($0.25)
# ═══════════════════════════════════════════════════════════════


async def _token_pulse(address: str, chain: str) -> dict:
    """Comprehensive token health dashboard."""
    result = {"chain": chain, "address": address, "sources_used": []}

    # Layer 1: DexScreener - core metrics
    try:
        data, _ = await fetch_with_fallback(
            [f"https://api.dexscreener.com/latest/dex/tokens/{address}"], timeout=8
        )
        if data and data.get("pairs"):
            pair = data["pairs"][0]
            result["price_usd"] = pair.get("priceUsd", 0)
            result["liquidity"] = pair.get("liquidity", {}).get("usd", 0)
            result["volume_24h"] = pair.get("volume", {}).get("h24", 0)
            result["price_change_1h"] = pair.get("priceChange", {}).get("h1", 0)
            result["price_change_6h"] = pair.get("priceChange", {}).get("h6", 0)
            result["price_change_24h"] = pair.get("priceChange", {}).get("h24", 0)
            result["txns_24h"] = pair.get("txns", {}).get("h24", {})
            result["makers"] = pair.get("makers", {}).get("h24", 0)
            result["fdv"] = pair.get("fdv", 0)
            result["pair_age_hours"] = pair.get("pairCreatedAt", 0)
            result["sources_used"].append("dexscreener")
    except Exception:
        pass

    # Layer 2: Coingecko - additional metrics
    try:
        data, _ = await fetch_with_fallback(
            [f"https://api.coingecko.com/api/v3/coins/{address}"], timeout=8
        )
        if data:
            result["coingecko"] = {
                "market_cap": data.get("market_data", {}).get("market_cap", {}).get("usd"),
                "circulating_supply": data.get("market_data", {}).get("circulating_supply"),
                "total_supply": data.get("market_data", {}).get("total_supply"),
            }
            result["sources_used"].append("coingecko")
    except Exception:
        pass

    # Layer 3: Compute health score
    score = 50  # baseline

    if result.get("liquidity", 0) > 100000:
        score += 15
    elif result.get("liquidity", 0) > 10000:
        score += 10
    elif result.get("liquidity", 0) < 1000:
        score -= 20

    if result.get("volume_24h", 0) > 100000:
        score += 10
    elif result.get("volume_24h", 0) < 100:
        score -= 15

    change_24h = result.get("price_change_24h", 0)
    if -10 <= change_24h <= 10:
        score += 5  # stable
    elif change_24h > 50:
        score -= 10  # pump risk
    elif change_24h < -30:
        score -= 15  # dump

    makers = result.get("makers", 0)
    if makers > 1000:
        score += 10
    elif makers < 50:
        score -= 10

    if len(result["sources_used"]) >= 2:
        score += 5
    elif len(result["sources_used"]) == 0:
        score -= 20

    score = max(0, min(100, score))

    # Trend direction
    if result.get("price_change_24h", 0) > 5:
        trend = "up"
    elif result.get("price_change_24h", 0) < -5:
        trend = "down"
    else:
        trend = "stable"

    result["health_score"] = score
    result["trend"] = trend
    result["health_label"] = (
        "excellent"
        if score >= 80
        else "good"
        if score >= 60
        else "fair"
        if score >= 40
        else "poor"
        if score >= 20
        else "critical"
    )

    return result


@sub_router.post("/pulse")
async def token_pulse(req: TokenRequest):
    """Token health dashboard - liquidity, volume, momentum, trajectory."""
    try:
        result = await _token_pulse(req.address, req.chain)
        await record_x402_payment("pulse", "0.01", req.address)
        return {
            "tool": "Token Pulse",
            "version": "2.0",
            "timestamp": datetime.now(UTC).isoformat(),
            **result,
            "guarantee": "Data delivered or auto-refund via x402 receipt",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


# ═══════════════════════════════════════════════════════════════
# TOOL 14: Token Forensics ($0.10)
# ═══════════════════════════════════════════════════════════════


async def _token_forensics(address: str, chain: str) -> dict:
    """Deep token forensics combining multiple data sources."""
    result = {"address": address, "chain": chain, "sources_used": []}

    # DexScreener
    try:
        data, _ = await fetch_with_fallback(
            [f"https://api.dexscreener.com/latest/dex/tokens/{address}"]
        )
        if data and data.get("pairs"):
            pair = data["pairs"][0]
            result["price_usd"] = pair.get("priceUsd", 0)
            result["liquidity_usd"] = pair.get("liquidity", {}).get("usd", 0)
            result["volume_24h"] = pair.get("volume", {}).get("h24", 0)
            result["price_change_24h"] = pair.get("priceChange", {}).get("h24", 0)
            result["pair_age_days"] = pair.get("pairCreatedAt", 0)
            result["sources_used"].append("dexscreener")
    except Exception:
        pass

    # CoinGecko
    try:
        data, _ = await fetch_with_fallback([f"https://api.coingecko.com/api/v3/coins/{address}"])
        if data:
            result["coingecko"] = {
                "name": data.get("name"),
                "symbol": data.get("symbol"),
                "market_cap_rank": data.get("market_cap_rank"),
                "current_price": data.get("market_data", {}).get("current_price", {}),
            }
            result["sources_used"].append("coingecko")
    except Exception:
        pass

    # GeckoTerminal
    try:
        chain_map = {"solana": "solana", "base": "base", "ethereum": "eth", "bsc": "bsc"}
        gecko_chain = chain_map.get(chain, chain)
        data, _ = await fetch_with_fallback(
            [f"https://api.geckoterminal.com/api/v2/networks/{gecko_chain}/tokens/{address}"]
        )
        if data and data.get("data"):
            result["geckoterminal"] = True
            result["sources_used"].append("geckoterminal")
    except Exception:
        pass

    # DeFiLlama
    try:
        data, _ = await fetch_with_fallback([f"https://coins.llama.fi/token/{chain}:{address}"])
        if data and data.get("coins"):
            result["defillama"] = data["coins"]
            result["sources_used"].append("defillama")
    except Exception:
        pass

    # Risk scoring
    risk_score = 0
    findings = []
    liq = result.get("liquidity_usd", 0)
    if liq > 0 and liq < 1000:
        risk_score += 25
        findings.append(f"Very low liquidity: ${liq:,.0f}")
    elif liq >= 1000 and liq < 10000:
        risk_score += 15
        findings.append(f"Low liquidity: ${liq:,.0f}")

    vol = result.get("volume_24h", 0)
    if liq > 0 and vol < liq * 0.01:
        risk_score += 20
        findings.append("Extremely low volume relative to liquidity")

    if len(result["sources_used"]) == 0:
        risk_score += 30
        findings.append("No data from any source")

    risk_score = min(100, risk_score)
    result["risk_score"] = risk_score
    result["findings"] = findings
    result["recommendation"] = (
        "AVOID" if risk_score >= 70 else "CAUTION" if risk_score >= 40 else "PROCEED"
    )

    return result


@sub_router.post("/forensics")
async def token_forensics(req: GenericRequest):
    """Deep token forensics report."""
    try:
        address = req.address or req.token or ""
        result = await _token_forensics(address, req.chain)
        await record_x402_payment("forensics", "0.10", address)
        return {
            "tool": "Token Forensics",
            "version": "2.0",
            "timestamp": datetime.now(UTC).isoformat(),
            **result,
            "guarantee": "Data delivered or auto-refund via x402 receipt",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


# ═══════════════════════════════════════════════════════════════
# TOOL 20: Token Deep Dive ($0.10)
# ═══════════════════════════════════════════════════════════════


@sub_router.post("/token_deep_dive")
async def token_deep_dive(req: GenericRequest):
    """Deep token analysis across chains."""
    try:
        query = req.address or req.token or req.query or ""
        result = await _token_forensics(query, req.chain)
        result["tool_name"] = "Token Deep Dive"
        await record_x402_payment("token_deep_dive", "0.10", query)
        return {
            "tool": "Token Deep Dive",
            "version": "2.0",
            "timestamp": datetime.now(UTC).isoformat(),
            **result,
            "guarantee": "Data delivered or auto-refund via x402 receipt",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


# ═══════════════════════════════════════════════════════════════
# TOOL 22: Honeypot Check ($0.05)
# ═══════════════════════════════════════════════════════════════


async def _honeypot_check(address: str, chain: str) -> dict:
    """Honeypot detection."""
    result = {"address": address, "chain": chain, "sources_used": []}

    # DexScreener - check for trading activity
    try:
        data, _ = await fetch_with_fallback(
            [f"https://api.dexscreener.com/latest/dex/tokens/{address}"]
        )
        if data and data.get("pairs"):
            pair = data["pairs"][0]
            txns = pair.get("txns", {}).get("h24", {})
            buys = txns.get("buys", 0)
            sells = txns.get("sells", 0)

            result["buys_24h"] = buys
            result["sells_24h"] = sells
            result["sources_used"].append("dexscreener")

            # If only buys and no sells, likely honeypot
            if buys > 5 and sells == 0:
                result["is_honeypot"] = True
                result["confidence"] = 0.85
                result["reason"] = "Many buys but zero sells in 24h"
            elif buys > 0 and sells > 0:
                result["is_honeypot"] = False
                result["confidence"] = 0.7
                result["reason"] = "Normal buy/sell ratio"
            else:
                result["is_honeypot"] = None
                result["confidence"] = 0.3
                result["reason"] = "Insufficient trading data"
        else:
            result["is_honeypot"] = None
            result["reason"] = "No trading pairs found"
    except Exception:
        pass

    # Check contract for EVM chains
    if chain in ["base", "ethereum", "bsc"]:
        try:
            # Get contract code
            code = await rpc_call(chain, "eth_getCode", [address, "latest"])
            if code == "0x" or code is None:
                result["is_contract"] = False
                result["reason"] = "No contract code found"
            else:
                result["is_contract"] = True
                result["sources_used"].append(f"{chain}_rpc")
        except Exception:
            pass

    return result


@sub_router.post("/honeypot_check")
async def honeypot_check(req: GenericRequest):
    """Honeypot detection."""
    try:
        address = req.address or req.token or ""
        result = await _honeypot_check(address, req.chain)
        await record_x402_payment("honeypot_check", "0.05", address)
        return {
            "tool": "Honeypot Check",
            "version": "2.0",
            "timestamp": datetime.now(UTC).isoformat(),
            **result,
            "guarantee": "Data delivered or auto-refund via x402 receipt",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


# ═══════════════════════════════════════════════════════════════
# TOOL 25: Token Comparison ($0.08)
# ═══════════════════════════════════════════════════════════════


@sub_router.post("/token_comparison")
async def token_comparison(req: MultiTokenRequest):
    """Side-by-side token comparison."""
    try:
        comparisons = []
        for addr in req.addresses[:5]:
            result = await _token_forensics(addr, req.chain)
            comparisons.append(
                {
                    "address": addr,
                    "price_usd": result.get("price_usd", 0),
                    "liquidity_usd": result.get("liquidity_usd", 0),
                    "volume_24h": result.get("volume_24h", 0),
                    "risk_score": result.get("risk_score", 0),
                    "sources_used": result.get("sources_used", []),
                }
            )

        await record_x402_payment("token_comparison", "0.08", ",".join(req.addresses[:3]))
        return {
            "tool": "Token Comparison",
            "version": "2.0",
            "timestamp": datetime.now(UTC).isoformat(),
            "tokens": comparisons,
            "guarantee": "Data delivered or auto-refund via x402 receipt",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


# ═══════════════════════════════════════════════════════════════
# TOOL 26: Risk Monitor ($0.05)
# ═══════════════════════════════════════════════════════════════


async def _risk_monitor(address: str, chain: str) -> dict:
    """Real-time risk monitoring."""
    result = {"address": address, "chain": chain, "sources_used": [], "alerts": []}

    # Check DexScreener for anomalies
    try:
        data, _ = await fetch_with_fallback(
            [f"https://api.dexscreener.com/latest/dex/tokens/{address}"]
        )
        if data and data.get("pairs"):
            pair = data["pairs"][0]
            liq = pair.get("liquidity", {}).get("usd", 0)
            pair.get("volume", {}).get("h24", 0)
            price_change = pair.get("priceChange", {}).get("h24", 0)

            if price_change < -50:
                result["alerts"].append(
                    {
                        "type": "price_crash",
                        "severity": "critical",
                        "detail": f"Price down {price_change}% in 24h",
                    }
                )
            elif price_change < -20:
                result["alerts"].append(
                    {
                        "type": "price_drop",
                        "severity": "warning",
                        "detail": f"Price down {price_change}% in 24h",
                    }
                )

            if liq < 1000:
                result["alerts"].append(
                    {
                        "type": "low_liquidity",
                        "severity": "warning",
                        "detail": f"Liquidity only ${liq:,.0f}",
                    }
                )

            result["sources_used"].append("dexscreener")
    except Exception:
        pass

    result["alert_count"] = len(result["alerts"])
    result["risk_level"] = (
        "critical"
        if any(a["severity"] == "critical" for a in result["alerts"])
        else "warning"
        if result["alerts"]
        else "normal"
    )

    return result


@sub_router.post("/risk_monitor")
async def risk_monitor(req: GenericRequest):
    """Real-time risk monitoring."""
    try:
        address = req.address or req.token or ""
        result = await _risk_monitor(address, req.chain)
        await record_x402_payment("risk_monitor", "0.05", address)
        return {
            "tool": "Risk Monitor",
            "version": "2.0",
            "timestamp": datetime.now(UTC).isoformat(),
            **result,
            "guarantee": "Data delivered or auto-refund via x402 receipt",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


# ═══════════════════════════════════════════════════════════════
# TOOL 32: Liquidity Flow ($0.08)
# ═══════════════════════════════════════════════════════════════


async def _liquidity_flow(address: str, chain: str) -> dict:
    """Liquidity flow analysis."""
    result = {"address": address, "chain": chain, "sources_used": []}

    # DexScreener liquidity data
    try:
        data, _ = await fetch_with_fallback(
            [f"https://api.dexscreener.com/latest/dex/tokens/{address}"]
        )
        if data and data.get("pairs"):
            pairs = data["pairs"]
            result["pairs"] = []
            total_liq = 0
            for p in pairs[:5]:
                liq = p.get("liquidity", {}).get("usd", 0)
                total_liq += liq
                result["pairs"].append(
                    {
                        "exchange": p.get("dexId"),
                        "base": p.get("baseToken", {}).get("symbol"),
                        "quote": p.get("quoteToken", {}).get("symbol"),
                        "liquidity_usd": liq,
                        "volume_24h": p.get("volume", {}).get("h24", 0),
                    }
                )
            result["total_liquidity_usd"] = total_liq
            result["sources_used"].append("dexscreener")
    except Exception:
        pass

    # DeFiLlama for protocol TVL
    try:
        data, _ = await fetch_with_fallback([f"https://coins.llama.fi/token/{chain}:{address}"])
        if data and data.get("coins"):
            coin = data["coins"][f"{chain}:{address}"]
            result["price"] = coin.get("price", 0)
            result["sources_used"].append("defillama")
    except Exception:
        pass

    return result


@sub_router.post("/liquidity_flow")
async def liquidity_flow(req: GenericRequest):
    """Liquidity flow analysis."""
    try:
        address = req.address or req.token or ""
        result = await _liquidity_flow(address, req.chain)
        await record_x402_payment("liquidity_flow", "0.08", address)
        return {
            "tool": "Liquidity Flow",
            "version": "2.0",
            "timestamp": datetime.now(UTC).isoformat(),
            **result,
            "guarantee": "Data delivered or auto-refund via x402 receipt",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


# ═══════════════════════════════════════════════════════════════
# TOOL 33: Rug Pull Predictor ($0.10)
# ═══════════════════════════════════════════════════════════════


async def _rug_pull_predictor(address: str, chain: str) -> dict:
    """Rug pull prediction model."""
    result = {"address": address, "chain": chain, "sources_used": [], "signals": []}

    # Get token data
    try:
        data, _ = await fetch_with_fallback(
            [f"https://api.dexscreener.com/latest/dex/tokens/{address}"]
        )
        if data and data.get("pairs"):
            pair = data["pairs"][0]
            liq = pair.get("liquidity", {}).get("usd", 0)
            vol = pair.get("volume", {}).get("h24", 0)
            pair_age = pair.get("pairCreatedAt", 0)
            import time

            age_hours = (time.time() * 1000 - pair_age) / 3600000 if pair_age else 999

            # Rug pull signals
            if liq < 5000:
                result["signals"].append(
                    {"type": "low_liq", "weight": 0.3, "detail": f"Liquidity ${liq:,.0f}"}
                )
            if age_hours < 24:
                result["signals"].append(
                    {"type": "new_token", "weight": 0.25, "detail": f"Age {age_hours:.1f}h"}
                )
            if vol > liq * 10:
                result["signals"].append(
                    {
                        "type": "vol_spike",
                        "weight": 0.2,
                        "detail": f"Volume {vol / liq:.1f}x liquidity",
                    }
                )

            # Check holder concentration via Solana RPC
            if chain == "solana":
                try:
                    token_accounts = await rpc_call(
                        "solana",
                        "getTokenAccountsByOwner",
                        [
                            address,
                            {"programId": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"},
                            {"encoding": "jsonParsed"},
                        ],
                    )
                    if token_accounts and token_accounts.get("value"):
                        holders = len(token_accounts["value"])
                        if holders < 50:
                            result["signals"].append(
                                {
                                    "type": "few_holders",
                                    "weight": 0.25,
                                    "detail": f"Only {holders} holders",
                                }
                            )
                        result["holder_count"] = holders
                        result["sources_used"].append("solana_rpc")
                except Exception:
                    pass

            result["sources_used"].append("dexscreener")
    except Exception:
        pass

    # Calculate rug pull probability
    total_weight = sum(s["weight"] for s in result["signals"])
    rug_probability = min(1.0, total_weight)

    result["rug_probability"] = rug_probability
    result["risk_level"] = (
        "CRITICAL"
        if rug_probability >= 0.7
        else "HIGH"
        if rug_probability >= 0.4
        else "MEDIUM"
        if rug_probability >= 0.2
        else "LOW"
    )
    result["recommendation"] = (
        "DO NOT BUY"
        if rug_probability >= 0.7
        else "EXTREME CAUTION"
        if rug_probability >= 0.4
        else "MONITOR"
        if rug_probability >= 0.2
        else "OK"
    )

    return result


@sub_router.post("/rug_pull_predictor")
async def rug_pull_predictor(req: GenericRequest):
    """Rug pull prediction."""
    try:
        address = req.address or req.token or ""
        result = await _rug_pull_predictor(address, req.chain)
        await record_x402_payment("rug_pull_predictor", "0.10", address)
        return {
            "tool": "Rug Pull Predictor",
            "version": "2.0",
            "timestamp": datetime.now(UTC).isoformat(),
            **result,
            "guarantee": "Data delivered or auto-refund via x402 receipt",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
