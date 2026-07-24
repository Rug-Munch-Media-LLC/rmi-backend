"""API Developer Portal - self-service key management, docs, usage tracking."""

from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Header
from pydantic import BaseModel

from app.core.errors import AuthError

router = APIRouter(prefix="/api/v1/developer", tags=["developer-portal"])

# In-memory store (Supabase in prod)
_api_keys: dict[str, dict] = {}
_usage_store: dict[str, list] = {}

class CreateKeyRequest(BaseModel):
    email: str
    name: str = ""

class APIKeyResponse(BaseModel):
    key: str
    name: str
    email: str
    tier: str = "free"
    created_at: str
    requests_today: int = 0

def _generate_key() -> str:
    return "rmi_" + hashlib.sha256(os.urandom(32)).hexdigest()[:32]

@router.post("/keys")
async def create_api_key(req: CreateKeyRequest) -> Any:
    """Generate a new API key. Free tier: 100 requests/day."""
    key = _generate_key()
    _api_keys[key] = {
        "key": key,
        "name": req.name or "Untitled",
        "email": req.email,
        "tier": "free",
        "created_at": datetime.now(UTC).isoformat(),
        "requests_today": 0,
    }
    return APIKeyResponse(**_api_keys[key])

@router.get("/keys")
async def list_keys(x_api_key: str = Header(...)) -> dict[str, list]:
    """List your API keys."""
    if x_api_key not in _api_keys:
        raise AuthError(message="Invalid API key")
    return {"keys": [_api_keys[x_api_key]]}

@router.delete("/keys/{key}")
async def revoke_key(key: str) -> dict:
    """Revoke an API key."""
    _api_keys.pop(key, None)
    return {"revoked": key}

@router.get("/usage")
async def get_usage(x_api_key: str = Header(...)) -> dict:
    """Get your API usage stats."""
    if x_api_key not in _api_keys:
        raise AuthError("Invalid API key")
    return {
        "key": x_api_key,
        "tier": _api_keys[x_api_key]["tier"],
        "requests_today": _api_keys[x_api_key].get("requests_today", 0),
        "limit": 100 if _api_keys[x_api_key]["tier"] == "free" else 10000,
    }

@router.get("/docs")
async def api_docs() -> dict[str, str]:
    """API documentation - available endpoints."""
    return {
        "base_url": "https://rugmunch.io/api/v1",
        "authentication": "X-API-Key header",
        "rate_limits": {"free": "100/day", "pro": "10,000/day", "enterprise": "unlimited"},
        "endpoints": [
            {"method": "GET", "path": "/databus/fetch/{data_type}", "desc": "Query DataBus"},
            {"method": "GET", "path": "/token/scan", "desc": "SENTINEL security scan"},
            {"method": "GET", "path": "/mev-sniper/signals", "desc": "MEV sniper signals"},
            {"method": "GET", "path": "/sentiment/token/{symbol}", "desc": "Social sentiment"},
            {"method": "GET", "path": "/arbitrage/find/{symbol}", "desc": "Cross-chain arbitrage"},
            {"method": "GET", "path": "/prediction-markets/markets", "desc": "Prediction markets"},
            {"method": "GET", "path": "/dify-tools/catalog", "desc": "All 15 AI tools"},
        ],
        "sdks": [
            "Python: pip install rmi-sdk (coming soon)",
            "JavaScript: npm install rmi-sdk (coming soon)",
        ],
        "swagger": "https://rugmunch.io/docs",
    }
