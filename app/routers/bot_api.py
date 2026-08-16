"""
Bot API Router — Telegram-optimized API endpoints
===================================================
Shared scanner, AI, and market data for the RugMunch Telegram bot.
All endpoints are optimized for fast responses with Redis caching.

Endpoints:
  POST /api/v1/bot/scan   — Fast token scan (reads Redis cache, fallback to DexScreener)
  POST /api/v1/bot/ask    — AI Q&A (free remote snappy model, fast answers)
  GET  /api/v1/bot/pulse  — Market pulse (BTC, ETH, SOL price + Greed/Fear + top movers)
  GET  /api/v1/bot/health — Health check
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

import httpx
from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from app.core.logging import get_logger
from app.core.redis import get_redis_async
from app.llm_config import get_cheap_fallback_config, get_hf_config, get_long_config, get_snappy_config

logger = get_logger(__name__)
router = APIRouter(prefix="/api/v1/bot", tags=["bot"])

BOT_CACHE_TTL = 60  # 1 minute for bot requests


# ── Models ──


class BotScanRequest(BaseModel):
    address: str = Field(..., min_length=32, max_length=64, description="Token contract address")
    chain: str = Field(default="solana", description="Blockchain (solana, ethereum, bsc, polygon)")


class BotAskRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=500, description="Crypto question")
    user_id: str = Field(default="telegram", description="Telegram user ID for rate limiting")


# ── Helpers ──


async def _read_scanner_cache(chain: str, address: str) -> dict[str, Any] | None:
    """Read scanner result from Redis cache."""
    try:
        r = get_redis_async()
        if r is None:
            return None
        cache_key = f"sentinelscan:{chain}:{address}"
        raw = await r.get(cache_key)
        if raw is None:
            return None
        return json.loads(raw)
    except Exception:
        return None


async def _quick_scan_fallback(chain: str, address: str) -> dict[str, Any]:
    """Fallback scan using DexScreener."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"https://api.dexscreener.com/latest/dex/tokens/{address}",
                headers={"User-Agent": "RugMunchBot/1.0"},
            )
            if resp.status_code == 200:
                pairs = resp.json().get("pairs", [])
                if pairs:
                    p = pairs[0]
                    return {
                        "risk_score": 50,
                        "liquidity_usd": float(p.get("liquidity", {}).get("usd", 0)),
                        "price_usd": float(p.get("priceUsd", 0)),
                        "volume_24h": float(p.get("volume", {}).get("h24", 0)),
                        "holders": 0,
                        "deployer_funded_by": "unknown",
                        "is_mintable": False,
                        "is_honeypot": False,
                    }
    except Exception:  # noqa: S110 — intentional best-effort
        pass
    return {"risk_score": 0, "error": "No data available for this token"}


async def _ask_llm_once(config: dict[str, Any], query: str) -> str:
    """One OpenAI-compatible chat POST to ``config`` (free or cheap tier).

    Returns the stripped answer, or ``""`` on ANY failure (missing key, non-200,
    rate-limit, timeout, empty content). Never raises, never hangs (30s timeout).
    """
    api_key = config.get("api_key")
    base_url = config.get("base_url")
    model = config.get("model")
    if not api_key or not base_url or not model:
        return ""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                base_url,
                json={
                    "model": model,
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "You are the RMI Terminal, a crypto intelligence AI. Answer concisely in 2-4 sentences."
                            ),
                        },
                        {"role": "user", "content": query},
                    ],
                    "max_tokens": config.get("max_tokens", 256),
                    "temperature": config.get("temperature", 0.4),
                },
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://rugmunch.io",
                    "X-Title": "RMI Bot Ask",
                },
            )
            if resp.status_code != 200:
                return ""
            data = resp.json()
            choices = data.get("choices") or []
            if not choices:
                return ""
            content = (choices[0].get("message") or {}).get("content", "")
            return content.strip() if isinstance(content, str) else ""
    except Exception:  # intentional fail-open
        return ""


# ── Routes ──


@router.get("/health")
async def bot_health() -> dict[str, Any]:
    """Health check for bot connectivity."""
    return {
        "status": "ok",
        "version": "1.0.0",
        "endpoints": {
            "scan": "POST /api/v1/bot/scan",
            "ask": "POST /api/v1/bot/ask",
            "pulse": "GET /api/v1/bot/pulse",
            "health": "GET /api/v1/bot/health",
        },
    }


@router.post("/scan")
async def bot_scan(req: BotScanRequest) -> dict[str, Any]:
    """Fast token scan — reads Redis cache first, falls back to DexScreener."""
    address = req.address.strip()
    chain = req.chain.lower()

    # Try Redis cache first
    cached = await _read_scanner_cache(chain, address)
    if cached:
        return {
            "cached": True,
            "risk_score": cached.get("risk_score", 50),
            "liquidity_usd": cached.get("liquidity_usd", 0),
            "holders": cached.get("total_holders", cached.get("holder_count", 0)),
            "deployer_funded_by": cached.get("deployer_funded_by", "unknown"),
            "is_honeypot": cached.get("is_honeypot", cached.get("is_potential_honeypot", False)),
            "is_mintable": cached.get("is_mintable", False),
            "is_renounced": cached.get("is_renounced", cached.get("is_mint_revoked", False)),
            "scam_probability": cached.get("scam_probability", 0),
            "top_holders": (cached.get("top_holders") or [])[:5],
            "summary": cached.get("analysis_summary", cached.get("summary", "")),
            "scanned_at": cached.get("scanned_at", ""),
        }

    # Fallback to DexScreener
    data = await _quick_scan_fallback(chain, address)
    return {"cached": False, **data}


