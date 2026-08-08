"""
x402 Enrichment — Infrastructure Utilities
Redis, ClickHouse connections, cache helpers, alerts, Etherscan, LLM, intel, propagation.
"""

from __future__ import annotations

import base64
import datetime
import json
import os
import time
import urllib.parse
import urllib.request
from typing import Any

import redis

from app.core.logging import get_logger

logger = get_logger("x402.enrich")


# ── Redis Connection ────────────────────────────────────────────────


def _redis() -> Any:
    """Lazy Redis connection (sync client)."""
    import redis as _redis
    from dotenv import load_dotenv

    load_dotenv(override=True)
    return _redis.Redis(
        host=os.getenv("REDIS_HOST", "rmi-redis"),
        port=int(os.getenv("REDIS_PORT", "6379")),
        password=os.getenv("REDIS_PASSWORD", ""),
        decode_responses=True,
        socket_connect_timeout=2,
        socket_timeout=2,
    )


# ── ClickHouse Connection ───────────────────────────────────────────


def _ch():
    """Lazy ClickHouse connection."""
    from clickhouse_driver import Client

    return Client(
        host=os.getenv("CH_HOST", "rmi-clickhouse"),
        port=int(os.getenv("CH_PORT", "9000")),
        user=os.getenv("CH_USER", "default"),
        password=os.getenv("CH_PASSWORD", "") or None,
        settings={"max_execution_time": 3},
    )


# ── Cache Helpers ───────────────────────────────────────────────────


def get_cached_enrichment(address: str) -> dict | None:
    """Check if an address has cached enrichment data."""
    try:
        r = _redis()
        cached = r.get(f"x402:enrich:{address}")
        if cached:
            return json.loads(cached)
    except (ConnectionError, TimeoutError, ValueError, json.JSONDecodeError, redis.exceptions.ResponseError) as e:
        logger.warning("Enrichment error: %s", e)
    return None


def preload_enrichment_cache(addresses: list[str]) -> None:
    """
    Background preload: fetch and cache enrichment for hot addresses.
    Intended for high-traffic addresses (top wallets, trending tokens).
    """
    from app.routers.x402_enrichment.labels import lookup_labels

    for addr in addresses[:50]:
        try:
            r = _redis()
            if r.exists(f"x402:enrich:{addr}"):
                continue
            result, _ = lookup_labels([addr])
            labels = result.get(addr, [])
            if labels:
                r.setex(
                    f"x402:enrich:{addr}",
                    1800,
                    json.dumps(
                        {"labels": [{"category": line.label_category, "name": line.label_name} for line in labels]}
                    ),
                )
        except (
            ConnectionError,
            TimeoutError,
            OSError,
            ValueError,
            json.JSONDecodeError,
            redis.exceptions.ResponseError,
        ):
            continue


# ── Alert Hooks ─────────────────────────────────────────────────────


def _fire_enrichment_alert(tool_name: str, alert_type: str, data: list, r=None) -> None:
    """Fire alert when enrichment finds high-risk addresses."""
    try:
        if not r:
            r = _redis()
        alert = json.dumps(
            {
                "tool": tool_name,
                "type": alert_type,
                "data": data,
                "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
            }
        )
        r.lpush("x402:alerts:high_risk", alert)
        r.ltrim("x402:alerts:high_risk", 0, 999)
        logger.warning("ENRICHMENT ALERT: %s in %s: %s", alert_type, tool_name, data)
    except (ConnectionError, TimeoutError, OSError, redis.exceptions.ResponseError) as e:
        logger.warning("Enrichment OSError: %s", e)


# ── Cron Intel Injection ────────────────────────────────────────────


def _load_recent_intel(r=None) -> dict | None:
    """Load the latest RMI intel briefing from Redis for injection into tool results."""
    try:
        if not r:
            r = _redis()
        data = r.get("x402:intel:latest")
        if not data:
            return None
        intel = json.loads(data)
        age_h = (time.time() - intel.get("timestamp", 0)) / 3600
        if age_h > 12:
            return None
        return {
            "source": "rmi_cron_intel",
            "briefing": intel.get("summary", "")[:500],
            "age_hours": round(age_h, 1),
            "scanner_alerts": intel.get("alerts", 0),
            "tokens_scanned": intel.get("tokens_scanned", 0),
            "timestamp": intel.get("timestamp", 0),
        }
    except (ConnectionError, TimeoutError, ValueError, json.JSONDecodeError, redis.exceptions.ResponseError):
        return None


# ── LLM Synthesis ───────────────────────────────────────────────────


