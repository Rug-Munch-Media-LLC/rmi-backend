"""
Model Registry - Rate Limit Tracking
=====================================
Tracks API usage across all providers. Respects hard limits.
"""

from __future__ import annotations

import time
from collections import defaultdict

from app.core.logging import get_logger

from .models import PROVIDER_LIMITS

logger = get_logger("model_registry")


class RateLimitTracker:
    """Tracks API usage across all providers. Respects hard limits."""

    def __init__(self) -> None:
        self._minute: dict[str, int] = defaultdict(int)
        self._hour: dict[str, int] = defaultdict(int)
        self._day: dict[str, int] = defaultdict(int)
        self._minute_start = time.time()
        self._hour_start = time.time()
        self._day_start = time.time()
        self._total_calls = 0
        self._throttled = 0

    def _reset_windows(self) -> None:
        now = time.time()
        if now - self._minute_start > 60:
            self._minute.clear()
            self._minute_start = now
        if now - self._hour_start > 3600:
            self._hour.clear()
            self._hour_start = now
        if now - self._day_start > 86400:
            self._day.clear()
            self._day_start = now

    def can_call(self, provider: str) -> tuple[bool, str]:
        """Check if we can make a call to this provider without exceeding limits."""
        self._reset_windows()
        limits = PROVIDER_LIMITS.get(provider, {})
        if not limits:
            return True, ""

        rpm = limits.get("rpm", 20)
        if self._minute[provider] >= rpm:
            wait = 60 - (time.time() - self._minute_start)
            return False, f"{provider}: RPM limit ({rpm}/min), retry in {wait:.0f}s"

        if provider == "openrouter":
            rpd = limits.get("rpd_free_with_credits", 1000)
        elif provider == "groq":
            rpd = limits.get("rpd", 14400)
        else:
            rpd = limits.get("rpd", 100000)

        if self._day[provider] >= rpd:
            return False, f"{provider}: Daily limit ({rpd}/day) exhausted"

        if provider == "mistral" and limits.get("rps", 1) and self._minute[provider] >= 58:
            return False, "mistral: nearing RPS limit"

        return True, ""

    def record_call(self, provider: str, tokens: int = 0) -> None:
        """Record a successful API call."""
        self._reset_windows()
        self._minute[provider] += 1
        self._hour[provider] += 1
        self._day[provider] += 1
        self._total_calls += 1

    def record_throttle(self, provider: str) -> None:
        """Record a throttled/blocked call."""
        self._throttled += 1

    def budget_remaining(self, provider: str) -> dict:
        """Get remaining budget for a provider."""
        self._reset_windows()
        limits = PROVIDER_LIMITS.get(provider, {})
        rpm = limits.get("rpm", 20)

        if provider == "openrouter":
            rpd = limits.get("rpd_free_with_credits", 1000)
        elif provider == "groq":
            rpd = limits.get("rpd", 14400)
        else:
            rpd = 100000

        return {
            "provider": provider,
            "minute_used": self._minute[provider],
            "minute_limit": rpm,
            "minute_remaining": max(0, rpm - self._minute[provider]),
            "day_used": self._day[provider],
            "day_limit": rpd,
            "day_remaining": max(0, rpd - self._day[provider]),
            "day_pct": round(self._day[provider] / max(rpd, 1) * 100, 1),
        }

    def stats(self) -> dict:
        """Full usage statistics."""
        return {
            "total_calls": self._total_calls,
            "throttled": self._throttled,
            "providers": {p: self.budget_remaining(p) for p in PROVIDER_LIMITS},
            "budget_allocation": {
                "daily_intel": "3-5 calls/day",
                "ct_rundown": "1-2 calls/day",
                "content_review": "5-10 calls/day",
                "background": "10-20 calls/day",
                "headroom": f"~{1000 - self._day.get('openrouter', 0)} calls remaining today",
            },
        }


# Global tracker instance
rate_tracker = RateLimitTracker()


def _can_use(model_config: dict) -> bool:
    """Check if model is under its rate limit using the tracker."""
    provider = model_config.get("provider", "openrouter")
    can, reason = rate_tracker.can_call(provider)
    if not can:
        logger.debug(f"Rate limited: {reason}")
        rate_tracker.record_throttle(provider)
        return False
    return True


def _track_usage(model_id: str, tokens: int = 0) -> None:
    """Track model usage through the rate tracker."""
    model_providers = {
        "openrouter": [
            "nvidia/",
            "z-ai/",
            "google/",
            "qwen/",
            "openai/",
            "moonshotai/",
            "liquid/",
            "openrouter/",
        ],
        "groq": ["llama-3", "llama-4", "mixtral", "gemma"],
        "mistral": ["mistral", "ministral", "codestral", "open-mistral"],
    }
    for provider, prefixes in model_providers.items():
        if any(model_id.startswith(pref) for pref in prefixes):
            rate_tracker.record_call(provider, tokens)
            return