@router.post("/ask")
async def bot_ask(req: BotAskRequest) -> dict[str, Any]:
    """AI Q&A — uses the free remote snappy model for fast, free answers."""
    query = req.query.strip()

    # Check Redis cache for common questions
    cache_key = f"bot:ask:{hashlib.sha256(query.encode()).hexdigest()[:16]}"
    try:
        r = get_redis_async()
        if r:
            cached = await r.get(cache_key)
            if cached:
                cached_data = json.loads(cached)
                cached_data["provider"] = "cache"
                return cached_data
    except Exception:  # noqa: S110 — best-effort cache
        pass

    # Free-first chain: free laguna (snappy) → free nemotron (long) → paid
    # qwen :lowest (get_cheap_fallback_config) only on rate-limit/failure.
    chain: list[tuple[str, dict[str, Any]]] = [
        ("snappy", get_snappy_config()),
        ("long", get_long_config()),
        ("huggingface", get_hf_config()),  # free HF Inference tier (same setup as telegram)
        ("fallback", get_cheap_fallback_config()),
    ]
    for provider, config in chain:
        answer = await _ask_llm_once(config, query)
        if not answer:
            logger.warning("bot_ask_tier_failed", provider=provider)
            continue
        result = {
            "answer": answer,
            "query_type": "general",
            "sources": [],
            "verified": True,
            "provider": f"{provider}-{config.get('model', '')}",
        }
        # Cache for 5 minutes
        try:
            r2 = get_redis_async()
            if r2:
                await r2.setex(cache_key, 300, json.dumps(result))
        except Exception:  # noqa: S110 — best-effort cache
            pass
        return result

    # Fail open: every free/cheap tier failed (rate-limited / unavailable).
    return {
        "answer": "I couldn't process that question right now. Please try again in a moment.",
        "query_type": "error",
        "sources": [],
        "verified": False,
        "provider": "none",
        "detail": "all_llm_tiers_failed",
    }


@router.get("/pulse")
async def bot_pulse(refresh: bool = Query(default=False)) -> dict[str, Any]:
    """Market pulse — quick crypto market overview for Telegram bot."""
    if not refresh:
        try:
            r = get_redis_async()
            if r:
                cached = await r.get("bot:pulse:v1")
                if cached:
                    return json.loads(cached)
        except Exception:  # noqa: S110 — best-effort cache
            pass

    data: dict[str, Any] = {"btc": {}, "eth": {}, "sol": {}, "fear_greed": {}, "top_movers": [], "updated_at": ""}

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            # CoinGecko simple price
            resp = await client.get(
                "https://api.coingecko.com/api/v3/simple/price",
                params={
                    "ids": "bitcoin,ethereum,solana",
                    "vs_currencies": "usd",
                    "include_24hr_change": "true",
                    "include_market_cap": "true",
                    "include_24hr_vol": "true",
                },
                headers={"User-Agent": "RugMunchBot/1.0"},
            )
            if resp.status_code == 200:
                prices = resp.json()
                data["btc"] = {
                    "price": prices.get("bitcoin", {}).get("usd", 0),
                    "change_24h": prices.get("bitcoin", {}).get("usd_24h_change", 0),
                }
                data["eth"] = {
                    "price": prices.get("ethereum", {}).get("usd", 0),
                    "change_24h": prices.get("ethereum", {}).get("usd_24h_change", 0),
                }
                data["sol"] = {
                    "price": prices.get("solana", {}).get("usd", 0),
                    "change_24h": prices.get("solana", {}).get("usd_24h_change", 0),
                }

            # Fear & Greed
            fng_resp = await client.get(
                "https://api.alternative.me/fng/?limit=1",
                headers={"User-Agent": "RugMunchBot/1.0"},
            )
            if fng_resp.status_code == 200:
                fng_data = fng_resp.json()
                if fng_data.get("data"):
                    fng = fng_data["data"][0]
                    data["fear_greed"] = {
                        "value": int(fng.get("value", 50)),
                        "classification": fng.get("value_classification", "Neutral"),
                    }

            # Top movers via DexScreener
            dex_resp = await client.get(
                "https://api.dexscreener.com/latest/dex/search?q=SOL",
                headers={"User-Agent": "RugMunchBot/1.0"},
            )
            if dex_resp.status_code == 200:
                pairs = dex_resp.json().get("pairs", [])[:5]
                data["top_movers"] = [
                    {
                        "symbol": p.get("baseToken", {}).get("symbol", ""),
                        "price": float(p.get("priceUsd", 0)),
                        "change_24h": float(p.get("priceChange", {}).get("h24", 0)),
                        "volume_24h": float(p.get("volume", {}).get("h24", 0)),
                        "liquidity": float(p.get("liquidity", {}).get("usd", 0)),
                        "dex": p.get("dexId", ""),
                    }
                    for p in pairs
                    if float(p.get("liquidity", {}).get("usd", 0)) > 10000
                ]

    except Exception as e:
        logger.warning("pulse_fetch_failed", error=str(e)[:120])

    data["updated_at"] = datetime.now(UTC).isoformat()

    # Cache for 60 seconds
    try:
        r = get_redis_async()
        if r:
            await r.setex("bot:pulse:v1", BOT_CACHE_TTL, json.dumps(data))
    except Exception:  # noqa: S110 — best-effort cache
        pass

    return data
