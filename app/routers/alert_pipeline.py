#!/usr/bin/env python3
"""
Auto-Alerting Pipeline for RMI Platform
Send real-time alerts from detections to Telegram + webhooks
"""

import asyncio
import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime
from enum import Enum
from typing import Any

import httpx
import redis
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.logging import get_logger

logger = get_logger(__name__)


class AlertLevel(Enum):
    CRITICAL = "CRITICAL"
    WARNING = "WARNING"
    INFO = "INFO"
    DEBUG = "DEBUG"


@dataclass
class Alert:
    source: str
    alert_type: str
    level: str  # "CRITICAL", "WARNING", "INFO", "DEBUG"
    wallet_address: str
    token_symbol: str
    message: str
    timestamp: str
    confidence: float = 0.0
    metadata: dict[str, Any] = None

    def to_dict(self) -> dict:
        return asdict(self)

    def to_telegram_message(self) -> str:
        """Format alert as Telegram message"""
        level_emoji = {"CRITICAL": "🚨", "WARNING": "⚠️", "INFO": "ℹ️", "DEBUG": "🔍"}
        return (
            f"{level_emoji.get(self.level, 'ℹ️')} *{self.alert_type.upper()}* [{self.level}]\n\n"
            f" Wallet: `{self.wallet_address[:10]}...{self.wallet_address[-6:]}`\n"
            f" Token: `{self.token_symbol}`\n"
            f" Confidence: {self.confidence:.0%}\n\n"
            f"{self.message}\n\n"
            f"📅 {self.timestamp}"
        )


# Configuration
REDIS_HOST = os.getenv("REDIS_HOST", "172.20.0.3")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_ALERTS_CHANNEL = os.getenv("TELEGRAM_ALERTS_CHANNEL", "")  # -100xxx format

WEBHOOK_URLS = [
    os.getenv("ALERT_WEBHOOK_URL", ""),
    os.getenv("SLACK_ALERT_WEBHOOK", ""),
]

# Rate limiting (max alerts per minute)
RATE_LIMIT_MAX = 5
RATE_LIMIT_WINDOW = 60  # seconds

# API Router
router = APIRouter(prefix="/api/v1/alerts", tags=["alerting"])


# Rate limit tracking via Redis
def get_rate_limit_key() -> str:
    """Get Redis key for rate limiting"""
    return "alerting:rate_limit:counts"


def check_and_update_rate_limit() -> bool:
    """Check rate limit and increment counter. Returns True if under limit."""
    try:
        r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, password=REDIS_PASSWORD or None, decode_responses=True)
        key = get_rate_limit_key()
        current_time = int(datetime.now().timestamp())
        window_key = f"{key}:{current_time // RATE_LIMIT_WINDOW}"

        count = r.incr(window_key)
        r.expire(window_key, RATE_LIMIT_WINDOW * 2)

        return not count > RATE_LIMIT_MAX
    except Exception as e:
        logger.warning(f"Rate limit check failed: {e}")
        return True  # Allow on error


# Send to Telegram
async def send_to_telegram(alert: Alert) -> bool:
    """Send alert to Telegram channel"""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_ALERTS_CHANNEL:
        logger.info("Telegram config missing, skipping")
        return False

    if not check_and_update_rate_limit():
        logger.info("Rate limit exceeded, alert queued")
        return False

    try:
        import telegram

        bot = telegram.Bot(token=TELEGRAM_BOT_TOKEN)
        message = alert.to_telegram_message()
        await bot.send_message(chat_id=TELEGRAM_ALERTS_CHANNEL, text=message, parse_mode="MarkdownV2")
        return True
    except Exception as e:
        logger.warning(f"Telegram send failed: {e}")
        return False


# Send to webhooks
async def send_to_webhooks(alert: Alert) -> list[dict]:
    """Send alert to configured webhooks"""
    results = []

    for url in WEBHOOK_URLS:
        if not url:
            continue
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    url,
                    json={"alert": alert.to_dict(), "timestamp": datetime.now().isoformat()},
                    headers={"Content-Type": "application/json"},
                )
                results.append(
                    {
                        "url": url,
                        "status": response.status_code,
                        "success": response.status_code == 200,
                    }
                )
        except Exception as e:
            results.append({"url": url, "status": "error", "error": str(e), "success": False})

    return results


# Alert storage in Redis
def cache_alert(alert: Alert, ttl: int = 3600) -> bool:
    """Cache alert in Redis for downstream processing"""
    try:
        r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, password=REDIS_PASSWORD or None, decode_responses=True)
        key = f"alerts:{alert.alert_type}:{alert.wallet_address}"
        alert_json = json.dumps(alert.to_dict())
        r.hset(
            key,
            mapping={
                "alert_json": alert_json,
                "created_at": datetime.now().isoformat(),
                "level": alert.level,
            },
        )
        r.expire(key, ttl)
        return True
    except Exception as e:
        logger.warning(f"Cache alert failed: {e}")
        return False


# Process and dispatch alert
async def process_alert(alert: Alert) -> dict[str, Any]:
    """Process alert: send to Telegram, webhooks, cache"""
    results = {
        "telegram": False,
        "webhooks": [],
        "cached": False,
        "timestamp": datetime.now().isoformat(),
    }

    # Send to Telegram
    if alert.level == "CRITICAL":
        results["telegram"] = await send_to_telegram(alert)

    # Send to webhooks (always for all levels)
    if alert.level in ["CRITICAL", "WARNING"]:
        results["webhooks"] = await send_to_webhooks(alert)

    # Cache alert
    results["cached"] = cache_alert(alert)

    return results


