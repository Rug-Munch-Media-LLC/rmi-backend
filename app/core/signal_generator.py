#!/usr/bin/env python3
"""RMI Signal Generator - Automated trading signals from SENTINEL + market data.
Publishes to Redpanda for real-time consumption. Cron every 5 minutes."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.internal_key import INTERNAL_API_KEY
from app.core.logging import get_logger

logger = get_logger(__name__)

BACKEND = os.getenv("BACKEND_URL", "http://localhost:8000")
REDPANDA = os.getenv("REDPANDA_BROKER", "rmi-redpanda:9092")
TOPIC = "rmi.sentinel.signals"
RMI_KEY = INTERNAL_API_KEY

CHAINS = ["solana", "ethereum", "bsc", "base", "arbitrum"]

SIGNAL_RULES = {
    "avoid": {"max_safety": 35, "label": "🔴 AVOID", "desc": "High risk - likely scam or honeypot"},
    "caution": {
        "max_safety": 55,
        "min_safety": 36,
        "label": "🟡 CAUTION",
        "desc": "Moderate risk - DYOR carefully",
    },
    "watch": {
        "min_safety": 56,
        "max_safety": 75,
        "label": "🟢 WATCH",
        "desc": "Decent metrics - worth monitoring",
    },
    "gem": {
        "min_safety": 76,
        "max_liquidity": 500000,
        "label": "💎 GEM",
        "desc": "Strong safety, low cap - potential gem",
    },
    "bluechip": {
        "min_safety": 76,
        "min_liquidity": 500000,
        "label": "🏦 BLUECHIP",
        "desc": "Established, high liquidity, safe",
    },
}


async def fetch_trending(chain: str, limit: int = 10) -> list:
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(
            f"{BACKEND}/api/v1/databus/fetch/trending?chain={chain}&limit={limit}",
            headers={"X-RMI-Key": RMI_KEY},
        )
        if r.status_code == 200:
            data = r.json()
            return data.get("data", data).get("tokens", []) if isinstance(data, dict) else []
    return []


async def scan_and_signal(token: dict) -> dict | None:
    addr = token.get("address", "") or token.get("mint", "")
    if not addr:
        return None
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(
            f"{BACKEND}/api/v1/token/scan",
            json={"token_address": addr, "chain": token.get("chain", "solana")},
            headers={"X-RMI-Key": RMI_KEY},
        )
        if r.status_code != 200:
            return None
        scan = r.json()
        score = scan.get("safety_score", 50)
        liq = scan.get("free", {}).get("liquidity_usd", 0) or 0

        for _rule_name, rule in SIGNAL_RULES.items():
            if "max_safety" in rule and score > rule["max_safety"]:
                continue
            if "min_safety" in rule and score < rule["min_safety"]:
                continue
            if "max_liquidity" in rule and liq > rule.get("max_liquidity", float("inf")):
                continue
            if "min_liquidity" in rule and liq < rule.get("min_liquidity", 0):
                continue

            return {
                "timestamp": datetime.now(UTC).isoformat(),
                "token": token.get("symbol", "?"),
                "address": addr,
                "chain": token.get("chain", "?"),
                "safety_score": score,
                "liquidity_usd": liq,
                "signal": rule["label"],
                "description": rule["desc"],
                "risk_flags": scan.get("risk_flags", []),
            }
    return None


async def publish_signal(signal: dict) -> None:
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            await c.post(
                f"http://{REDPANDA}/topics/{TOPIC}",
                json={"records": [{"value": json.dumps(signal), "key": signal["address"]}]},
                headers={"Content-Type": "application/vnd.kafka.json.v2+json"},
            )
    except (httpx.HTTPError, json.JSONDecodeError):
        pass


async def main() -> Any:
    logger.info(f"Signal Generator - {datetime.now(UTC).isoformat()}")
    signals = []
    for chain in CHAINS:
        tokens = await fetch_trending(chain, 10)
        for token in tokens:
            signal = await scan_and_signal(token)
            if signal:
                signals.append(signal)
                await publish_signal(signal)

    # Sort by interest: gems first, then avoids (people want warnings)
    signals.sort(
        key=lambda s: (
            0 if "GEM" in s["signal"] else 1 if "AVOID" in s["signal"] else 2,
            -s["safety_score"],
        )
    )

    for s in signals[:20]:
        logger.info(
            f"  {s['signal']} {s['token']} ({s['chain']}) score={s['safety_score']} liq=${s['liquidity_usd']:,.0f}"
        )
    logger.info(f"Generated {len(signals)} signals across {len(CHAINS)} chains")
    return signals


if __name__ == "__main__":
    asyncio.run(main())
