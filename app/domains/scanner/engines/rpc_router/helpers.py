"""Shared RPC router helpers: URL resolution, tiers, quota ledger, client, health report."""

from __future__ import annotations

import os
import re
import time
from collections import defaultdict
from typing import Any

import httpx

from app.domains.scanner.engines._logging import get_logger
from app.domains.scanner.engines.rpc_router.hedging import EndpointHealth

logger = get_logger(__name__)


class HelpersMixin:
    """Shared plumbing for RpcRouter (mixed in).

    Attribute contract: these are initialised by ``_init_router_state`` (called
    from ``RpcRouter.__init__``); declared here so the mixin type-checks
    independently of the composing class.
    """

    _evm_health: dict[str, dict[str, EndpointHealth]]
    _solana_health: dict[str, EndpointHealth]
    _client: httpx.AsyncClient | None
    _rr_offset: dict[str, int]
    _quota: dict[str, list[float]]
    _call_timeout: float
    freemium_escalations: int
    _last_escalation_log: dict[str, float]

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the shared httpx client."""
        if self._client is None or self._client.is_closed:
            limits = httpx.Limits(
                max_connections=200,
                max_keepalive_connections=50,
                keepalive_expiry=30,
            )
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._call_timeout),
                limits=limits,
            )
        return self._client

    # ─── Tier helpers ──────────────────────────────────────────────

    @staticmethod
    def _tier_of_template(url: str) -> str:
        """Classify a raw (unresolved) endpoint template.

        Any URL embedding a ``${ENV_VAR}`` key placeholder is *freemium* —
        a keyed free-tier provider whose quota we protect. Everything else
        is *free*. Freemium endpoints are used ONLY when the free pool for
        the chain is exhausted (all dead / rate-limited).
        """
        return "freemium" if "${" in url else "free"

    def _resolve_url(self, url: str) -> str | None:
        """Resolve ``${ENV_VAR}`` references in URLs.

        Returns ``None`` when a referenced key env var is unset/empty —
        keyed endpoints without credentials are excluded entirely so we
        never health-check or call a broken half-resolved URL.
        """

        class _MissingKey(Exception):
            pass

        def _sub(m: re.Match[str]) -> str:
            val = os.getenv(m.group(1), "")
            if not val:
                raise _MissingKey(m.group(1))
            return val

        try:
            return re.sub(r"\$\{(\w+)\}", _sub, url)
        except _MissingKey:
            return None

    def _raw_endpoints(self, chain: str) -> list[str]:
        """Raw endpoint templates for a chain (before key resolution)."""
        from app.domains.scanner.engines.rpc_router.evm import EVM_RPC_ENDPOINTS
        from app.domains.scanner.engines.rpc_router.solana import SOLANA_RPC_ENDPOINTS

        if chain == "solana":
            return list(SOLANA_RPC_ENDPOINTS)
        return list(EVM_RPC_ENDPOINTS.get(chain, []))

    def _split_tiers(self, chain: str) -> tuple[list[str], list[str]]:
        """Split a chain's endpoints into resolved (free, freemium) lists.

        Endpoints whose API-key env var is missing are dropped entirely.
        """
        free: list[str] = []
        freemium: list[str] = []
        for raw in self._raw_endpoints(chain):
            resolved = self._resolve_url(raw)
            if resolved is None:
                continue
            if self._tier_of_template(raw) == "freemium":
                freemium.append(resolved)
            else:
                free.append(resolved)
        return free, freemium

    def _get_health(self, chain: str, url: str, tier: str = "free") -> EndpointHealth:
        """Get or create an EndpointHealth for a URL."""
        if chain == "solana":
            if url not in self._solana_health:
                self._solana_health[url] = EndpointHealth(url=url, chain=chain, tier=tier)
            return self._solana_health[url]
        if url not in self._evm_health[chain]:
            self._evm_health[chain][url] = EndpointHealth(url=url, chain=chain, tier=tier)
        return self._evm_health[chain][url]

    # ─── Quota ledger (freemium providers) ─────────────────────────

    def _record_quota(self, url: str) -> None:
        """Record a call against the provider host's hourly quota window."""
        from urllib.parse import urlparse

        now = time.time()
        q = self._quota[urlparse(url).netloc]
        q[:] = [t for t in q if now - t < 3600]
        q.append(now)

    def _quota_usage(self, url: str) -> int:
        """Calls made to this provider host in the last hour."""
        from urllib.parse import urlparse

        now = time.time()
        return sum(1 for t in self._quota.get(urlparse(url).netloc, []) if now - t < 3600)

    def _record_escalation(self, chain: str) -> None:
        """Count + throttled-log a free→freemium escalation for a chain."""
        self.freemium_escalations += 1
        now = time.time()
        if now - self._last_escalation_log.get(chain, 0.0) > 300:
            logger.warning(
                "rpc_freemium_escalation",
                chain=chain,
                note="free tier exhausted; falling back to keyed providers",
            )
            self._last_escalation_log[chain] = now

    # ─── Convenience: get_balance (EVM + Solana) ───────────────────

    async def get_balance(
        self,
        chain: str,
        address: str,
        timeout: float | None = None,
    ) -> int | None:
        """
        Get native token balance for an address.
        For EVM: eth_getBalance. For Solana: getBalance.
        Returns balance in smallest unit (wei/lamports), or None.
        """
        if chain == "solana":
            result = await self.call(chain, "getBalance", [address], timeout=timeout)
            if result and isinstance(result, dict):
                return result.get("result", {}).get("value")
            return None

        result = await self.call(chain, "eth_getBalance", [address, "latest"], timeout=timeout)
        if result and isinstance(result, dict):
            val = result.get("result")
            if val and val.startswith("0x"):
                return int(val, 16)
        return None

    # ─── Stats & Health Report ─────────────────────────────────────

    def get_health_report(self) -> dict[str, Any]:
        """Get a health report for all tracked RPC endpoints."""
        report: dict[str, Any] = {
            "evm_chains": {},
            "solana": {},
            "summary": {
                "total_endpoints": 0,
                "alive": 0,
                "dead": 0,
                "total_calls": 0,
                "total_failures": 0,
                "freemium_escalations": self.freemium_escalations,
                "freemium_calls_last_hour": sum(
                    sum(1 for t in q if time.time() - t < 3600) for q in self._quota.values()
                ),
            },
        }

        def _entry(health: EndpointHealth) -> dict[str, Any]:
            return {
                "url": health.url,
                "tier": health.tier,
                "alive": health.alive,
                "consecutive_failures": health.consecutive_failures,
                "total_calls": health.total_calls,
                "total_failures": health.total_failures,
                "avg_latency_ms": round(health.avg_latency_ms, 1),
                "is_dead": health.is_dead,
            }

        for chain, endpoints in self._evm_health.items():
            chain_report = []
            for _url, health in endpoints.items():
                chain_report.append(_entry(health))
                report["summary"]["total_endpoints"] += 1
                if health.alive:
                    report["summary"]["alive"] += 1
                else:
                    report["summary"]["dead"] += 1
                report["summary"]["total_calls"] += health.total_calls
                report["summary"]["total_failures"] += health.total_failures
            report["evm_chains"][chain] = chain_report

        for url, health in self._solana_health.items():
            report["solana"][url] = _entry(health)
            report["summary"]["total_endpoints"] += 1
            if health.alive:
                report["summary"]["alive"] += 1
            else:
                report["summary"]["dead"] += 1
            report["summary"]["total_calls"] += health.total_calls
            report["summary"]["total_failures"] += health.total_failures

        return report


