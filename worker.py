#!/usr/bin/env python3
"""
RMI Worker - Background job processor for token scanning and queue processing

Uses structured JSON logging consistent with the FastAPI backend.
"""

import asyncio
import json
import os
import sys
from datetime import UTC, datetime

from dotenv import load_dotenv

load_dotenv()

# ── Structured logging ───────────────────────────────────────────────
from app.core.logging_config import setup_stdlib_logging  # noqa: E402 — dotenv must load first

logger = setup_stdlib_logging(__name__)
worker_log = logger  # alias used throughout

REDIS_HOST = os.getenv("REDIS_HOST", "dragonfly")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")
REDIS_DB = int(os.getenv("REDIS_DB", "0"))

SCAN_QUEUE = "token_scan_queue"
# Backlog ceiling; discovery pauses above it (alert fires at 500).
SCAN_QUEUE_MAX = int(os.getenv("SCAN_QUEUE_MAX", "400"))
NEWS_QUEUE = "news_queue"
ALERT_QUEUE = "alert_queue"

# Gradual rollout: use the UnifiedTokenScanner by default.  Set
# USE_UNIFIED_SCANNER=false to roll back to the legacy tier-based scanner.
USE_UNIFIED_SCANNER = os.getenv("USE_UNIFIED_SCANNER", "true").lower() in ("1", "true", "yes")


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
            logger.info("redis_connected", extra={"host": REDIS_HOST, "port": REDIS_PORT})
            return r
        except Exception as e:
            retries += 1
            logger.warning(
                "redis_connect_retry",
                extra={"attempt": retries, "max_retries": max_retries, "error": str(e)},
            )
            if retries >= max_retries:
                logger.error("redis_max_retries_exceeded")
                sys.exit(1)
            await asyncio.sleep(2)


async def process_scan_job(r, job_data):
    """Process a token scan job from the queue using real security scanning."""
    try:
        import json

        token_address = job_data.get("token_address")
        chain = job_data.get("chain", "solana")
        tiers = ["free", "pro"] if job_data.get("deep") else ["free"]

        logger.info(
            "scanning_token",
            extra={"token_address": token_address, "chain": chain, "tiers": tiers},
        )

        # Use the real scanner engine with real modules
        try:
            if USE_UNIFIED_SCANNER:
                from app.domains.scanner.unified.scanner import scan_token_unified

                scan_result = await scan_token_unified(
                    token_address=token_address,
                    chain=chain,
                    tier="pro" if job_data.get("deep") else "free",
                )
            else:
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
                    "modules_successful": sum(1 for m in scan_result_obj.modules_run if m.get("status") == "success"),
                }

            logger.info(
                "token_scanned",
                extra={
                    "token_address": token_address,
                    "safety_score": scan_result.get("safety_score"),
                    "risk_flags": scan_result.get("flags") or [],
                },
            )

        except ImportError:
            # Fallback: basic DexScreener fetch
            logger.warning("scanner_fallback", extra={"token_address": token_address, "chain": chain})
            import httpx

            async with httpx.AsyncClient(timeout=30.0) as client:
                try:
                    response = await client.get(f"https://api.dexscreener.com/latest/dex/tokens/{token_address}")
                    _ = response.json() if response.status_code == 200 else {}
                except Exception as e:
                    logger.error(
                        "token_fetch_failed",
                        extra={"token_address": token_address, "error": str(e)},
                    )

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
        payload = json.dumps(scan_result, default=str)  # enums (LockStatus) are not JSON-native
        await r.set(f"scan_result:{token_address}", payload, ex=3600)
        await r.lpush("scan_results", payload)
        await r.ltrim("scan_results", 0, 99)

        logger.info("scan_completed", extra={"token_address": token_address})
        return True
    except Exception as e:
        logger.error("scan_job_failed", extra={"token_address": token_address, "error": str(e)})
        return False


