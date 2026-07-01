#!/usr/bin/env python3
"""#13 — Social-Seed Detection Engine. Watches X/Twitter for new token tickers,
cross-references against DataBus, runs SENTINEL scan, posts risk assessment."""

import os
import re
from datetime import UTC, datetime

import httpx
from fastapi import APIRouter, Query

router = APIRouter(prefix="/api/v1/social-seed", tags=["social-seed-detector"])

BACKEND = os.environ.get("BACKEND_URL", "http://localhost:8000")
DEXSCREENER = "https://api.dexscreener.com/latest/dex/search"

# In-memory store for detected social seeds (Redis in prod)
_detected_seeds: list[dict] = []

# Token ticker pattern: $SYMBOL or #SYMBOL
TICKER_RE = re.compile(r"\$([A-Z]{2,8})|#([A-Za-z]{3,12})", re.IGNORECASE)

# Known scam keywords
SCAM_KEYWORDS = [
    "airdrop",
    "claim now",
    "presale",
    "whitelist",
    "giveaway",
    "free mint",
    "stealth launch",
    "100x",
    "1000x",
    "moon",
    "wen lambo",
    "gem",
    "alpha leak",
]


@router.get("/detect")
async def detect_social_seeds(q: str = Query("crypto new token launch")):
    """Search for social mentions of new token launches. Uses web search as proxy for X."""
    seeds: list[dict] = []

    # Use web search to find recent mentions (proxy for X/Twitter API)
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                "https://html.duckduckgo.com/html/",
                params={"q": f"{q} site:twitter.com after:{datetime.now(UTC).strftime('%Y-%m-%d')}"},
                headers={"User-Agent": "RMI-SocialSeed/1.0"},
            )
            if resp.status_code == 200:
                html = resp.text
                # Extract potential tickers
                found_tickers = set()
                for match in TICKER_RE.finditer(html):
                    ticker = (match.group(1) or match.group(2)).upper()
                    if len(ticker) >= 3 and ticker not in ("THE", "AND", "FOR", "NEW", "ETH", "BTC", "SOL"):
                        found_tickers.add(ticker)

                for ticker in list(found_tickers)[:20]:
                    # Check if this ticker exists on DexScreener
                    try:
                        r2 = await client.get(f"{DEXSCREENER}?q={ticker}", timeout=8)
                        if r2.status_code == 200:
                            pairs = r2.json().get("pairs", [])
                            if pairs:
                                top = pairs[0]
                                seeds.append(
                                    {
                                        "ticker": ticker,
                                        "chain": top.get("chainId", "?"),
                                        "dex": top.get("dexId", "?"),
                                        "price_usd": float(top.get("priceUsd", 0) or 0),
                                        "liquidity_usd": top.get("liquidity", {}).get("usd", 0) or 0,
                                        "age_hours": int(
                                            (
                                                (datetime.now(UTC).timestamp() * 1000 - top.get("pairCreatedAt", 0))
                                                / 3600000
                                            )
                                            or 0
                                        ),
                                    }
                                )
                    except Exception:
                        continue
    except Exception:
        pass

    # Score risk for each seed
    for s in seeds:
        s["risk_score"] = 50
        s["risk_flags"] = []
        if s["liquidity_usd"] < 10000:
            s["risk_score"] -= 20
            s["risk_flags"].append("low_liquidity")
        if s["age_hours"] < 1:
            s["risk_score"] -= 10
            s["risk_flags"].append("brand_new")
        s["risk_level"] = "high" if s["risk_score"] < 40 else "medium" if s["risk_score"] < 70 else "low"

    seeds.sort(key=lambda s: s["age_hours"])
    _detected_seeds.extend(seeds[:30])
    return {"query": q, "seeds_detected": len(seeds), "seeds": seeds}


@router.get("/recent")
async def get_recent_seeds(limit: int = Query(20, le=50)):
    """Get recently detected social seeds."""
    return {"seeds": _detected_seeds[-limit:], "total_detected": len(_detected_seeds)}


@router.post("/scan-seed")
async def scan_detected_seed(ticker: str = Query(...), chain: str = Query("solana")):
    """Run full SENTINEL scan on a socially detected token."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            # First find the pair on DexScreener
            r1 = await client.get(f"{DEXSCREENER}?q={ticker}", timeout=8)
            if r1.status_code != 200 or not r1.json().get("pairs"):
                return {"error": f"No pair found for {ticker}"}

            pair = r1.json()["pairs"][0]
            addr = pair.get("pairAddress", "")

            # Run SENTINEL scan
            r2 = await client.post(
                f"{BACKEND}/api/v1/token/scan",
                json={"token_address": addr, "chain": chain},
                headers={"X-RMI-Key": "rmi-internal-2026"},
            )
            if r2.status_code != 200:
                return {"error": "Scan failed"}

            scan = r2.json()
            return {
                "ticker": ticker,
                "address": addr,
                "chain": chain,
                "safety_score": scan.get("safety_score", 50),
                "risk_flags": scan.get("risk_flags", []),
                "social_risk": "HIGH" if any(kw in str(scan).lower() for kw in SCAM_KEYWORDS) else "MEDIUM",
            }
    except Exception as e:
        return {"error": str(e)}