def _synthesize_enrichment(risk_summary: str, scam_flags: list, tool_name: str) -> str:
    """Use DeepSeek Flash to synthesize a human-readable enrichment paragraph."""
    try:
        import httpx as _httpx

        key_b64 = os.getenv("LLM_API_KEY_B64", "")
        if not key_b64:
            return ""

        api_key = base64.b64decode(key_b64).decode()

        prompt = (
            f"A crypto security tool '{tool_name}' analyzed some addresses and found: "
            f"{risk_summary} "
            f"Scam patterns detected: {json.dumps(scam_flags)[:500]}. "
            "Write ONE sentence (max 80 chars) summarizing the key risk finding concisely."
        )
        with _httpx.Client(timeout=5) as client:
            r = client.post(
                "https://api.deepseek.com/v1/chat/completions",
                json={
                    "model": "deepseek-chat",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 60,
                    "temperature": 0.3,
                },
                headers={"Authorization": f"Bearer {api_key}"},
            )
        d = r.json()
        return d["choices"][0]["message"]["content"].strip().strip('"')
    except (_httpx.HTTPError, ConnectionError, TimeoutError, ValueError, json.JSONDecodeError, KeyError):
        return ""


# ── Cross-Chain Label Propagation ───────────────────────────────────


def propagate_labels_cross_chain() -> int:
    """Copy phishing/scam/sanctioned labels across chains where the same address exists."""
    try:
        ch = _ch()
        count = ch.execute("""
            INSERT INTO wallet_memory.wallet_labels
            (address, chain_id, label_name, label_category, label_subtype, source, is_sanctioned, loaded_at)
            SELECT DISTINCT
                a.address,
                a.chain_id AS chain_id,
                scam.label_name,
                scam.label_category,
                scam.label_subtype || ' (cross-chain from ' || scam.chain_id || ')',
                'cross_chain_propagation',
                scam.is_sanctioned,
                now()
            FROM wallet_memory.wallet_labels AS scam
            JOIN wallet_memory.wallet_labels AS a
                ON a.address = scam.address AND a.chain_id != scam.chain_id
            WHERE scam.label_category IN ('phish-hack','scam','sanctioned','etherscan-phish-hack-list')
              AND a.chain_id NOT IN ('','unknown')
              AND scam.chain_id NOT IN ('','unknown')
            LIMIT 100000
        """)
        logger.info("Cross-chain propagation: %s labels propagated", count)
        return count
    except (ConnectionError, TimeoutError, OSError, redis.exceptions.ResponseError) as e:
        logger.warning("Cross-chain propagation failed: %s", e)
        return 0


# ── Etherscan V2 Live Contract Name Lookups ─────────────────────


def _get_etherscan_key() -> str:
    """Load Etherscan API key from .env. Returns empty string if not configured."""
    try:
        from dotenv import load_dotenv

        load_dotenv("/app/.env", override=True)
        return os.getenv("ETHERSCAN_API_KEY", "")
    except (ImportError, OSError):
        return ""


def lookup_contract_names(addresses: list[str], chain: str = "ethereum") -> dict[str, str]:
    """
    Live Etherscan V2 contract name lookups for unknown addresses.
    Returns {address: contract_name} dict.
    Rate-limited to 5/sec (free tier). Skips addresses already in CH labels.
    """
    key = _get_etherscan_key()
    if not key:
        return {}

    CHAIN_MAP = {
        "ethereum": 1,
        "eth": 1,
        "bsc": 56,
        "polygon": 137,
        "matic": 137,
        "arbitrum": 42161,
        "arb": 42161,
        "optimism": 10,
        "op": 10,
        "base": 8453,
        "avalanche": 43114,
        "avax": 43114,
        "fantom": 250,
        "ftm": 250,
        "gnosis": 100,
    }
    cid = CHAIN_MAP.get(chain.lower(), 1)

    results = {}
    for addr in addresses[:20]:
        try:
            params = {
                "chainid": cid,
                "module": "contract",
                "action": "getsourcecode",
                "address": addr,
                "apikey": key,
            }
            url = f"https://api.etherscan.io/v2/api?{urllib.parse.urlencode(params)}"
            r = urllib.request.urlopen(url, timeout=5)
            d = json.loads(r.read())
            if d.get("status") == "1" and d.get("result"):
                contract = d["result"][0]
                name = contract.get("ContractName", "")
                if name and name != addr:
                    results[addr] = name
        except (urllib.error.URLError, ConnectionError, TimeoutError, json.JSONDecodeError, KeyError) as e:
            logger.warning("Enrichment URL error: %s", e)
        time.sleep(0.25)

    return results
