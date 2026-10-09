"""Vault data models."""

import time
from dataclasses import dataclass
from enum import Enum
from typing import Any


class KeyState(Enum):
    ACTIVE = "active"
    RATE_LIMITED = "rate_limited"
    DISABLED = "disabled"
    ERROR = "error"
    EXHAUSTED = "exhausted"  # monthly quota hit


@dataclass
class ManagedKey:
    """A single API key with runtime state. Key value is NEVER a plain string."""

    provider: str
    key_name: str  # env var name e.g. "HELIUS_API_KEY"
    _obfuscated: bytes  # obfuscated key value - never plain string
    source: str = "env"  # "env" or "vault"
    state: KeyState = KeyState.ACTIVE
    calls_total: int = 0
    calls_this_month: int = 0
    monthly_quota: int = 0  # 0 = unlimited
    errors: int = 0
    consecutive_429s: int = 0
    rate_limited_until: float = 0.0
    last_used: float = 0.0
    last_error: str = ""
    # Token bucket
    tokens: float = 0.0
    last_refill: float = 0.0
    rate_rps: float = 10.0
    burst: int = 15

    def __post_init__(self) -> None:
        self.tokens = float(self.burst)
        self.last_refill = time.monotonic()

    @property
    def value(self) -> str:
        """Deobfuscate and return the key. Use acquire()/release() for safety."""
        return self._obfuscated.decode("utf-8", errors="replace")[::-1]

    def _set_value(self, val: str) -> None:
        """Obfuscate a key value for in-memory storage."""
        self._obfuscated = val[::-1].encode("utf-8")

    def acquire(self) -> str:
        """Get the key value for use. Call release() when done."""
        self.calls_total += 1
        self.calls_this_month += 1
        self.last_used = time.monotonic()
        return self.value

    def release_success(self) -> None:
        """Mark the key call as successful."""
        self.consecutive_429s = 0
        self.rate_limited_until = 0.0
        self.state = KeyState.ACTIVE

    def release_rate_limited(self, backoff_base: float = 5.0) -> None:
        """Mark the key as rate limited with exponential backoff."""
        self.consecutive_429s += 1
        backoff = min(300, backoff_base * (2 ** min(self.consecutive_429s, 6)))
        self.rate_limited_until = time.monotonic() + backoff
        self.state = KeyState.RATE_LIMITED

    def release_error(self, error: str) -> None:
        """Mark the key as errored."""
        self.errors += 1
        self.last_error = error[:200]
        if self.errors > 10:
            self.state = KeyState.ERROR

    def is_available(self) -> bool:
        """Check if key is usable right now."""
        if self.state == KeyState.DISABLED:
            return False
        if self.state == KeyState.ERROR and self.errors > 50:
            return False
        if self.state == KeyState.RATE_LIMITED:
            if time.monotonic() > self.rate_limited_until:
                self.state = KeyState.ACTIVE
                self.consecutive_429s = 0
                return True
            return False
        return not (self.monthly_quota > 0 and self.calls_this_month >= self.monthly_quota)

    def refill_tokens(self, now: float) -> None:
        """Refill token bucket."""
        elapsed = now - self.last_refill
        self.tokens = min(float(self.burst), self.tokens + elapsed * self.rate_rps)
        self.last_refill = now

    def status(self) -> dict[str, Any]:
        """Return status dict - NEVER includes key value."""
        return {
            "provider": self.provider,
            "key_name": self.key_name,
            "state": self.state.value,
            "calls_total": self.calls_total,
            "calls_this_month": self.calls_this_month,
            "monthly_quota": self.monthly_quota,
            "tokens_available": round(self.tokens, 1),
            "errors": self.errors,
            "consecutive_429s": self.consecutive_429s,
            "rate_limited_for_secs": max(0, round(self.rate_limited_until - time.monotonic(), 1))
            if self.state == KeyState.RATE_LIMITED
            else 0,
            "last_used_ago_secs": round(time.monotonic() - self.last_used, 1) if self.last_used else 0,
        }
