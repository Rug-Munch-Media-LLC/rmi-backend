"""
Webhook Receivers — Helius (Solana) + Moralis Streams (EVM).
Processes real-time blockchain events: transfers, swaps, mints, whale activity.
INTELLIGENT PROCESSING: Whale detection, cluster correlation, scam pattern matching.
"""

import logging

from fastapi import APIRouter, HTTPException, Request

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/webhooks", tags=["webhooks"])

# ── Config ──────────────────────────────────────────────────

HELIUS_WEBHOOK_AUTH = "helius_REDACTED"
HELIUS_WEBHOOK_SECRET = "rmi_REDACTED"

# ── Intelligent Processor ────────────────────────────────────


async def _process_with_intelligence(event: dict, source: str) -> dict | None:
    """Process event with intelligent analysis."""
    try:
        from app.intelligent_webhooks import get_intelligent_processor

        processor = get_intelligent_processor()

        if source == "helius":
            alert = await processor.process_helius_event(event)
        elif source == "moralis":
            alert = await processor.process_moralis_event(event)
        else:
            return None

        if alert:
            # Log critical/high alerts
            if alert.severity in ("critical", "high"):
                logger.warning(
                    f"🚨 {alert.severity.upper()}: {alert.alert_type} "
                    f"wallet={alert.wallet[:16]}... amount=${alert.amount_usd:,.0f} "
                    f"risk={alert.risk_score:.1f}"
                )

            return {
                "alert_id": alert.alert_id,
                "severity": alert.severity,
                "type": alert.alert_type,
                "wallet": alert.wallet,
                "chain": alert.chain,
                "amount_usd": alert.amount_usd,
                "risk_score": alert.risk_score,
                "description": alert.description,
                "enriched": alert.enriched_data,
                "actions": alert.actions,
            }
        return None
    except Exception as e:
        logger.debug(f"Intelligent processing failed: {e}")
        return None


def _process_helius_event(event: dict) -> dict:
    """Process a single Helius webhook event."""
    result = {
        "signature": event.get("signature", ""),
        "type": event.get("type", "unknown"),
        "source": "helius",
        "timestamp": event.get("timestamp", 0),
        "wallet": "",
        "description": "",
        "processed": False,
    }

    try:
        # Extract key data from Helius enriched transaction
        events = event.get("events", [])
        event.get("accountData", [])
        fee_payer = event.get("feePayer", "")
        description = event.get("description", "")

        result["wallet"] = fee_payer
        result["description"] = description[:200]

        # Classify event type
        event_types = [e.get("type", "") for e in events] if isinstance(events, list) else []
        if any("swap" in str(e).lower() for e in [*event_types, description]):
            result["type"] = "swap"
        elif any("transfer" in str(e).lower() for e in [*event_types, description]):
            result["type"] = "transfer"
        elif any("mint" in str(e).lower() for e in [*event_types, description]):
            result["type"] = "mint"
        elif any("nft" in str(e).lower() for e in [*event_types, description]):
            result["type"] = "nft"

        # Track amounts from account data
        native_transfers = event.get("nativeTransfers", [])
        if native_transfers:
            max_transfer = max(native_transfers, key=lambda t: t.get("amount", 0), default={})
            result["largest_transfer"] = max_transfer.get("amount", 0) / 1e9  # lamports to SOL

        # Token transfers
        token_transfers = event.get("tokenTransfers", [])
        if token_transfers:
            for tt in token_transfers[:3]:  # Top 3 token transfers
                mint = tt.get("mint", "")
                amount = tt.get("tokenAmount", {}).get("uiAmount", 0)
                if amount and float(amount) > 0:
                    result.setdefault("token_moves", []).append(
                        {
                            "mint": mint,
                            "amount": float(amount),
                            "from": tt.get("fromUserAccount", ""),
                            "to": tt.get("toUserAccount", ""),
                        }
                    )

        result["processed"] = True

    except Exception as e:
        logger.warning(f"Event processing error: {e}")
        result["error"] = str(e)[:200]

    return result


