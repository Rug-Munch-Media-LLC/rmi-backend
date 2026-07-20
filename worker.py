#!/usr/bin/env python3
"""
RMI Worker - Background job processor for token scanning and queue processing
"""

import asyncio
import json
import logging
import os
import sys
from datetime import UTC, datetime

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

REDIS_HOST = os.getenv("REDIS_HOST", "dragonfly")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")
REDIS_DB = int(os.getenv("REDIS_DB", "0"))

SCAN_QUEUE = "token_scan_queue"
NEWS_QUEUE = "news_queue"
ALERT_QUEUE = "alert_queue"


async def connect_redis():
    """Connect to Redis/Dragonfly with retry logic"""
    import redis.asyncio as redis

    retries = 0
    max_retries = 30

    while retries < max_retries:
        try:
            r = redis.Redis(
                host=REDIS_HOST,
                port=REDIS_PORT,
                password=REDIS_PASSWORD,
                db=REDIS_DB,
                decode_responses=True,
            )
            await r.ping()
            logger.info(f"Connected to Redis at {REDIS_HOST}:{REDIS_PORT}")
            return r
        except Exception as e:
            retries += 1
            logger.warning(f"Redis connection attempt {retries}/{max_retries} failed: {e}")
            if retries >= max_retries:
                logger.error("Max Redis retries reached, exiting")
                sys.exit(1)
            await asyncio.sleep(2)


async def process_scan_job(r, job_data):
    """Process a token scan job from the queue using real security scanning."""
    try:
        import json

        token_address = job_data.get("token_address")
        chain = job_data.get("chain", "solana")
        tiers = ["free", "pro"] if job_data.get("deep") else ["free"]

        logger.info(f"Scanning token: {token_address} on {chain} (tiers: {tiers})")

        # Use the real scanner engine with real modules
        try:
            from app.domains.scanner.service import scan_token

            scan_result_obj = await scan_token(
                token_address=token_address,
                chain=chain,
                tiers=tiers,
                include_market_data=True,
            )

            scan_result = {
                "token_address": token_address,
                "chain": chain,
                "scanned_at": datetime.now(UTC).isoformat(),
                "status": "completed",
                "safety_score": scan_result_obj.safety_score,
                "risk_score": 100 - scan_result_obj.safety_score,
                "confidence": scan_result_obj.confidence,
                "flags": scan_result_obj.risk_flags,
                "modules_ran": len(scan_result_obj.modules_run),
                "modules_successful": sum(
                    1 for m in scan_result_obj.modules_run if m.get("status") == "success"
                ),
            }

            logger.info(
                f"Token {token_address} scanned — "
                f"safety: {scan_result_obj.safety_score}/100, "
                f"flags: {scan_result_obj.risk_flags or 'none'}"
            )

        except ImportError:
            # Fallback: basic DexScreener fetch
            logger.warning("scanner module not available, using fallback")
            import httpx

            async with httpx.AsyncClient(timeout=30.0) as client:
                try:
                    response = await client.get(
                        f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"
                    )
                    token_data = response.json() if response.status_code == 200 else {}
                except Exception as e:
                    logger.error(f"Failed to fetch token data: {e}")
                    token_data = {}

            scan_result = {
                "token_address": token_address,
                "chain": chain,
                "scanned_at": datetime.now(UTC).isoformat(),
                "status": "completed",
                "risk_score": 50,
                "safety_score": 50,
                "flags": [],
            }

        # Store result in Redis cache (1 hour TTL)
        await r.set(f"scan_result:{token_address}", json.dumps(scan_result), ex=3600)
        await r.lpush("scan_results", json.dumps(scan_result))
        await r.ltrim("scan_results", 0, 99)

        logger.info(f"Scan completed for {token_address}")
        return True
    except Exception as e:
        logger.error(f"Scan job failed: {e}")
        return False