# API Endpoints
class AlertCreateRequest(BaseModel):
    source: str
    alert_type: str
    level: str = "INFO"
    wallet_address: str
    token_symbol: str
    message: str
    confidence: float = 0.0
    metadata: dict[str, Any] = None


@router.post("/create")
async def create_alert(request: AlertCreateRequest):
    """Create and dispatch a new alert"""
    alert = Alert(
        source=request.source,
        alert_type=request.alert_type,
        level=request.level,
        wallet_address=request.wallet_address,
        token_symbol=request.token_symbol,
        message=request.message,
        timestamp=datetime.now().isoformat(),
        confidence=request.confidence,
        metadata=request.metadata or {},
    )

    results = await process_alert(alert)

    return {
        "success": True,
        "alert_id": f"{alert.alert_type}:{alert.wallet_address}",
        "dispatch": results,
    }


@router.post("/webhook")
async def webhook_alert(notification: dict[str, Any]):
    """Receive external webhook and convert to internal alert"""
    source = notification.get("source", "external")
    alert_type = notification.get("alert_type", "unknown")
    level = notification.get("level", "INFO")

    alert = Alert(
        source=source,
        alert_type=alert_type,
        level=level,
        wallet_address=notification.get("wallet_address", "unknown"),
        token_symbol=notification.get("token_symbol", "unknown"),
        message=notification.get("message", ""),
        timestamp=datetime.now().isoformat(),
        confidence=notification.get("confidence", 0.0),
        metadata=notification.get("metadata", {}),
    )

    results = await process_alert(alert)

    return {
        "success": True,
        "alert_id": f"{alert.alert_type}:{alert.wallet_address}",
        "dispatch": results,
    }


@router.get("/status")
async def get_alert_status():
    """Get current alerting system status"""
    try:
        r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, password=REDIS_PASSWORD or None, decode_responses=True)
        total_alerts = r.dbsize()

        # Count alerts by type
        alert_counts = {}
        for key in r.scan_iter("alerts:*"):
            if ":" in key:
                alert_type = key.split(":")[1]
                alert_counts[alert_type] = alert_counts.get(alert_type, 0) + 1

        return {
            "status": "ok",
            "redis_connected": True,
            "total_alerts_cached": total_alerts,
            "alerts_by_type": alert_counts,
            "rate_limit": {"max_per_minute": RATE_LIMIT_MAX, "window_seconds": RATE_LIMIT_WINDOW},
            "webhooks_configured": len([w for w in WEBHOOK_URLS if w]),
            "telegram_channel": TELEGRAM_ALERTS_CHANNEL if TELEGRAM_ALERTS_CHANNEL else "not_configured",
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


@router.get("/recent")
async def get_recent_alerts(limit: int = 10):
    """Get recent alerts from cache"""
    try:
        r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, password=REDIS_PASSWORD or None, decode_responses=True)

        alerts = []
        for key in r.scan_iter("alerts:*", count=limit):
            alert_data = r.hgetall(key)
            if alert_data and "alert_json" in alert_data:
                alert = json.loads(alert_data["alert_json"])
                alerts.append(alert)

        # Sort by timestamp (newest first)
        alerts.sort(key=lambda x: x.get("timestamp", ""), reverse=True)

        return {"success": True, "count": len(alerts), "alerts": alerts[:limit]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Background task: monitor alert queue
async def alert_pipeline_monitor():
    """Monitor Redis for new alerts from agents"""
    logger.info(f"[{datetime.now().isoformat()}] Alert pipeline monitor started")
    while True:
        try:
            r = redis.Redis(
                host=REDIS_HOST,
                port=REDIS_PORT,
                password=REDIS_PASSWORD or None,
                decode_responses=True,
            )

            # Check for alerts in queue
            alert_json = r.rpop("social_monitor_queue")
            if alert_json:
                alert = json.loads(alert_json)
                logger.info(f"Processing alert from queue: {alert.get('alert_type', 'unknown')}")
                # Process alert through pipeline
                result = await process_alert(Alert(**alert))
                logger.info(f"Dispatch result: {result}")
            alert_json = r.rpop("chain_walker_queue")
            if alert_json:
                alert = json.loads(alert_json)
                logger.info(f"Processing chain-walker alert: {alert.get('alert_type', 'unknown')}")
                result = await process_alert(Alert(**alert))
                logger.info(f"Dispatch result: {result}")
            # Check syndicate detection alerts
            alert_json = r.rpop("syndicate_alerts")
            if alert_json:
                alert = json.loads(alert_json)
                logger.info(f"Processing syndicate alert: {alert.get('wallet_address', 'unknown')}")
                result = await process_alert(Alert(**alert))
                logger.info(f"Dispatch result: {result}")
            await asyncio.sleep(5)  # Poll every 5 seconds

        except Exception as e:
            logger.warning(f"Alert pipeline monitor error: {e}")
            await asyncio.sleep(10)


def get_alert_pipeline_router():
    """Get the alerting router"""
    return router
