"""Parallel token-scan consumers for the rmi-worker.

The unified scan pipeline is dominated by network enrichment (~2 min/token
when the live news fallback fires), so one sequential consumer drained the
discovery queue far slower than discovery produced tokens (2026-09-23:
backlog grew 328 -> 880 while the loop processed ~1 token/min).

This module runs N bounded consumers (env SCAN_CONCURRENCY, default 4) —
same jobs, same code path, just not one-at-a-time. The scan job body moved
here from worker.py (which is >200 lines and shrink-only; worker.py now
imports run_scan_consumers() and keeps only the discovery + news/alert work).
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime

from app.core.logging_config import setup_stdlib_logging

logger = setup_stdlib_logging(__name__)

SCAN_QUEUE = "token_scan_queue"
SCAN_CONCURRENCY = int(os.getenv("SCAN_CONCURRENCY", "4"))
# Gradual rollout: use the UnifiedTokenScanner by default.  Set
# USE_UNIFIED_SCANNER=false to roll back to the legacy tier-based scanner.
USE_UNIFIED_SCANNER = os.getenv("USE_UNIFIED_SCANNER", "true").lower() in ("1", "true", "yes")


async def process_scan_job(r, job_data):
    """Process a token scan job from the queue using real security scanning."""
    try:
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
        logger.error("scan_job_failed", extra={"token_address": job_data.get("token_address"), "error": str(e)})
        return False


async def scan_queue_consumer(r) -> None:
    """Drain SCAN_QUEUE forever — one of SCAN_CONCURRENCY parallel consumers."""
    while True:
        try:
            result = await r.brpop(SCAN_QUEUE, timeout=1)
            if result:
                _, job_json = result
                await process_scan_job(r, json.loads(job_json))
                continue

            await asyncio.sleep(0.5)

        except asyncio.CancelledError:
            logger.info("scan_consumer_cancelled")
            break
        except Exception as e:
            logger.error("scan_consumer_error", extra={"error": str(e)})
            await asyncio.sleep(5)


async def run_scan_consumers(r) -> None:
    """Run SCAN_CONCURRENCY scan consumers concurrently."""
    logger.info("scan_consumers_started", extra={"count": SCAN_CONCURRENCY})
    await asyncio.gather(*(scan_queue_consumer(r) for _ in range(SCAN_CONCURRENCY)))
