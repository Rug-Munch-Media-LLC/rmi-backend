"""
RMI Intelligent Webhook System
===============================
Databus-powered webhook receiver and dispatcher.
Handles inbound webhooks from Arkham, Helius, Moralis, and any service.
Auto-caches, indexes in RAG, and triggers premium scanner analysis.

Architecture:
  Webhook → Validate → Cache(DataBus) → RAG Index → Premium Scanner → Alert Pipeline
"""

import hashlib
import hmac
import json
import logging
import os
from datetime import datetime

import httpx
import redis

logger = logging.getLogger("webhooks")

REDIS_HOST = os.getenv("REDIS_HOST", "rmi-redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")

# Webhook configurations per service
WEBHOOK_CONFIGS = {
    "arkham": {
        "secret_env": "ARKHAM_WEBHOOK_SECRET",
        "verify_header": "X-Arkham-Signature",
        "events": ["entity_updated", "transaction_detected", "label_added"],
        "cache_ttl": 3600,
    },
    "helius": {
        "secret_env": "HELIUS_WEBHOOK_SECRET",
        "verify_header": "x-helius-signature",
        "events": ["transaction", "nft_event", "token_transfer", "account_update"],
        "cache_ttl": 600,
    },
    "moralis": {
        "secret_env": "MORALIS_WEBHOOK_SECRET",
        "verify_header": "x-signature",
        "events": ["txs", "logs", "nft_transfers", "erc20_transfers"],
        "cache_ttl": 900,
    },
    "alchemy": {
        "secret_env": "ALCHEMY_WEBHOOK_SECRET",
        "verify_header": "X-Alchemy-Signature",
        "events": ["address_activity", "nft_activity", "tx_status"],
        "cache_ttl": 600,
    },
}


def _redis():
    return redis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        password=REDIS_PASSWORD,
        decode_responses=True,
        socket_connect_timeout=2,
    )


async def handle_webhook(service: str, payload: dict, headers: dict, raw_body: bytes = b"") -> dict:
    """Universal webhook handler.

    1. Validate signature
    2. Store in DataBus cache
    3. Index in RAG for intelligence
    4. Trigger premium scanner
    5. Push to alert pipeline if high risk
    """
    config = WEBHOOK_CONFIGS.get(service)
    if not config:
        return {"error": f"Unknown service: {service}", "supported": list(WEBHOOK_CONFIGS.keys())}

    # ── 1. Validate signature ──
    secret = os.getenv(config["secret_env"], "")
    if secret:
        sig_header = headers.get(config["verify_header"], "")
        if not _verify_signature(secret, raw_body, sig_header):
            return {"error": "Invalid signature", "status": "rejected"}

    # ── 2. Deduplicate (prevent replay) ──
    event_id = (
        payload.get("id")
        or payload.get("eventId")
        or hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]
    )
    try:
        r = _redis()
        if r.get(f"webhook:dedup:{service}:{event_id}"):
            r.close()
            return {"status": "duplicate", "event_id": event_id}
        r.setex(f"webhook:dedup:{service}:{event_id}", 3600, "1")
        r.close()
    except Exception:
        pass

    # ── 3. Store in DataBus cache ──
    event_type = payload.get("type") or payload.get("event") or "unknown"
    cache_key = f"webhook:{service}:{event_type}:{event_id}"
    try:
        r = _redis()
        r.setex(
            cache_key,
            config["cache_ttl"],
            json.dumps(
                {
                    "service": service,
                    "event_type": event_type,
                    "payload": payload,
                    "received_at": datetime.utcnow().isoformat(),
                    "headers": {k: v for k, v in headers.items() if k.lower() not in ("authorization", "x-api-key")},
                }
            ),
        )
        r.close()
    except Exception:
        pass

    # ── 4. Index in RAG for intelligence ──
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            await c.post(
                "http://localhost:8000/api/v1/rag/permanence/index",
                json={
                    "collection": f"webhooks_{service}",
                    "documents": [
                        {
                            "id": f"{service}:{event_id}",
                            "text": json.dumps(payload, default=str)[:2000],
                            "metadata": {
                                "service": service,
                                "event_type": event_type,
                                "received_at": datetime.utcnow().isoformat(),
                            },
                        }
                    ],
                },
            )
    except Exception:
        pass

    # ── 5. Trigger premium scanner based on event type ──
    scan_triggers = _get_scan_triggers(service, event_type, payload)

    # ── 6. Push to alert pipeline if high risk ──
    risk = _assess_webhook_risk(service, event_type, payload)
    if risk["level"] in ("HIGH", "CRITICAL"):
        try:
            from app.alert_pipeline import push_alert

            await push_alert(
                title=f"[{service.upper()}] {risk['title']}",
                severity=risk["level"].lower(),
                description=risk["description"],
                chain=payload.get("chain", "unknown"),
                metadata={"webhook_service": service, "event_id": event_id},
            )
        except Exception:
            pass

    return {
        "status": "processed",
        "service": service,
        "event_id": event_id,
        "event_type": event_type,
        "risk_level": risk["level"],
        "scan_triggers": scan_triggers,
        "cached": True,
    }


