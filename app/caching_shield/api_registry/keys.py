"""
API Registry - Key Discovery & Key Pool Load Balancer
======================================================
ApiKey dataclass, discover_keys environment scanner, and KeyPool.
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass

from app.core.logging import get_logger

from .config import PROVIDER_REGISTRY

logger = get_logger("api_registry")

@dataclass
class ApiKey:
    """A single API key with runtime state."""

    provider: str
    key_name: str
    value: str
    base_url: str = ""
    calls_total: int = 0
    calls_this_month: int = 0
    errors: int = 0
    rate_limited: int = 0
    last_used: float = 0.0
    last_error: str = ""
    tokens: float = 0.0
    last_refill: float = 0.0
    rate_limited_until: float = 0.0
    consecutive_429s: int = 0
    disabled: bool = False

    def __post_init__(self) -> None:
        cfg = PROVIDER_REGISTRY.get(self.provider)
        burst = cfg.burst if cfg else 15
        self.tokens = float(burst)
        self.last_refill = time.monotonic()

def discover_keys() -> dict[str, list[ApiKey]]:
    """Scan environment variables and build key pools per provider."""
    pools: dict[str, list[ApiKey]] = {}

    for prov_name, cfg in PROVIDER_REGISTRY.items():
        keys: list[ApiKey] = []

        # Special: Solana Tracker uses custom key naming
        if prov_name == "solana_tracker":
            secure_endpoint = os.getenv("SOLANATRACKER_SECURE_URL", "")
            if secure_endpoint:
                keys.append(
                    ApiKey(
                        provider=prov_name,
                        key_name="SOLANATRACKER_SECURE_URL",
                        value="",
                        base_url=secure_endpoint,
                    )
                )
            st_key = os.getenv("SOLANATRACKER_API_KEY", "")
            if st_key:
                keys.append(
                    ApiKey(
                        provider=prov_name,
                        key_name="SOLANATRACKER_API_KEY",
                        value=st_key,
                        base_url="https://data.solanatracker.io",
                    )
                )
            st_key2 = os.getenv("SOLANATRACKER_API_KEY_2", "")
            if st_key2:
                keys.append(
                    ApiKey(
                        provider=prov_name,
                        key_name="SOLANATRACKER_API_KEY_2",
                        value=st_key2,
                        base_url="https://data.solanatracker.io",
                    )
                )
        # Standard: scan {PREFIX}, {PREFIX}_2, {PREFIX}_3, ...
        else:
            for suffix in ["", "_2", "_3", "_4", "_5"]:
                env_name = f"{cfg.env_prefix}{suffix}"
                key_val = os.getenv(env_name, "")
                if key_val and key_val not in ("your_key_here", "***", ""):
                    keys.append(
                        ApiKey(
                            provider=prov_name,
                            key_name=env_name,
                            value=key_val,
                            base_url=cfg.base_url if cfg.base_url else "",
                        )
                    )

            if prov_name == "helius":
                custom_url = os.getenv("HELIUS_RPC_URL", "")
                if custom_url:
                    for k in keys:
                        k.base_url = custom_url

        if keys:
            pools[prov_name] = keys
            logger.info(f"API Registry: {prov_name} -> {len(keys)} key(s)")

    return pools

class KeyPool:
    """Round-robin key pool with health tracking and rate limiting."""

    def __init__(self, provider: str, keys: list[ApiKey], config) -> None:
        self.provider = provider
        self.keys = keys
        self.config = config
        self._index = 0
        self._lock = asyncio.Lock()
        self.total_calls = 0

    async def acquire(self) -> ApiKey | None:
        """Get the next available key in the pool."""
        async with self._lock:
            now = time.monotonic()
            for _ in range(len(self.keys)):
                key = self.keys[self._index]
                self._index = (self._index + 1) % len(self.keys)

                if key.disabled:
                    continue
                if now < key.rate_limited_until:
                    continue

                elapsed = now - key.last_refill
                rate = self.config.rate_rps
                key.tokens = min(float(self.config.burst), key.tokens + elapsed * rate)
                key.last_refill = now

                if key.tokens < 1.0:
                    continue

                if (
                    self.config.monthly_quota > 0
                    and key.calls_this_month >= self.config.monthly_quota
                ):
                    continue

                key.tokens -= 1.0
                key.calls_this_month += 1
                key.calls_total += 1
                key.last_used = now
                self.total_calls += 1
                return key

            return None

    def mark_success(self, key: ApiKey) -> None:
        key.consecutive_429s = 0
        key.rate_limited_until = 0.0

    def mark_rate_limited(self, key: ApiKey) -> None:
        now = time.monotonic()
        key.consecutive_429s += 1
        key.rate_limited += 1
        backoff = min(120, 5 * (2 ** min(key.consecutive_429s, 5)))
        key.rate_limited_until = now + backoff

    def mark_error(self, key: ApiKey, error: str) -> None:
        key.errors += 1
        key.last_error = error[:100]

    def stats(self) -> dict:
        now = time.monotonic()
        key_stats = []
        for k in self.keys:
            k.tokens = min(
                float(self.config.burst), k.tokens + (now - k.last_refill) * self.config.rate_rps
            )
            key_stats.append(
                {
                    "name": k.key_name,
                    "calls": k.calls_total,
                    "errors": k.errors,
                    "rate_limited": k.rate_limited,
                    "tokens": round(k.tokens, 1),
                    "rate_limited_now": now < k.rate_limited_until,
                    "disabled": k.disabled,
                }
            )

        return {
            "provider": self.provider,
            "total_keys": len(self.keys),
            "active_keys": sum(
                1 for k in self.keys if not k.disabled and not (now < k.rate_limited_until)
            ),
            "total_calls": self.total_calls,
            "rate_rps": self.config.rate_rps,
            "burst": self.config.burst,
            "monthly_quota": self.config.monthly_quota,
            "keys": key_stats,
        }