def _init_router_state(self: Any) -> None:
    """Initialize shared RpcRouter state (called from RpcRouter.__init__)."""
    # Initialize endpoint health trackers
    self._evm_health: dict[str, dict[str, EndpointHealth]] = defaultdict(dict)
    self._solana_health: dict[str, EndpointHealth] = {}

    # Shared httpx client (connection pooling)
    self._client: httpx.AsyncClient | None = None

    # ── Tier / rotation / quota state ──────────────────────────
    # Round-robin offset per chain: spreads consecutive calls across
    # the top healthy endpoints instead of hammering the same winner.
    self._rr_offset: dict[str, int] = defaultdict(int)
    # Freemium quota ledger: provider host -> recent call timestamps
    # (1h sliding window). Escalation picks the LEAST-used provider.
    self._quota: dict[str, list[float]] = defaultdict(list)
    # Escalation telemetry: how often the free tier failed us.
    self.freemium_escalations: int = 0
    self._last_escalation_log: dict[str, float] = {}

    # Performance tuning
    self._call_timeout: float = 10.0  # Per-endpoint timeout
    self._health_check_payloads: dict[str, tuple[str, list[Any]]] = {
        # EVM: eth_blockNumber
        "ethereum": ("eth_blockNumber", []),
        "bsc": ("eth_blockNumber", []),
        "polygon": ("eth_blockNumber", []),
        "arbitrum": ("eth_blockNumber", []),
        "base": ("eth_blockNumber", []),
        "optimism": ("eth_blockNumber", []),
        "avalanche": ("eth_blockNumber", []),
        "fantom": ("eth_blockNumber", []),
        "linea": ("eth_blockNumber", []),
        "zksync": ("eth_blockNumber", []),
        "scroll": ("eth_blockNumber", []),
        "mantle": ("eth_blockNumber", []),
        "gnosis": ("eth_blockNumber", []),
        # Solana: getHealth
        "solana": ("getHealth", []),
    }
