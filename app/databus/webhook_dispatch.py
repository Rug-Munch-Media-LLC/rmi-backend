"""Webhook → action mapping: which premium scans to trigger, how risky it is."""

from __future__ import annotations

from typing import Any


def _get_scan_triggers(service: str, event_type: str, payload: dict[str, Any]) -> list[str]:
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


def _assess_webhook_risk(service: str, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
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
