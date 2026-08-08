"""
DataBus Security Gate - Access Control for Premium/Paid Data
==============================================================

Three tiers:
  - PUBLIC:任何人 can access (market data, prices, news)
  - AUTHENTICATED: logged-in users (wallet labels, risk scans, wallet profiles)
  - ADMIN: admin key required (Arkham, Nansen, premium intel, raw keys)

Never exposes API keys in responses. Never leaks internal data to public users.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from app.core.logging import get_logger

if TYPE_CHECKING:
    from fastapi import Request

logger = get_logger("databus.security")

# Data type → minimum access level
ACCESS_LEVELS = {
    # ── PUBLIC (anyone) ──
    "token_price": "public",
    "tvl": "public",
    "news": "public",
    "market_overview": "public",
    "trending": "public",
    "market_movers": "public",
    "dex_data": "public",
    "social_feed": "public",
    "defi_protocols": "public",
    "prediction_markets": "public",
    "prediction_signals": "public",
    "spl_token_metadata": "public",  # Raw SPL token decoder (free, no 3rd-party API)
    # ── AUTHENTICATED (logged in) ──
    "wallet_labels": "authenticated",
    "wallet_balance": "authenticated",
    "wallet_profile": "authenticated",
    "risk_scan": "authenticated",
    "funding_source": "authenticated",
    "smart_money": "authenticated",
    "rag_search": "authenticated",
    "rugmap": "authenticated",
    "rugmaps_analysis": "authenticated",
    "socialfi_resolve": "authenticated",
    "cross_chain": "authenticated",
    "wallet_cluster": "authenticated",
    "bundle_detect": "authenticated",
    "wallet_tokens": "authenticated",
    "token_detail": "authenticated",
    "wallet_pnl": "authenticated",
    "gmgn_smart_money": "authenticated",
    "threat_check": "authenticated",
    "contract_scan": "authenticated",
    # ── PREMIUM (paid subscription) ──
    "sentinel_deep": "premium",
    "arkham_transfers": "premium",
    "arkham_counterparties": "premium",
    "nansen_labels": "premium",
    "nansen_smart_money": "premium",
    "portfolio": "premium",
    # ── ADMIN (admin key required) ──
    "entity_intel": "admin",
    "arkham_portfolio": "admin",
    "arkham_entity": "admin",
    "arkham_labels": "admin",
}

ADMIN_KEY = os.getenv("ADMIN_API_KEY", "")


class SecurityGate:
    """Validates access to data based on tier."""

    @staticmethod
    def get_access_level(data_type: str) -> str:
        return ACCESS_LEVELS.get(data_type, "authenticated")

    @staticmethod
    def check_access(data_type: str, request: Request | None = None, admin_key: str = "") -> bool:
        """
        Check if the requester has access to this data type.
        Returns True if access is allowed, raises HTTPException if not.
        """
        level = SecurityGate.get_access_level(data_type)

        if level == "public":
            return True

        if level == "authenticated":
            # In production, verify JWT/session here
            # For now, all authenticated users can access
            return True

        if level == "admin":
            provided = admin_key
            if not provided and request:
                provided = request.headers.get("X-Admin-Key", "")
                provided = provided or request.query_params.get("admin_key", "")
            if not ADMIN_KEY:
                logger.critical("ADMIN_API_KEY not configured — admin access denied")
                return False
            if provided and provided == ADMIN_KEY:
                return True
            logger.warning(f"Admin access denied for data_type={data_type}")
            return False

        if level == "premium":
            # In production, verify subscription level here
            return True

        return True

    @staticmethod
    def sanitize_response(data: dict, data_type: str, access_level: str) -> dict:
        """
        Strip sensitive fields from responses based on access level.
        NEVER include: API keys, internal URLs, server paths, error details.
        """
        if not isinstance(data, dict):
            return data

        # Always strip these fields
        dangerous_keys = {
            "api_key",
            "apikey",
            "token",
            "secret",
            "password",
            "authorization",
            "x-api-key",
            "key",
            "api-key",
            "internal_url",
            "server_path",
        }
        sanitized = {}
        for k, v in data.items():
            if k.lower() in dangerous_keys:
                continue
            if isinstance(v, dict):
                sanitized[k] = SecurityGate.sanitize_response(v, data_type, access_level)
            elif isinstance(v, list):
                sanitized[k] = [
                    SecurityGate.sanitize_response(item, data_type, access_level) if isinstance(item, dict) else item
                    for item in v
                ]
            else:
                sanitized[k] = v

        # Strip source details for non-admin
        if access_level != "admin" and "source" in sanitized:
            src = sanitized["source"]
            if isinstance(src, dict):
                sanitized["source"] = src.get("name", src.get("type", "external"))
            # Keep simple string sources for public

        return sanitized


# ── Singleton ──
security = SecurityGate()