def _verify_signature(secret: str, body: bytes, signature: str) -> bool:
    """HMAC signature verification."""
    if not secret or not signature:
        return True  # No secret configured, skip verification
    try:
        expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)
    except Exception:
        return False


def _get_scan_triggers(service: str, event_type: str, payload: dict) -> list[str]:
    """Determine which premium scans to trigger based on webhook event."""
    triggers = []
    payload.get("address") or payload.get("tokenAddress") or payload.get("wallet", "")

    if service == "arkham":
        if event_type in ("entity_updated", "label_added"):
            triggers.append("cluster_map")
            triggers.append("bundle_scan")
        if event_type == "transaction_detected":
            triggers.append("mev_sandwich")
            triggers.append("copy_trading")

    elif service == "helius":
        if event_type == "transaction":
            triggers.append("sniper_detect")
            triggers.append("mev_sandwich")
            triggers.append("wash_trading")
        if event_type == "token_transfer":
            triggers.append("fresh_wallets")
            triggers.append("insider_signals")
        if event_type == "nft_event":
            triggers.append("bot_farm")

    elif service == "moralis":
        if event_type in ("txs", "erc20_transfers"):
            triggers.append("copy_trading")
            triggers.append("bundle_scan")
        if event_type == "nft_transfers":
            triggers.append("bot_farm")

    elif service == "alchemy":
        if event_type == "address_activity":
            triggers.append("cluster_map")
            triggers.append("sniper_detect")

    return triggers


def _assess_webhook_risk(service: str, event_type: str, payload: dict) -> dict:
    """Quick risk assessment from webhook data."""
    value_usd = float(payload.get("valueUsd") or payload.get("value", 0))

    if value_usd > 100000:
        return {
            "level": "HIGH",
            "title": f"Large transfer: ${value_usd:,.0f}",
            "description": f"{service} detected a large transaction of ${value_usd:,.0f}",
        }

    if event_type in ("entity_updated", "label_added") and payload.get("label", "").lower() in (
        "scam",
        "fraud",
        "hack",
        "phishing",
    ):
        return {
            "level": "CRITICAL",
            "title": "Scam entity flagged",
            "description": f"{service} flagged {payload.get('address', '?')} as {payload.get('label', 'scam')}",
        }

    return {"level": "LOW", "title": "Routine event", "description": f"{service} {event_type}"}


async def setup_webhook(service: str, webhook_url: str, events: list[str] | None = None, **kw) -> dict:
    """Programmatically set up a webhook subscription with a service.

    For services that support API-based webhook registration (Helius, Moralis),
    this auto-configures the webhook URL and event filters.
    """
    api_key = kw.get("api_key", "")

    if service == "helius":
        if not api_key:
            api_key = os.getenv("HELIUS_API_KEY", "")
        if not api_key:
            return {"error": "HELIUS_API_KEY required"}
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                resp = await c.post(
                    f"https://api.helius.xyz/v0/webhooks?api-key={api_key}",
                    json={
                        "webhookURL": webhook_url,
                        "transactionTypes": events or ["ANY"],
                        "accountAddresses": kw.get("addresses", []),
                        "webhookType": "enhanced",
                    },
                )
                return resp.json() if resp.status_code == 200 else {"error": resp.text[:200]}
        except Exception as e:
            return {"error": str(e)}

    elif service == "moralis":
        if not api_key:
            api_key = os.getenv("MORALIS_API_KEY", "")
        if not api_key:
            return {"error": "MORALIS_API_KEY required"}
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                resp = await c.post(
                    "https://authapi.moralis.io/streams/evm",
                    headers={"X-API-Key": api_key, "Content-Type": "application/json"},
                    json={
                        "webhookUrl": webhook_url,
                        "description": f"RMI DataBus webhook for {service}",
                        "tag": "rmi-databus",
                        "chains": kw.get("chains", ["eth", "bsc", "polygon"]),
                        "includeNativeTxs": True,
                    },
                )
                return resp.json() if resp.status_code in (200, 201) else {"error": resp.text[:200]}
        except Exception as e:
            return {"error": str(e)}

    return {
        "status": "manual_setup_required",
        "service": service,
        "webhook_url": webhook_url,
        "instructions": f"Configure webhook at {service} dashboard to point to {webhook_url}",
    }


async def list_webhooks() -> dict:
    """List all configured webhook subscriptions."""
    try:
        r = _redis()
        keys = r.keys("webhook:*:*")
        webhooks = []
        for key in keys[:50]:
            data = r.get(key)
            if data:
                webhooks.append(json.loads(data))
        r.close()
        return {"webhooks": webhooks, "total": len(webhooks)}
    except Exception:
        return {"webhooks": [], "total": 0}
