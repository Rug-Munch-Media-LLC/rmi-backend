"""Endpoint health tracking + hedging/failover logic (mixed into RpcRouter)."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

import httpx

from app.domains.scanner.engines._logging import get_logger

logger = get_logger(__name__)


@dataclass
class EndpointHealth:
    """Health tracking for a single RPC endpoint."""

    url: str
    chain: str
    tier: str = "free"  # "free" (keyless public) | "freemium" (keyed free-tier)
    consecutive_failures: int = 0
    total_calls: int = 0
    total_failures: int = 0
    avg_latency_ms: float = 0.0
    last_check: float = 0.0
    is_dead: bool = False  # Marked dead after N consecutive failures
    dead_until: float = 0.0  # Unix timestamp when we can try again

    COOLDOWN_SECONDS: int = 60  # Try dead endpoints again after 60s
    MAX_CONSECUTIVE_FAILURES: int = 3  # Mark dead after 3 consecutive failures

    @property
    def alive(self) -> bool:
        """Is this endpoint worth trying right now?"""
        if not self.is_dead:
            return True
        return time.time() > self.dead_until

    def record_success(self, latency_ms: float) -> None:
        self.consecutive_failures = 0
        self.total_calls += 1
        self.avg_latency_ms = (self.avg_latency_ms * (self.total_calls - 1) + latency_ms) / self.total_calls
        self.last_check = time.time()
        self.is_dead = False

    def record_failure(self) -> None:
        self.consecutive_failures += 1
        self.total_calls += 1
        self.total_failures += 1
        self.last_check = time.time()
        if self.consecutive_failures >= self.MAX_CONSECUTIVE_FAILURES and not self.is_dead:
            self.is_dead = True
            self.dead_until = time.time() + self.COOLDOWN_SECONDS
            logger.warning(
                "rpc_endpoint_dead",
                url=self.url,
                consecutive_failures=self.consecutive_failures,
                cooldown_s=self.COOLDOWN_SECONDS,
            )

    def reset(self) -> None:
        """Reset health (e.g. after endpoint recovers)."""
        self.consecutive_failures = 0
        self.total_calls = 0
        self.total_failures = 0
        self.avg_latency_ms = 0.0
        self.is_dead = False
        self.dead_until = 0.0


class HedgingMixin:
    """Multi-endpoint hedging, health checks, and failover (mixed into RpcRouter)."""

    def _rank(self, pairs: list[tuple[str, EndpointHealth]]) -> list[tuple[str, EndpointHealth]]:
        """Sort by health: clean first, then lowest latency."""
        return sorted(
            pairs,
            key=lambda x: (
                0 if x[1].consecutive_failures == 0 else 1,
                x[1].avg_latency_ms if x[1].avg_latency_ms > 0 else 999,
            ),
        )

    def _spread(self, ranked: list[tuple[str, EndpointHealth]], chain: str) -> list[tuple[str, EndpointHealth]]:
        """Round-robin the top-3 healthy endpoints across consecutive calls.

        Keeps quality ordering (best-first within the rotation window) but
        distributes load so one low-latency winner doesn't absorb every
        request and trip its rate limit.
        """
        head, tail = ranked[:3], ranked[3:]
        if len(head) > 1:
            k = self._rr_offset[chain] % len(head)
            self._rr_offset[chain] += 1
            head = head[k:] + head[:k]
        return head + tail

    def get_available_endpoints(self, chain: str) -> list[tuple[str, EndpointHealth]]:
        """
        Get callable endpoints for a chain, tier-aware:

        1. Free tier alive → round-robin spread over its healthiest.
        2. Free tier exhausted (all dead/rate-limited) → ESCALATE to the
           freemium keyed pool, least-quota-used provider first.
        3. Nothing alive → empty list.
        """
        free_urls, freemium_urls = self._split_tiers(chain)

        free_alive = [(u, self._get_health(chain, u)) for u in free_urls if self._get_health(chain, u).alive]
        if free_alive:
            return self._spread(self._rank(free_alive), chain)

        # Free pool exhausted → escalate into keyed providers.
        fremium_alive = [
            (u, self._get_health(chain, u, tier="freemium"))
            for u in freemium_urls
            if self._get_health(chain, u, tier="freemium").alive
        ]
        if fremium_alive:
            self._record_escalation(chain)
            fremium_alive.sort(
                key=lambda x: (
                    self._quota_usage(x[0]),  # least-used provider first
                    x[1].consecutive_failures,
                    x[1].avg_latency_ms if x[1].avg_latency_ms > 0 else 999,
                )
            )
            return fremium_alive

        return []

    # ─── Health Check ──────────────────────────────────────────────

    async def check_endpoint(self, chain: str, url: str, health: EndpointHealth) -> bool:
        """
        Check if an RPC endpoint is alive with a lightweight call.
        Returns True if healthy.
        """
        payload_key = chain if chain in self._health_check_payloads else "ethereum"
        method, params = self._health_check_payloads.get(payload_key, ("eth_blockNumber", []))

        body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params,
        }

        try:
            client = await self._get_client()
            t0 = time.monotonic()
            resp = await client.post(url, json=body)
            latency = (time.monotonic() - t0) * 1000  # ms

            if resp.status_code == 200:
                data = resp.json()
                if data and isinstance(data, dict) and "result" in data:
                    health.record_success(latency)
                    return True

            # Rate limited: mark with longer cooldown
            if resp.status_code == 429:
                health.is_dead = True
                health.dead_until = time.time() + 120  # 2 min cooldown for rate limits
                health.consecutive_failures += 1
                logger.debug("rpc_rate_limited", url=url, chain=chain)
                return False

            # Other error
            health.record_failure()
            logger.debug(
                "rpc_health_check_failed",
                url=url,
                chain=chain,
                status=resp.status_code,
            )
            return False

        except (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError) as exc:
            health.record_failure()
            logger.debug(
                "rpc_health_check_error",
                url=url,
                chain=chain,
                error=str(exc)[:60],
            )
            return False

    async def ensure_healthy_endpoints(self, chain: str) -> list[tuple[str, EndpointHealth]]:
        """
        Get and verify healthy endpoints for a chain.
        Lazily checks endpoints that haven't been checked recently.
        Returns list of (url, health) tuples that are confirmed healthy.

        Tier-aware: only the ACTIVE tier (free while it has living
        endpoints, freemium after escalation) is health-checked — we never
        burn keyed-provider quota on probes while the free pool is fine.
        """
        free_urls, fremium_urls = self._split_tiers(chain)
        if not free_urls and not fremium_urls:
            logger.warning("no_rpc_endpoints_configured", chain=chain)
            return []

        # Quick check: tier-aware availability without network I/O
        available = self.get_available_endpoints(chain)
        if available:
            # Verify the first one hasn't gone stale (check if >30s since last check)
            _, health = available[0]
            if time.time() - health.last_check < 30:
                return available

        # Validate: check stale/dead endpoints within the ACTIVE tier only.
        active_tier = available[0][1].tier if available else "free"
        active_urls = free_urls if active_tier == "free" else fremium_urls

        healthy = []
        check_tasks = []
        urls_to_check = []

        for url in active_urls:
            health = self._get_health(chain, url, tier=active_tier)
            # Only check endpoints that haven't been verified recently, or are dead
            if (time.time() - health.last_check > 30) or health.is_dead:
                check_tasks.append(self.check_endpoint(chain, url, health))
                urls_to_check.append(url)
            elif health.alive:
                healthy.append((url, health))

        if check_tasks:
            results = await asyncio.gather(*check_tasks, return_exceptions=True)
            for url, is_healthy in zip(urls_to_check, results, strict=False):
                if isinstance(is_healthy, Exception):
                    logger.debug("rpc_check_exception", url=url, error=str(is_healthy))
                    health = self._get_health(chain, url, tier=active_tier)
                    health.record_failure()
                elif is_healthy:
                    healthy.append((url, self._get_health(chain, url, tier=active_tier)))

        # Sort healthy: by latency then failures; spread load across top-3
        healthy = self._spread(self._rank(healthy), chain)
        return healthy
