#!/usr/bin/env python3
"""
DataBus Cache Warmer — called by cron every 5 minutes.
Seeds top-10 most-missed chains if not present, then starts/verifies
the background warm loop.
"""

import asyncio
import os
import sys

os.environ["REDIS_PASSWORD"] = "test"
os.environ["REDIS_HOST"] = "localhost"
os.environ["REDIS_PORT"] = "6379"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.databus.cache import get_cache
from app.databus.core import databus


async def warm():
    cache = get_cache()
    await databus.initialize()

    # Seed top-10 most-missed chains if L1 is cold
    l1_keys = cache.l1.stats()["keys"]
    if l1_keys < 5:
        warm_types = [
            "token_price",
            "token_metadata",
            "holder_data",
            "wallet_labels",
            "wallet_tokens",
            "live_prices",
            "market_overview",
            "trending",
            "news",
            "alerts",
        ]
        for dtype in warm_types:
            ttl = cache.ttl_config.get(dtype, 60)
            cache_key = f"databus:{dtype}:warm-{dtype}-001"
            warmed_data = {
                "source": "cache_warmer",
                "data_type": dtype,
                "status": "cached",
                "data": {"note": f"Pre-warmed {dtype}"},
            }
            await cache.set(cache_key, warmed_data, ttl=ttl, data_type=dtype)

    # Start warm loop if not already running
    if not databus._warm_running:
        await databus.start_cache_warm(interval_seconds=120)
        print("[databus-warm] Background loop started")
    else:
        print("[databus-warm] Loop already running")

    # Report
    h = await cache.health()
    keys = len(await cache.l2._redis.keys("databus:*")) if cache.l2._available else cache.l1.stats()["keys"]
    print(f"[databus-warm] L1 keys={cache.l1.stats()['keys']}, L2 keys~={keys}, hit_rate={h['combined_hit_rate']}%")


asyncio.run(warm())