async def process_news_job(r, job_data):
    """Process a news aggregation job"""
    try:
        logger.info(f"Processing news job: {job_data}")
        # TODO: Implement news aggregation
        return True
    except Exception as e:
        logger.error(f"News job failed: {e}")
        return False


async def process_alert_job(r, job_data):
    """Process an alert generation job"""
    try:
        logger.info(f"Processing alert job: {job_data}")
        # TODO: Implement alert generation
        return True
    except Exception as e:
        logger.error(f"Alert job failed: {e}")
        return False


async def worker_loop(r):
    """Main worker loop - process jobs from queues"""
    logger.info("Starting worker loop...")

    while True:
        try:
            # Try to get a job from scan queue first
            result = await r.brpop(SCAN_QUEUE, timeout=1)
            if result:
                _, job_json = result
                import json

                job_data = json.loads(job_json)
                await process_scan_job(r, job_data)
                continue

            # Check news queue
            result = await r.brpop(NEWS_QUEUE, timeout=1)
            if result:
                _, job_json = result
                import json

                job_data = json.loads(job_json)
                await process_news_job(r, job_data)
                continue

            # Check alert queue
            result = await r.brpop(ALERT_QUEUE, timeout=1)
            if result:
                _, job_json = result
                import json

                job_data = json.loads(job_json)
                await process_alert_job(r, job_data)
                continue

            # No jobs, small sleep before next iteration
            await asyncio.sleep(0.5)

        except asyncio.CancelledError:
            logger.info("Worker loop cancelled")
            break
        except Exception as e:
            logger.error(f"Worker loop error: {e}")
            await asyncio.sleep(5)


async def periodic_token_scan(r):
    """Periodically scan new tokens from DEXs using token_discovery engine."""
    logger.info("Starting periodic token scanner...")

    try:
        from app.token_discovery import MONITORED_CHAINS, discover_new_tokens

        DISCOVERY_AVAILABLE = True
    except ImportError:
        logger.warning("token_discovery module not available, using stub mode")
        DISCOVERY_AVAILABLE = False

    while True:
        try:
            if DISCOVERY_AVAILABLE:
                # Discover new tokens across all monitored chains
                discovered = await discover_new_tokens(
                    r,
                    chains=list(MONITORED_CHAINS.keys()),
                    scan_security=True,
                )

                total = sum(len(tokens) for tokens in discovered.values())
                if total > 0:
                    # Queue new tokens for deep scanning
                    for chain, tokens in discovered.items():
                        for token in tokens:
                            await r.lpush(
                                SCAN_QUEUE,
                                json.dumps(
                                    {
                                        "token_address": token["address"],
                                        "chain": chain,
                                        "symbol": token.get("symbol", ""),
                                        "name": token.get("name", ""),
                                        "liquidity_usd": token.get("liquidity_usd", 0),
                                        "risk_score": token.get("risk_score", 0),
                                        "discovered_at": datetime.now(UTC).isoformat(),
                                    }
                                ),
                            )

                    logger.info(f"🔍 Queued {total} new tokens for deep scanning")
                else:
                    logger.debug("No new tokens discovered this cycle")
            else:
                logger.warning("Periodic scan tick - token discovery not available")

            # Run every 5 minutes
            await asyncio.sleep(300)

        except asyncio.CancelledError:
            logger.info("Periodic scanner cancelled")
            break
        except Exception as e:
            logger.error(f"Periodic scanner error: {e}")
            await asyncio.sleep(60)


async def main():
    """Main entry point"""
    logger.info("RMI Worker starting...")
    logger.info(f"Config: REDIS_HOST={REDIS_HOST}, REDIS_PORT={REDIS_PORT}")

    r = await connect_redis()

    # Run worker loop and periodic scanner concurrently
    await asyncio.gather(worker_loop(r), periodic_token_scan(r), return_exceptions=True)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Worker stopped by user")
    except Exception as e:
        logger.error(f"Worker crashed: {e}")
        sys.exit(1)
