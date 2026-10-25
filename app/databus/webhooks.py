"""
RMI Intelligent Webhook System
===============================
Databus-powered webhook receiver and dispatcher.
Handles inbound webhooks from Arkham, Helius, Moralis, and any service.
Auto-caches, indexes in RAG, and triggers premium scanner analysis.

Architecture:
  Webhook → Validate → Cache(DataBus) → RAG Index → Premium Scanner → Alert Pipeline

Companion modules (this file is the public façade):
  webhook_config.py   — per-service config, Redis client, error surface
  webhook_verify.py   — fail-closed HMAC + auth-header verification
  webhook_dispatch.py — scan triggers + risk assessment
  webhook_register.py — outbound subscription setup / listing
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from typing import Any

from app.databus.webhook_config import (
    _WEBHOOK_ERRORS,
    REDIS_HOST,
    REDIS_PASSWORD,
    REDIS_PORT,
    WEBHOOK_CONFIGS,
    _redis,
    logger,
)
from app.databus.webhook_dispatch import _assess_webhook_risk, _get_scan_triggers
from app.databus.webhook_register import list_webhooks, setup_webhook
from app.databus.webhook_verify import _verify_auth_header, _verify_signature

__all__ = [
    "REDIS_HOST",
    "REDIS_PASSWORD",
    "REDIS_PORT",
    "WEBHOOK_CONFIGS",
    "handle_webhook",
    "list_webhooks",
    "setup_webhook",
]


async def handle_webhook(
    service: str, payload: dict[str, Any], headers: dict[str, Any], raw_body: bytes = b""
) -> dict[str, Any]:
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

    # ── 1. Validate signature (fail-closed: unconfigured secret => reject) ──
    secret = os.getenv(config["secret_env"], "")
    if not secret:
        return {"error": "Webhook secret not configured", "status": "rejected"}
    sig_header = headers.get(config["verify_header"], "")
    if not _verify_signature(secret, raw_body, sig_header):
        return {"error": "Invalid signature", "status": "rejected"}

    # ── 1b. Validate auth header (e.g., Helius sends authHeader as Bearer token) ──
    auth_env = config.get("auth_env", "")
    if auth_env and not _verify_auth_header(headers, auth_env):
        return {"error": "Invalid auth token", "status": "rejected"}

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
    except _WEBHOOK_ERRORS as exc:
        logger.warning("webhook_dedup_failed", service=service, error=str(exc))

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
                    "received_at": datetime.now(UTC).isoformat(),
                    "headers": {k: v for k, v in headers.items() if k.lower() not in ("authorization", "x-api-key")},
                }
            ),
        )
        r.close()
    except json.JSONDecodeError as exc:
        logger.warning("webhook_cache_store_failed", service=service, error=str(exc))

    # ── 4. Index in RAG for intelligence ──
    # DEPRECATED (v3): the legacy RAG permanence endpoint
    # (``http://localhost:8000/api/v1/rag/permanence/index``) was removed and
    # has no replacement route. Webhook events are still persisted in Redis
    # above; RAG ingestion now happens via Redis channel ``rmi:rag:ingest``.
    # Original dead call removed — do not restore; errors were already swallowed.
    logger.debug("webhook_rag_permanence_skipped (legacy /api/v1/rag/permanence/index removed in v3)")

    # ── 5. Trigger premium scanner based on event type ──
    scan_triggers = _get_scan_triggers(service, event_type, payload)

    # ── 6. Intelligent alert routing (Arkham-style, importance-gated) ──
    # The webhook is parsed (Helius-aware), enriched with wallet labels
    # (exchange/defi/mixer/known actor), scored, gated by the news
    # reranker bands (channels.route_news) and throttled per wallet — only
    # meaningful events surface to the alerts channel. Fail-open: any
    # router error degrades to a skip decision, never a webhook 5xx.
    risk = _assess_webhook_risk(service, event_type, payload)
    routing = ""
    try:
        from app.databus.alert_router import route_webhook_alert

        routed = await route_webhook_alert(service, payload, event_id)
        routing = routed.get("decision", "")
        if routing in ("main", "news"):
            risk = {
                "level": "CRITICAL" if routing == "main" else "HIGH",
                "title": routed.get("importance", 0.0),
                "description": routing,
            }
    except _WEBHOOK_ERRORS as exc:
        logger.warning("webhook_alert_route_failed", service=service, error=str(exc))
    except Exception:
        logger.debug("webhook_alert_router_unavailable", service=service, exc_info=True)

    return {
        "status": "processed",
        "service": service,
        "event_id": event_id,
        "event_type": event_type,
        "risk_level": risk["level"],
        "routing": routing,  # main|news = alert pushed; skip|throttled|duplicate|unparseable = gated out
        "scan_triggers": scan_triggers,
        "cached": True,
    }
