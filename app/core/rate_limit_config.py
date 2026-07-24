"""Rate limit categories and path-to-category mappings for slowapi."""

from __future__ import annotations
from dataclasses import dataclass

@dataclass(frozen=True)
class RateLimitRule:
    category: str
    limit: str  # e.g. "10/min"
    burst: int = 1
    exempt_internal: bool = True

RULES: list[RateLimitRule] = [
    RateLimitRule("health", "1000/min"),
    RateLimitRule("auth", "5/min", burst=2),
    RateLimitRule("x402", "1000/min"),
    RateLimitRule("scan", "20/min"),
    RateLimitRule("ai", "10/min"),
    RateLimitRule("market", "60/min"),
    RateLimitRule("public", "100/min"),
    RateLimitRule("mcp", "30/min"),
    RateLimitRule("admin", "10/min", burst=2),
]

DEFAULT_RULE = RateLimitRule("default", "60/min")

PATH_CATEGORIES: dict[str, str] = {
    "/health": "health",
    "/live": "health",
    "/ready": "health",
    "/metrics": "health",
    "/register": "auth",
    "/login": "auth",
    "/2fa": "auth",
    "/forgot-password": "auth",
    "/api/v1/x402": "x402",
    "/pay": "x402",
    "/affiliates": "x402",
    "/usage": "x402",
    "/api/v1/scanner": "scan",
    "/api/v1/contract-analyzer": "scan",
    "/api/v1/death-clock": "scan",
    "/api/v1/rugmaps": "scan",
    "/api/v1/honeypot-map": "scan",
    "/api/v1/tvl-verify": "scan",
    "/api/v1/whale-alerts": "scan",
    "/api/v1/criminal-clusters": "scan",
    "/api/v1/address-profiler": "scan",
    "/api/v1/mev-advisory": "scan",
    "/api/v1/wallet-clusters": "scan",
    "/api/v1/ai": "ai",
    "/api/v1/chat": "ai",
    "/api/v1/rag": "ai",
    "/api/v1/market": "market",
    "/api/v1/news": "market",
    "/api/v1/bulletin": "market",
    "/api/v1/bulletin-board": "market",
    "/api/v1/catalog": "market",
    "/api/v1/token": "public",
    "/api/v1/wallet": "public",
    "/api/v1/labels": "public",
    "/mcp": "mcp",
    "/api/v1/admin": "admin",
    "/api/v1/_test": "admin",
}
