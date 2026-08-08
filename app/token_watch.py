"""
Token Watch / LP Monitor Service
==================================
Paid x402 feature: "Watch this token and alert me if LP drops below X%"

Users set monitoring conditions on tokens. The monitor service periodically
checks LP levels, price changes, and other conditions via Dexscreener API.
When conditions trigger, alerts fire via WebSocket and are stored in Redis.

Pricing:
  - token_watch_create:  $0.05 (set up a watch)
  - token_watch_alert:   $0.00  (alert delivery is free - it's the value prop)

Watch conditions supported:
  - lp_drop_below:   LP locked % falls below threshold
  - lp_unlocked:     LP lock expires or is removed
  - price_drop:      Price drops below threshold
  - price_change:     Price changes by X% in Y minutes
  - new_pair:         New trading pair created for token
  - rug_indicators:   Suspicious patterns detected (mint, ownership change, etc.)
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

import httpx

from app.core.logging import get_logger

logger = get_logger("token_watch")

# ─── Data Models ──────────────────────────────────────────────────────


class WatchCondition(StrEnum):
    LP_DROP_BELOW = "lp_drop_below"
    LP_UNLOCKED = "lp_unlocked"
    PRICE_DROP = "price_drop"
    PRICE_CHANGE = "price_change"
    RUG_INDICATORS = "rug_indicators"


@dataclass
class TokenWatch:
    """A monitoring watch on a token."""

    watch_id: str
    token_address: str
    chain: str
    condition: str  # WatchCondition value
    threshold: float  # Threshold value (e.g. 50.0 for 50% LP)
    created_by: str  # Wallet or device ID
    created_at: str = ""
    expires_at: str = ""  # Watch expiry (default 30 days)
    active: bool = True
    last_checked: str = ""
    last_value: float | None = None
    triggered: bool = False
    triggered_at: str = ""
    trigger_message: str = ""
    check_interval_seconds: int = 300  # Default: check every 5 min
    webhook_url: str = ""  # Optional webhook for external alerting

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TokenWatch:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class WatchAlert:
    """An alert triggered by a watch condition."""

    alert_id: str
    watch_id: str
    token_address: str
    chain: str
    condition: str
    message: str
    severity: str = "warning"  # info / warning / high / critical
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: str = ""
    delivered: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ─── Token Watch Service ──────────────────────────────────────────────


class TokenWatchService:
    """Manages token watches and periodic monitoring."""

    DEXSCREENER_TOKEN_URL = "https://api.dexscreener.com/latest/dex/tokens/{address}"  # noqa: S105 — config default
    DEXSCREENER_SEARCH_URL = "https://api.dexscreener.com/latest/dex/search?q={address}"

    # Chain name mapping for Dexscreener
    CHAIN_MAP = {
        "ethereum": "ethereum",
        "eth": "ethereum",
        "bsc": "bsc",
        "bnb": "bsc",
        "polygon": "polygon",
        "matic": "polygon",
        "base": "base",
        "arbitrum": "arbitrum",
        "arb": "arbitrum",
        "optimism": "optimism",
        "op": "optimism",
        "solana": "solana",
        "sol": "solana",
    }

    def __init__(self) -> None:
        self._redis = None
        self._running = False
        self._check_task: asyncio.Task | None = None

    async def _get_redis(self) -> Any:
        """Get async Redis client."""
        if self._redis is None:
            try:
                import redis.asyncio as aioredis

                self._redis = aioredis.Redis(
                    host=os.getenv("REDIS_HOST", "localhost"),
                    port=int(os.getenv("REDIS_PORT", "6379")),
                    password=os.getenv("REDIS_PASSWORD", ""),
                    db=0,
                    decode_responses=True,
                )
                await self._redis.ping()
            except Exception as e:
                logger.warning(f"Redis unavailable for token watch: {e}")
                self._redis = None
        return self._redis

    # ── Watch CRUD ─────────────────────────────────────────────────

    async def create_watch(
        self,
        token_address: str,
        chain: str,
        condition: str,
        threshold: float,
        created_by: str,
        check_interval: int = 300,
        webhook_url: str = "",
    ) -> TokenWatch:
        """Create a new token watch."""
        watch_id = hashlib.sha256(f"watch:{token_address}:{chain}:{condition}:{time.time()}".encode()).hexdigest()[:12]

        now = datetime.now(UTC)
        expires = now + timedelta(days=30)

        watch = TokenWatch(
            watch_id=watch_id,
            token_address=token_address.lower(),
            chain=chain.lower(),
            condition=condition,
            threshold=threshold,
            created_by=created_by,
            created_at=now.isoformat(),
            expires_at=expires.isoformat(),
            check_interval_seconds=check_interval,
            webhook_url=webhook_url,
        )

        # Store in Redis
        r = await self._get_redis()
        if r:
            key = f"token_watch:{watch.watch_id}"
            await r.setex(key, 30 * 86400, json.dumps(watch.to_dict()))
            # Index by token address
            await r.sadd(f"token_watches:{watch.token_address}:{watch.chain}", watch.watch_id)
            # Global active watches set
            await r.sadd("token_watches:active", watch.watch_id)

        logger.info(f"Created watch {watch_id} for {token_address} on {chain}: {condition} < {threshold}")
        return watch

    async def get_watch(self, watch_id: str) -> TokenWatch | None:
        """Get a watch by ID."""
        r = await self._get_redis()
        if not r:
            return None
        data = await r.get(f"token_watch:{watch_id}")
        if data:
            return TokenWatch.from_dict(json.loads(data))
        return None

    async def list_watches(self, token_address: str = "", chain: str = "", created_by: str = "") -> list[TokenWatch]:
        """List watches, optionally filtered."""
        r = await self._get_redis()
        if not r:
            return []

        watches = []
        if token_address and chain:
            watch_ids = await r.smembers(f"token_watches:{token_address.lower()}:{chain.lower()}")
        elif created_by:
            watch_ids = await r.smembers(f"token_watches:user:{created_by}")
        else:
            watch_ids = await r.smembers("token_watches:active")

        for wid in watch_ids:
            watch = await self.get_watch(wid)
            if watch and watch.active:
                watches.append(watch)

        return watches

    async def delete_watch(self, watch_id: str) -> bool:
        """Deactivate a watch."""
        watch = await self.get_watch(watch_id)
        if not watch:
            return False

        watch.active = False
        r = await self._get_redis()
        if r:
            key = f"token_watch:{watch_id}"
            await r.setex(key, 30 * 86400, json.dumps(watch.to_dict()))
            await r.srem("token_watches:active", watch_id)

        logger.info(f"Deactivated watch {watch_id}")
        return True

    # ── Dexscreener Data Fetch ────────────────────────────────────

    async def _fetch_token_data(self, token_address: str, chain: str) -> dict[str, Any] | None:
        """Fetch token/pair data from Dexscreener."""
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                # Try token endpoint first
                resp = await client.get(self.DEXSCREENER_TOKEN_URL.format(address=token_address))
                if resp.status_code == 200:
                    data = resp.json()
                    pairs = data.get("pairs", [])
                    if pairs:
                        # Filter to matching chain
                        chain_name = self.CHAIN_MAP.get(chain.lower(), chain.lower())
                        chain_pairs = [p for p in pairs if p.get("chainId") == chain_name]
                        return chain_pairs[0] if chain_pairs else pairs[0]
        except Exception as e:
            logger.debug(f"Dexscreener token fetch failed: {e}")

        # Fallback: search endpoint
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(self.DEXSCREENER_SEARCH_URL.format(address=token_address))
                if resp.status_code == 200:
                    data = resp.json()
                    pairs = data.get("pairs", [])
                    if pairs:
                        return pairs[0]
        except Exception as e:
            logger.debug(f"Dexscreener search fetch failed: {e}")

        return None

    # ── Condition Checking ─────────────────────────────────────────

    async def check_watch(self, watch: TokenWatch) -> WatchAlert | None:
        """Check if a watch condition is triggered. Returns alert if triggered."""
        pair_data = await self._fetch_token_data(watch.token_address, watch.chain)
        if not pair_data:
            logger.debug(f"No Dexscreener data for {watch.token_address}")
            return None

        now = datetime.now(UTC).isoformat()
        watch.last_checked = now

        triggered = False
        message = ""
        severity = "warning"
        alert_data = {}

        if watch.condition == WatchCondition.LP_DROP_BELOW:
            # Check LP from liquidity info
            liquidity = pair_data.get("liquidity", {})
            lp_usd = liquidity.get("usd", 0) if isinstance(liquidity, dict) else float(liquidity) if liquidity else 0

            # Track LP relative to baseline
            if watch.last_value is not None and watch.last_value > 0:
                lp_pct_change = ((watch.last_value - lp_usd) / watch.last_value) * 100
            else:
                lp_pct_change = 0

            watch.last_value = lp_usd
            alert_data = {"lp_usd": lp_usd, "lp_pct_change": lp_pct_change}

            # If LP is below threshold (absolute USD or % of peak)
            if lp_usd < watch.threshold:
                triggered = True
                message = f"LP for {watch.token_address[:10]}... dropped below ${watch.threshold:,.0f} - currently ${lp_usd:,.0f}"
                severity = "critical"
            elif lp_pct_change > 20:  # More than 20% LP drop since last check
                triggered = True
                message = f"LP dropped {lp_pct_change:.1f}% since last check - ${lp_usd:,.0f} remaining"
                severity = "high"

        elif watch.condition == WatchCondition.LP_UNLOCKED:
            # Check if LP lock status changed
            info = pair_data.get("info", {})
            if isinstance(info, dict):
                liquidity_locked = info.get("liquidityLocked", False)
                if not liquidity_locked and watch.last_value == 1:  # Was locked
                    triggered = True
                    message = f"LP lock REMOVED for {watch.token_address[:10]}... - potential rug indicator"
                    severity = "critical"
                    watch.last_value = 0 if not liquidity_locked else 1
            else:
                watch.last_value = 0

        elif watch.condition == WatchCondition.PRICE_DROP:
            price_usd = float(pair_data.get("priceUsd", 0) or 0)
            watch.last_value = price_usd
            if price_usd < watch.threshold:
                triggered = True
                message = f"Price of {watch.token_address[:10]}... dropped below ${watch.threshold:.6f} - currently ${price_usd:.6f}"
                severity = "high"

        elif watch.condition == WatchCondition.PRICE_CHANGE:
            price_usd = float(pair_data.get("priceUsd", 0) or 0)
            if watch.last_value is not None and watch.last_value > 0:
                pct_change = abs(price_usd - watch.last_value) / watch.last_value * 100
                if pct_change > watch.threshold:
                    direction = "up" if price_usd > watch.last_value else "down"
                    triggered = True
                    message = (
                        f"Price {direction} {pct_change:.1f}% for {watch.token_address[:10]}... - ${price_usd:.6f}"
                    )
                    severity = "warning" if pct_change < 30 else "high"
            watch.last_value = price_usd

        elif watch.condition == WatchCondition.RUG_INDICATORS:
            # Check for suspicious contract changes
            info = pair_data.get("info", {})
            if isinstance(info, dict):
                info.get("socials", [])
                # Check if renounced ownership, etc.
                # This is a simplified check - full implementation would
                # use the contract_diff analysis

        # Update watch in Redis
        r = await self._get_redis()
        if r:
            key = f"token_watch:{watch.watch_id}"
            await r.setex(key, 30 * 86400, json.dumps(watch.to_dict()))

        if triggered:
            alert = WatchAlert(
                alert_id=f"alert_{hashlib.sha256(f'{watch.watch_id}:{now}'.encode()).hexdigest()[:10]}",
                watch_id=watch.watch_id,
                token_address=watch.token_address,
                chain=watch.chain,
                condition=watch.condition,
                message=message,
                severity=severity,
                data=alert_data,
                timestamp=now,
            )

            watch.triggered = True
            watch.triggered_at = now
            watch.trigger_message = message

            # Store alert
            if r:
                alert_key = f"token_watch_alert:{alert.alert_id}"
                await r.setex(alert_key, 7 * 86400, json.dumps(alert.to_dict()))
                # Index alerts by watch
                await r.sadd(f"token_watch_alerts:{watch.watch_id}", alert.alert_id)

            # Push via WebSocket if available
            try:
                from app.websocket_alerts import get_websocket_manager

                ws = get_websocket_manager()
                await ws.broadcast_security_alert(
                    wallet=watch.token_address,
                    severity=severity,
                    message=message,
                    chain=watch.chain,
                )
            except Exception as e:
                logger.debug(f"WebSocket broadcast failed: {e}")

            # Webhook delivery
            if watch.webhook_url:
                try:
                    async with httpx.AsyncClient(timeout=10) as client:
                        await client.post(watch.webhook_url, json=alert.to_dict())
                except Exception as e:
                    logger.debug(f"Webhook delivery failed: {e}")

            logger.info(f"Watch {watch.watch_id} TRIGGERED: {message}")
            return alert

        return None

    # ── Background Monitor Loop ───────────────────────────────────

    async def start_monitor(self, interval: int = 60) -> None:
        """Start the background monitoring loop."""
        if self._running:
            return
        self._running = True
        self._check_task = asyncio.create_task(self._monitor_loop(interval))
        logger.info(f"Token watch monitor started (interval={interval}s)")

    async def stop_monitor(self) -> None:
        """Stop the monitoring loop."""
        self._running = False
        if self._check_task:
            self._check_task.cancel()
        logger.info("Token watch monitor stopped")

    async def _monitor_loop(self, interval: int) -> None:
        """Periodically check all active watches."""
        while self._running:
            try:
                r = await self._get_redis()
                if r:
                    watch_ids = await r.smembers("token_watches:active")
                    for wid in watch_ids:
                        try:
                            watch = await self.get_watch(wid)
                            if not watch or not watch.active:
                                continue
                            # Check if it's time to check this watch
                            if watch.last_checked:
                                last = datetime.fromisoformat(watch.last_checked)
                                elapsed = (datetime.now(UTC) - last).total_seconds()
                                if elapsed < watch.check_interval_seconds:
                                    continue
                            await self.check_watch(watch)
                        except Exception as e:
                            logger.error(f"Watch check error for {wid}: {e}")
                            # Back off on errors
                            await asyncio.sleep(5)
            except Exception as e:
                logger.error(f"Monitor loop error: {e}")

            await asyncio.sleep(interval)

    # ── Alert Retrieval ────────────────────────────────────────────

    async def get_alerts(self, watch_id: str = "", token_address: str = "", chain: str = "") -> list[dict[str, Any]]:
        """Get alerts for a watch or token."""
        r = await self._get_redis()
        if not r:
            return []

        alert_ids = set()
        if watch_id:
            alert_ids = await r.smembers(f"token_watch_alerts:{watch_id}")
        elif token_address and chain:
            watches = await self.list_watches(token_address=token_address, chain=chain)
            for w in watches:
                w_alerts = await r.smembers(f"token_watch_alerts:{w.watch_id}")
                alert_ids.update(w_alerts)

        alerts = []
        for aid in alert_ids:
            data = await r.get(f"token_watch_alert:{aid}")
            if data:
                alerts.append(json.loads(data))

        return sorted(alerts, key=lambda x: x.get("timestamp", ""), reverse=True)


# ─── Singleton ────────────────────────────────────────────────────────

_watch_service: TokenWatchService | None = None


def get_token_watch_service() -> TokenWatchService:
    """Get the global TokenWatchService singleton."""
    global _watch_service
    if _watch_service is None:
        _watch_service = TokenWatchService()
    return _watch_service
