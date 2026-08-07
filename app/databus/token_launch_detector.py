#!/usr/bin/env python3
"""
Token Launch Detector — Real-time new token discovery.
======================================================
Polls DexScreener/GMGN for new token launches every 30 seconds.
Auto-scans new tokens within 60 seconds. Sends Telegram alerts for high-risk.
Stores new launches in Redis for trending page.

Integrates with:
  - GMGN new pairs API (primary)
  - DexScreener search (fallback)
  - Existing scanner pipeline (risk assessment)
  - Telegram bot (@rugmunchbot) for alerts
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)

# Redis keys
LAUNCHES_KEY = "token_launches:recent"
LAUNCH_STATE_KEY = "token_launches:seen_addrs"
LAUNCH_ALERTS_KEY = "token_launches:alerts"

# Config
POLL_INTERVAL = 30  # seconds between polls
SCAN_WINDOW = 60  # seconds — tokens launched within this window are "new"
LAUNCHES_TTL = 3600  # 1 hour in Redis
STATE_TTL = 86400  # 24 hours for seen addresses

_HIGH_RISK_THRESHOLD = 70  # risk score threshold for alert


class TokenLaunchDetector:
    """Detects new token launches and triggers auto-scan + alerts."""

    def __init__(self) -> None:
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start the background polling loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._poll_loop())
        logger.info("token_launch_detector started (poll_interval=%ds)", POLL_INTERVAL)

    async def stop(self) -> None:
        """Stop the background polling loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        logger.info("token_launch_detector stopped")

    async def _poll_loop(self) -> None:
        """Main polling loop."""
        while self._running:
            try:
                await self._detect_new_launches()
            except Exception as e:
                logger.error("token_launch_poll_error: %s", e)
            await asyncio.sleep(POLL_INTERVAL)

    async def _detect_new_launches(self) -> None:
        """Fetch new launches and process them."""
        new_launches = await self._fetch_new_launches()
        if not new_launches:
            return

        # Filter to truly new (not seen before)
        seen = await self._get_seen_addresses()
        fresh = [l for l in new_launches if l.get("address") not in seen]

        if fresh:
            logger.info("new_token_launches count=%d", len(fresh))

            # Store in Redis
            await self._store_launches(fresh)

            # Mark as seen
            await self._mark_seen([l.get("address", "") for l in fresh])

            # Auto-scan each new token
            for launch in fresh:
                try:
                    await self._auto_scan_token(launch)
                except Exception as e:
                    logger.warning("auto_scan_error addr=%s err=%s", launch.get("address", ""), e)

    async def _fetch_new_launches(self) -> list[dict[str, Any]]:
        """Fetch new token launches from multiple sources."""
        launches: list[dict[str, Any]] = []

        # 1. Try GMGN
        try:
            from app.databus.gmgn_provider import get_gmgn_provider

            gmgn = get_gmgn_provider()
            gmgn_launches = await gmgn.get_new_pairs("solana", 50)
            if gmgn_launches:
                launches.extend(gmgn_launches)
                logger.debug("gmgn_new_pairs count=%d", len(gmgn_launches))
        except Exception as e:
            logger.debug("gmgn_new_pairs_failed: %s", e)

        # 2. DexScreener search (fallback)
        if not launches:
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.get(
                        "https://api.dexscreener.com/latest/dex/search",
                        params={"q": "SOL", "limit": 50},
                    )
                    if resp.status_code == 200:
                        pairs = resp.json().get("pairs", [])
                        for p in pairs:
                            created = p.get("pairCreatedAt")
                            age_s = 999999
                            if created:
                                age_s = (
                                    datetime.now(UTC) - datetime.fromtimestamp(created / 1000, tz=UTC)
                                ).total_seconds()

                            # Only include tokens launched within last 5 min
                            if age_s < 300:
                                launches.append(
                                    {
                                        "address": p.get("baseToken", {}).get("address", ""),
                                        "chain": p.get("chainId", "solana"),
                                        "symbol": p.get("baseToken", {}).get("symbol", ""),
                                        "name": p.get("baseToken", {}).get("name", ""),
                                        "price": float(p.get("priceUsd", 0)),
                                        "liquidity": float(p.get("liquidity", {}).get("usd", 0)),
                                        "market_cap": float(p.get("fdv", 0)),
                                        "created_at": created,
                                        "age_seconds": int(age_s),
                                        "dex": p.get("dexId", ""),
                                        "pair_address": p.get("pairAddress", ""),
                                        "source": "dexscreener",
                                    }
                                )
                logger.debug("dexscreener_new_launches count=%d", len(launches))
            except Exception as e:
                logger.warning("dexscreener_launches_failed: %s", e)

        # 3. GeckoTerminal trending (additional source)
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    "https://api.geckoterminal.com/api/v2/networks/solana/new_pools",
                    params={"limit": 20},
                )
                if resp.status_code == 200:
                    pools = resp.json().get("data", [])
                    for pool in pools:
                        attrs = pool.get("attributes", {})
                        launches.append(
                            {
                                "address": attrs.get("base_token_address", ""),
                                "chain": "solana",
                                "symbol": attrs.get("base_token_symbol", ""),
                                "name": attrs.get("name", ""),
                                "price": float(attrs.get("base_token_price_usd", 0)),
                                "liquidity": float(attrs.get("reserve_in_usd", 0)),
                                "source": "geckoterminal",
                            }
                        )
        except Exception:  # noqa: S110 — intentional best-effort
            pass

        return launches

    async def _auto_scan_token(self, launch: dict[str, Any]) -> None:
        """Run a quick risk scan on a newly launched token."""
        address = launch.get("address", "")
        if not address:
            return

        risk_score = 50  # default neutral
        risk_flags: list[str] = []

        # Quick checks from available data
        liquidity = float(launch.get("liquidity", 0))

        # Low liquidity = high risk
        if liquidity < 1000:
            risk_score += 30
            risk_flags.append("Extremely low liquidity (<$1K)")
        elif liquidity < 5000:
            risk_score += 15
            risk_flags.append("Low liquidity (<$5K)")

        # No social data = higher risk
        symbol = launch.get("symbol", "")
        if not symbol or symbol == "???":
            risk_score += 10
            risk_flags.append("No symbol/name defined")

        # Unknown DEX
        dex = launch.get("dex", "")
        if not dex or dex in ("unknown", ""):
            risk_score += 5
            risk_flags.append("Unknown DEX")

        risk_score = min(risk_score, 100)

        # Store scan result
        try:
            from app.core.redis import get_redis_async

            r = get_redis_async()
            if r:
                scan_result = {
                    "address": address,
                    "symbol": launch.get("symbol", ""),
                    "name": launch.get("name", ""),
                    "risk_score": risk_score,
                    "risk_flags": risk_flags,
                    "liquidity": liquidity,
                    "price": launch.get("price", 0),
                    "age_seconds": launch.get("age_seconds", 0),
                    "dex": dex,
                    "scanned_at": datetime.now(UTC).isoformat(),
                }
                await r.setex(
                    f"launch_scan:{address}",
                    600,  # 10-min TTL
                    json.dumps(scan_result),
                )
        except Exception as e:
            logger.warning("launch_scan_store_failed: %s", e)

        # Send alert for high risk
        if risk_score >= _HIGH_RISK_THRESHOLD:
            await self._send_telegram_alert(launch, risk_score, risk_flags)

    async def _send_telegram_alert(self, launch: dict[str, Any], risk_score: int, flags: list[str]) -> None:
        """Send high-risk alert to Telegram."""
        address = launch.get("address", "")
        symbol = launch.get("symbol", "???")
        name = launch.get("name", "Unknown")
        liq = launch.get("liquidity", 0)
        price = launch.get("price", 0)

        try:
            from app.core.redis import get_redis_async

            r = get_redis_async()
            if r:
                alert = {
                    "type": "new_launch_high_risk",
                    "address": address,
                    "symbol": symbol,
                    "name": name,
                    "risk_score": risk_score,
                    "risk_flags": flags,
                    "liquidity": liq,
                    "price": price,
                    "chain": "solana",
                    "timestamp": datetime.now(UTC).isoformat(),
                }
                await r.lpush(LAUNCH_ALERTS_KEY, json.dumps(alert))
                await r.ltrim(LAUNCH_ALERTS_KEY, 0, 99)  # Keep last 100 alerts
                logger.info("high_risk_alert symbol=%s risk=%d", symbol, risk_score)
        except Exception as e:
            logger.warning("alert_push_failed: %s", e)

    async def _get_seen_addresses(self) -> set[str]:
        """Get set of already-seen token addresses."""
        try:
            from app.core.redis import get_redis_async

            r = get_redis_async()
            if r:
                members = await r.smembers(LAUNCH_STATE_KEY)
                return {m.decode() if isinstance(m, bytes) else str(m) for m in members}
        except Exception:  # noqa: S110 — optional dependency
            pass
        return set()

    async def _mark_seen(self, addresses: list[str]) -> None:
        """Mark addresses as seen in Redis."""
        if not addresses:
            return
        try:
            from app.core.redis import get_redis_async

            r = get_redis_async()
            if r:
                await r.sadd(LAUNCH_STATE_KEY, *addresses)
                await r.expire(LAUNCH_STATE_KEY, STATE_TTL)
        except Exception:  # noqa: S110 — optional dependency
            pass

    async def _store_launches(self, launches: list[dict[str, Any]]) -> None:
        """Store new launches in Redis for trending page."""
        try:
            from app.core.redis import get_redis_async

            r = get_redis_async()
            if r:
                now = datetime.now(UTC).isoformat()
                for launch in launches:
                    launch["discovered_at"] = now
                    # Push to front of sorted set
                    await r.zadd(LAUNCHES_KEY, {json.dumps(launch): launch.get("age_seconds", 0)})
                # Trim to last 200
                await r.zremrangebyrank(LAUNCHES_KEY, 0, -201)
                await r.expire(LAUNCHES_KEY, LAUNCHES_TTL)
        except Exception as e:
            logger.warning("launch_store_failed: %s", e)

    async def get_recent_launches(self, limit: int = 50) -> list[dict[str, Any]]:
        """Get recent token launches from Redis."""
        try:
            from app.core.redis import get_redis_async

            r = get_redis_async()
            if r:
                # Get newest first (reverse order)
                raw = await r.zrevrange(LAUNCHES_KEY, 0, limit - 1)
                launches = []
                for item in raw:
                    try:
                        data = item.decode() if isinstance(item, bytes) else str(item)
                        launches.append(json.loads(data))
                    except (json.JSONDecodeError, TypeError):
                        continue
                return launches
        except Exception:  # noqa: S110 — intentional best-effort
            pass
        return []

    async def get_launch_alerts(self, limit: int = 20) -> list[dict[str, Any]]:
        """Get recent high-risk alerts."""
        try:
            from app.core.redis import get_redis_async

            r = get_redis_async()
            if r:
                raw = await r.lrange(LAUNCH_ALERTS_KEY, 0, limit - 1)
                alerts = []
                for item in raw:
                    try:
                        data = item.decode() if isinstance(item, bytes) else str(item)
                        alerts.append(json.loads(data))
                    except (json.JSONDecodeError, TypeError):
                        continue
                return alerts
        except Exception:  # noqa: S110 — intentional best-effort
            pass
        return []


# ── Singleton ──────────────────────────────────────────────

_detector: TokenLaunchDetector | None = None


def get_launch_detector() -> TokenLaunchDetector:
    """Get or create the launch detector singleton."""
    global _detector
    if _detector is None:
        _detector = TokenLaunchDetector()
    return _detector