async def process_news_job(r, job_data):
    """Process a news aggregation job"""
    try:
        logger.info("news_job_processing", extra={"job_data": job_data})
        # TODO: Implement news aggregation
        return True
    except Exception as e:
        logger.error("news_job_failed", extra={"error": str(e)})
        return False


async def process_alert_job(r, job_data):
    """Process an alert generation job"""
    try:
        logger.info("alert_job_processing", extra={"job_data": job_data})
        # TODO: Implement alert generation
        return True
    except Exception as e:
        logger.error("alert_job_failed", extra={"error": str(e)})
        return False


async def worker_loop(r):
    """Main worker loop - process jobs from queues"""
    logger.info("worker_loop_started")

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
            logger.info("worker_loop_cancelled")
            break
        except Exception as e:
            logger.error("worker_loop_error", extra={"error": str(e)})
            await asyncio.sleep(5)


async def periodic_token_scan(r):
    """Periodically scan new tokens from DEXs using token_discovery engine."""
    logger.info("periodic_scanner_started")

    try:
        from app.token_discovery import MONITORED_CHAINS, discover_tokens

        DISCOVERY_AVAILABLE = True  # noqa: N806
    except ImportError as exc:
        # 2026-09-23: this used to be a bare flag — a renamed symbol made discovery
        # silently dead for weeks ("token_discovery_unavailable" every tick). Always
        # carry the exception text so the next drift is one log line away.
        logger.warning("token_discovery_unavailable", extra={"error": str(exc)})
        DISCOVERY_AVAILABLE = False  # noqa: N806

    while True:
        try:
            if DISCOVERY_AVAILABLE:
                # Discover new tokens across all monitored chains
                discovered = await discover_tokens(chains=list(MONITORED_CHAINS.keys()))

                total = sum(len(tokens) for tokens in discovered.values())
                if total > 0:
                    # Queue new tokens for deep scanning
                    # Discovered ~30-70 tokens per 5-min tick vs a sequential
                    # scanner draining ~40/h: without a ceiling the queue grows
                    # forever. Skipped tokens are re-found next tick (dedup keys
                    # are written only when a token is accepted).
                    backlog = await r.llen(SCAN_QUEUE)
                    if backlog > SCAN_QUEUE_MAX:
                        logger.warning(
                            "scan_queue_saturated",
                            extra={"backlog": backlog, "cap": SCAN_QUEUE_MAX},
                        )
                        await asyncio.sleep(300)
                        continue

                    for chain, tokens in discovered.items():
                        for token in tokens:
                            # 2026-09-23: the 5-minute discovery cycle re-offered the
                            # same tokens forever (no dedup anywhere), so the queue grew
                            # unboundedly (109 items in 20 min on first deploy). One
                            # SETNX-with-TTL per token: a token is queued at most once
                            # per 24h, and the keys expire themselves.
                            dedup_key = f"scan:queued:{chain}:{token['address']}"
                            if not await r.set(dedup_key, 1, ex=86400, nx=True):
                                continue
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

                    logger.info("tokens_queued_for_scan", extra={"count": total})
                else:
                    logger.debug("no_new_tokens_discovered")
            else:
                logger.warning("periodic_scan_tick_discovery_unavailable")

            # Run every 5 minutes
            await asyncio.sleep(300)

        except asyncio.CancelledError:
            logger.info("periodic_scanner_cancelled")
            break
        except Exception as e:
            logger.error("periodic_scanner_error", extra={"error": str(e)})
            await asyncio.sleep(60)


async def main():
    """Main entry point"""
    logger.info("worker_starting")
    logger.info("worker_config", extra={"redis_host": REDIS_HOST, "redis_port": REDIS_PORT})

    r = await connect_redis()

    # Run worker loop and periodic scanner concurrently
    await asyncio.gather(worker_loop(r), periodic_token_scan(r), return_exceptions=True)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("worker_stopped_by_user")
    except Exception as e:
        logger.error("worker_crashed", extra={"error": str(e)})
        sys.exit(1)
