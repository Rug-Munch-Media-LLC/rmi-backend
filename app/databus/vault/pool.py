"""Vault key pool for a single provider."""

import asyncio
import time
from typing import Any

from app.core.logging import get_logger
from app.databus.vault.models import KeyState, ManagedKey

logger = get_logger("databus.vault.pool")


class VaultKeyPool:
    """
    Key pool for a single provider. Round-robin with health tracking.
    Keys loaded from vault (GPG pass store), NEVER from .env.
    Zero key exposure in logs, responses, or error messages.
    """

    def __init__(self, provider: str, rate_rps: float = 10.0, burst: int = 15, monthly_quota: int = 0) -> None:
        self.provider = provider
        self.rate_rps = rate_rps
        self.burst = burst
        self.monthly_quota = monthly_quota
        self.keys: list[ManagedKey] = []
        self._index = 0
        self._lock = asyncio.Lock()
        self.total_calls = 0

    def add_key(self, key_name: str, value: str, source: str = "vault") -> None:
        """Add a key to the pool. Value is immediately obfuscated."""
        mk = ManagedKey(
            provider=self.provider,
            key_name=key_name,
            _obfuscated=b"",  # set via method
            source=source,
            rate_rps=self.rate_rps,
            burst=self.burst,
            monthly_quota=self.monthly_quota,
        )
        mk._set_value(value)

        self.keys.append(mk)

    async def acquire(self) -> ManagedKey | None:
        """Get the next available key in round-robin. Returns None if all exhausted."""
        async with self._lock:
            now = time.monotonic()
            tried = 0
            while tried < len(self.keys):
                key = self.keys[self._index]
                self._index = (self._index + 1) % len(self.keys)
                tried += 1

                key.refill_tokens(now)
                if not key.is_available():
                    continue
                if key.tokens < 1.0:
                    continue

                key.tokens -= 1.0
                self.total_calls += 1
                return key

            return None  # All keys exhausted or rate-limited

    def status(self) -> dict[str, Any]:
        """Return pool status - NO key values ever exposed."""
        active = sum(1 for k in self.keys if k.is_available())
        rate_limited = sum(1 for k in self.keys if k.state == KeyState.RATE_LIMITED)
        return {
            "provider": self.provider,
            "total_keys": len(self.keys),
            "active_keys": active,
            "rate_limited_keys": rate_limited,
            "combined_rps": round(self.rate_rps * len(self.keys), 1),
            "monthly_quota_per_key": self.monthly_quota,
            "total_calls": self.total_calls,
            "keys": [k.status() for k in self.keys],
        }