def _process_moralis_event(event: dict) -> dict:
    """Process a single Moralis Stream (EVM) event."""
    result = {
        "hash": event.get("transactionHash", event.get("hash", "")),
        "chain": event.get("chainId", "0x1"),
        "source": "moralis",
        "type": "unknown",
        "from": event.get("fromAddress", ""),
        "to": event.get("toAddress", ""),
        "value": "0",
        "timestamp": event.get("blockTimestamp", ""),
        "processed": False,
    }

    try:
        # Classify by log events or description
        logs = event.get("logs", [])
        erc20_transfers = event.get("erc20Transfers", [])

        if erc20_transfers:
            result["type"] = "erc20_transfer"
            for t in erc20_transfers[:3]:
                result.setdefault("token_transfers", []).append(
                    {
                        "from": t.get("from", ""),
                        "to": t.get("to", ""),
                        "value": t.get("value", "0"),
                        "symbol": t.get("symbol", ""),
                        "token": t.get("address", ""),
                    }
                )
        elif logs:
            # Check for swap events (Uniswap, etc.)
            result["type"] = "contract_interaction"
        else:
            native_value = event.get("value", "0")
            if native_value and float(native_value) > 0:
                result["type"] = "native_transfer"
                result["value"] = str(float(native_value) / 1e18)

        # Whale detection — flag transfers > 10 ETH equivalent
        try:
            val = float(event.get("value", "0")) / 1e18 if event.get("value") else 0
            if val > 10:
                result["whale_alert"] = True
                result["whale_value_eth"] = val
        except (ValueError, TypeError):
            pass

        result["processed"] = True

    except Exception as e:
        logger.warning(f"Moralis event processing error: {e}")
        result["error"] = str(e)[:200]

    return result


# ── Endpoints ───────────────────────────────────────────────


@router.post("/helius")
async def helius_webhook(request: Request):
    """Receive Helius webhook events (Solana transfers, swaps, mints).
    INTELLIGENT: Whale detection, cluster correlation, scam pattern matching."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    # Helius can send single events or arrays
    events = body if isinstance(body, list) else [body]
    processed = []
    alerts = []

    for event in events:
        # Basic processing
        result = _process_helius_event(event)
        processed.append(result)

        # Intelligent processing
        alert = await _process_with_intelligence(event, "helius")
        if alert:
            alerts.append(alert)

    # Store in cache for retrieval
    try:
        from app.chain_cache import get_chain_cache

        cache = get_chain_cache()
        await cache.set("helius_events", processed, "latest", ttl=3600)
        if alerts:
            await cache.set("helius_alerts", alerts, "latest", ttl=3600)
    except Exception:
        pass

    return {
        "status": "ok",
        "events_processed": len(processed),
        "alerts_generated": len(alerts),
        "critical_alerts": len([a for a in alerts if a.get("severity") == "critical"]),
    }


@router.post("/moralis")
async def moralis_webhook(request: Request):
    """Receive Moralis Stream events (EVM whale tracking, token transfers).
    INTELLIGENT: Whale detection, cluster correlation, scam pattern matching."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    # Moralis streams send arrays of confirmed/tx events
    confirmed = body.get("confirmed", body.get("logs", []))
    if isinstance(body, list):
        confirmed = body

    processed = []
    alerts = []
    for event in confirmed if isinstance(confirmed, list) else [confirmed]:
        # Basic processing
        result = _process_moralis_event(event)
        processed.append(result)

        # Intelligent processing
        alert = await _process_with_intelligence(event, "moralis")
        if alert:
            alerts.append(alert)

    # Cache latest events
    try:
        from app.chain_cache import get_chain_cache

        cache = get_chain_cache()
        await cache.set("moralis_events", processed, "latest", ttl=3600)
        if alerts:
            await cache.set("moralis_alerts", alerts, "latest", ttl=3600)
    except Exception:
        pass

    return {
        "status": "ok",
        "events_processed": len(processed),
        "alerts_generated": len(alerts),
        "critical_alerts": len([a for a in alerts if a.get("severity") == "critical"]),
    }


@router.get("/events")
async def get_recent_events(source: str | None = None):
    """Get recent webhook events from cache."""
    try:
        from app.chain_cache import get_chain_cache

        cache = get_chain_cache()

        events = []
        helius = await cache.get("helius_events", "latest")
        if helius:
            events.extend(helius if source != "moralis" else [])

        moralis = await cache.get("moralis_events", "latest")
        if moralis:
            events.extend(moralis if source != "helius" else [])

        return {"total": len(events), "events": events[:50]}
    except Exception:
        return {"total": 0, "events": []}


@router.get("/health")
async def webhooks_health():
    """Webhook health check."""
    return {
        "status": "ok",
        "service": "webhook-receiver",
        "endpoints": {
            "helius": "/api/v1/webhooks/helius",
            "moralis": "/api/v1/webhooks/moralis",
            "events": "/api/v1/webhooks/events",
        },
    }
