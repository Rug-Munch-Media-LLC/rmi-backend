"""Per-proxy health bookkeeping and the circuit breaker for the X proxy tier.

Health is recorded per proxy **host** only. Proxy URLs embed ``user:pass@``,
so they are never stored or logged here — the snapshot this module produces is
safe to print from the CLI probe and safe to attach to diagnostics.

Rotation bookkeeping (round-robin order, per-proxy cooldown) stays in
``app.databus.proxy_rotator``; this module answers one question: is the proxy
tier healthy enough to try right now, and which hosts are failing?
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Final
from urllib.parse import urlsplit

from app.core.logging import get_logger

log = get_logger(__name__)

# Consecutive failures across ALL proxies that open the circuit breaker.
CIRCUIT_FAILURE_THRESHOLD: Final[int] = 6
# How long the breaker stays open before proxies are retried.
CIRCUIT_OPEN_SECONDS: Final[float] = 120.0


@dataclass
class _Stat:
    """Outcome counters for one proxy host."""

    success: int = 0
    failure: int = 0
    last_error: str = ""


class ProxyHealth:
    """Per-host outcome counters plus a global consecutive-failure breaker.

    The breaker is global rather than per proxy on purpose: a WebShare outage
    (or a revoked credential) fails every proxy at once, and the whole tier
    should go quiet instead of burning one timeout per proxy per request.
    """

    def __init__(
        self,
        *,
        failure_threshold: int = CIRCUIT_FAILURE_THRESHOLD,
        open_seconds: float = CIRCUIT_OPEN_SECONDS,
    ) -> None:
        self._failure_threshold = failure_threshold
        self._open_seconds = open_seconds
        self._lock = threading.Lock()
        self._stats: dict[str, _Stat] = {}
        self._consecutive_failures = 0
        self._opened_at = 0.0

    @staticmethod
    def host_of(proxy_url: str) -> str:
        """Host part of a proxy URL, for logging (never credentials)."""
        try:
            return urlsplit(proxy_url).hostname or "unknown"
        except ValueError:
            return "unknown"

    def record_success(self, proxy_url: str) -> None:
        """Count a proxied request that returned a usable response."""
        host = self.host_of(proxy_url)
        with self._lock:
            stat = self._stats.setdefault(host, _Stat())
            stat.success += 1
            self._consecutive_failures = 0
            self._opened_at = 0.0

    def record_failure(self, proxy_url: str, reason: str) -> None:
        """Count a failed attempt and open the breaker when the tier is down."""
        host = self.host_of(proxy_url)
        now = time.monotonic()
        with self._lock:
            stat = self._stats.setdefault(host, _Stat())
            stat.failure += 1
            stat.last_error = reason
            self._consecutive_failures += 1
            if self._consecutive_failures >= self._failure_threshold and self._opened_at == 0.0:
                self._opened_at = now
                log.warning(
                    "proxy_circuit_open",
                    consecutive_failures=self._consecutive_failures,
                    host=host,
                    reason=reason,
                )

    def circuit_open(self) -> bool:
        """True while the breaker is open (callers should go direct)."""
        with self._lock:
            if self._opened_at == 0.0:
                return False
            if time.monotonic() - self._opened_at >= self._open_seconds:
                self._opened_at = 0.0
                self._consecutive_failures = 0
                log.info("proxy_circuit_half_open")
                return False
            return True

    def snapshot(self) -> dict[str, object]:
        """Credential-free health report: hosts, counters, breaker state."""
        with self._lock:
            open_now = self._opened_at != 0.0 and (time.monotonic() - self._opened_at < self._open_seconds)
            return {
                "circuit_open": open_now,
                "consecutive_failures": self._consecutive_failures,
                "hosts": {
                    host: {
                        "success": stat.success,
                        "failure": stat.failure,
                        "last_error": stat.last_error,
                    }
                    for host, stat in sorted(self._stats.items())
                },
            }
