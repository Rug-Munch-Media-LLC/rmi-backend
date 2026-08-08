"""
Consensus RPC Client - Multi-Provider Voting with Health Tracking.

Queries 5+ Solana RPC endpoints in parallel, compares results via
N-of-M consensus voting, and returns a ConsensusResult with confidence
score, agreed/disagreed/failed sources.

EVM support via chain_id mapping for cross-chain queries.

Pattern: Match queries from N providers → vote on result → return
consensus with confidence %. 5 providers, majority=3 for high confidence.

Depends on: httpx (async HTTP), HELIUS_API_KEY env var.
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)

# ── Chain → RPC URL Mapping ────────────────────────────────────────────────

# Solana RPC endpoints (free + keyed, no cost for public tiers)
SOLANA_RPC_ENDPOINTS = {
    "helius": "https://mainnet.helius-rpc.com/?api-key={key}",
    "quicknode": "https://quiknode.pro/{key}/solana-mainnet/",
    "alchemy": "https://solana-mainnet.g.alchemy.com/v2/{key}",
    "drpc": "https://solana.drpc.org",
    "publicnode": "https://solana-rpc.publicnode.com",
    "anvil": "https://api.mainnet-beta.solana.com",
}

# EVM chain_id → default PublicNode RPC
CHAIN_ID_TO_DEFAULT_RPC = {
    1: "https://ethereum-rpc.publicnode.com",
    56: "https://bsc-rpc.publicnode.com",
    137: "https://polygon-rpc.publicnode.com",
    8453: "https://base-rpc.publicnode.com",
    42161: "https://arbitrum-rpc.publicnode.com",
    10: "https://optimism-rpc.publicnode.com",
    43114: "https://avalanche-c-chain-rpc.publicnode.com",
    250: "https://fantom-rpc.publicnode.com",
}

# EVM multi-provider map: chain_id → [(name, url_template, requires_key)]
# url_template uses {key} if API-keyed
EVM_RPC_PROVIDERS: dict[int, list[tuple[str, str, bool]]] = {
    1: [
        ("ethereum_publicnode", "https://ethereum-rpc.publicnode.com", False),
        ("llama_rpc", "https://eth.llamarpc.com", False),
        ("1rpc", "https://1rpc.io/eth", False),
        ("blastapi", "https://eth-mainnet.public.blastapi.io", False),
    ],
    56: [
        ("bsc_publicnode", "https://bsc-rpc.publicnode.com", False),
        ("1rpc", "https://1rpc.io/bnb", False),
        ("blastapi", "https://bsc-mainnet.public.blastapi.io", False),
    ],
    137: [
        ("polygon_publicnode", "https://polygon-rpc.publicnode.com", False),
        ("1rpc", "https://1rpc.io/matic", False),
    ],
    8453: [
        ("base_publicnode", "https://base-rpc.publicnode.com", False),
        ("1rpc", "https://1rpc.io/base", False),
    ],
    42161: [
        ("arbitrum_publicnode", "https://arbitrum-rpc.publicnode.com", False),
        ("1rpc", "https://1rpc.io/arb", False),
    ],
}

# ── Data Classes ───────────────────────────────────────────────────────────


@dataclass
class RpcEndpoint:
    """A single RPC provider with health tracking."""

    name: str
    url: str
    weight: float = 1.0  # Initial weight; decays on failure
    requires_key: bool = False
    # Health tracking
    total_calls: int = 0
    success_calls: int = 0
    failure_calls: int = 0
    last_latency: float = 0.0
    last_error: str = ""
    is_disabled: bool = False

    @property
    def success_rate(self) -> float:
        if self.total_calls == 0:
            return 1.0
        return self.success_calls / self.total_calls

    @property
    def effective_weight(self) -> float:
        """Weight adjusted by health. Disabled endpoints get weight 0."""
        if self.is_disabled:
            return 0.0
        # Blend base weight with recent success rate (70/30)
        return self.weight * (0.7 + 0.3 * self.success_rate)


@dataclass
class ConsensusResult:
    """Result of a multi-provider consensus query."""

    value: Any = None
    confidence: float = 0.0  # 0-100%
    agreed_sources: list[str] = field(default_factory=list)
    disagreed_sources: list[str] = field(default_factory=list)
    failed_sources: list[str] = field(default_factory=list)
    total_sources: int = 0
    response_count: int = 0
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def is_reliable(self) -> bool:
        return self.confidence >= 60.0


# ── Consensus RPC Client ───────────────────────────────────────────────────


class ConsensusRpcClient:
    """Multi-provider RPC client with N-of-M consensus voting.

    Queries 5+ Solana RPC endpoints in parallel. Compares responses
    (JSON-string equality for complex types, direct equality for scalars).
    Returns the majority-agreed value with confidence score.

    For EVM chains, uses the chain_id → provider mapping with
    eth_call / eth_getBalance / etc.
    """

    # Individual timeout per endpoint query
    PER_ENDPOINT_TIMEOUT = 8.0

    # Minimum number of responders before we compute consensus
    MIN_RESPONDERS = 2

    # If an endpoint fails 3 times in a row, disable it for 60s
    MAX_CONSECUTIVE_FAILURES = 3
    DISABLE_DURATION = 60.0

    def __init__(self) -> None:
        self._endpoints: dict[str, RpcEndpoint] = {}
        self._lock = asyncio.Lock()
        self._setup_solana_endpoints()
        self._disabled_until: dict[str, float] = {}

    def _helius_keys(self) -> list[str]:
        """Return all configured Helius API keys (up to 3)."""
        keys = []
        for suffix in ("", "_2", "_3"):
            k = os.getenv(f"HELIUS_API_KEY{suffix}", "")
            if k and k != "your_helius_key_here" and len(k) > 10:
                keys.append(k)
        return keys

    def _setup_solana_endpoints(self) -> None:
        """Build Solana RPC endpoint roster with ALL keys where available.

        Each Helius key becomes its own endpoint (helius, helius_2, helius_3)
        so consensus voting can use them as independent providers with their
        own rate limits.
        """
        helius_keys = self._helius_keys()
        quicknode_key = os.getenv("QUICKNODE_KEY", "")
        alchemy_key = os.getenv("ALCHEMY_SOLANA_KEY", "")

        endpoints = []

        # Helius: one endpoint per key (each has its own 25 RPS free tier)
        for i, key in enumerate(helius_keys):
            suffix = f"_{i + 1}" if i > 0 else ""
            endpoints.append(
                (
                    f"helius{suffix}",
                    SOLANA_RPC_ENDPOINTS["helius"].format(key=key),
                    1.2,
                    True,
                )
            )

        # QuickNode
        if quicknode_key and quicknode_key != "your_quicknode_key_here":
            endpoints.append(
                (
                    "quicknode",
                    SOLANA_RPC_ENDPOINTS["quicknode"].format(key=quicknode_key),
                    1.1,
                    True,
                )
            )

        # Alchemy
        if alchemy_key and len(alchemy_key) > 5:
            endpoints.append(
                (
                    "alchemy",
                    SOLANA_RPC_ENDPOINTS["alchemy"].format(key=alchemy_key),
                    1.0,
                    True,
                )
            )

        # Public/free endpoints - always available
        endpoints += [
            ("drpc", SOLANA_RPC_ENDPOINTS["drpc"], 0.9, False),
            ("publicnode", SOLANA_RPC_ENDPOINTS["publicnode"], 0.8, False),
            ("anvil", SOLANA_RPC_ENDPOINTS["anvil"], 0.7, False),
        ]

        for name, url, weight, requires_key in endpoints:
            # Skip keyed providers if we don't have a valid key
            if requires_key and ("missing" in url or not url.split("/")[-1].strip()):
                logger.debug(f"Skipping {name}: no API key configured")
                continue
            self._endpoints[name] = RpcEndpoint(
                name=name,
                url=url,
                weight=weight,
                requires_key=requires_key,
            )

        active = [n for n, e in self._endpoints.items() if not e.is_disabled]
        logger.info(f"ConsensusRPC: {len(active)} Solana endpoints active: {active}")

    # ── Health Management ────────────────────────────────────────────────

    async def _record_success(self, name: str, latency: float) -> None:
        async with self._lock:
            ep = self._endpoints.get(name)
            if ep:
                ep.total_calls += 1
                ep.success_calls += 1
                ep.last_latency = latency
                ep.last_error = ""

    async def _record_failure(self, name: str, error: str) -> None:
        async with self._lock:
            ep = self._endpoints.get(name)
            if ep:
                ep.total_calls += 1
                ep.failure_calls += 1
                ep.last_error = error
                # Check for consecutive failures to disable
                # Only disable if success rate recently is 0
                if ep.failure_calls >= self.MAX_CONSECUTIVE_FAILURES and (
                    ep.success_calls == 0 or (ep.total_calls >= 5 and ep.success_rate < 0.2)
                ):
                    ep.is_disabled = True
                    self._disabled_until[name] = time.monotonic() + self.DISABLE_DURATION
                    logger.warning(
                        f"RPC {name} disabled for {self.DISABLE_DURATION}s "
                        f"(failures={ep.failure_calls}, rate={ep.success_rate:.0%})"
                    )

    async def _maybe_reenable(self, name: str) -> None:
        """Re-enable an endpoint after its disable duration expires."""
        until = self._disabled_until.get(name, 0)
        if until and time.monotonic() > until:
            async with self._lock:
                ep = self._endpoints.get(name)
                if ep and ep.is_disabled:
                    ep.is_disabled = False
                    ep.failure_calls = max(0, ep.failure_calls - 2)  # soft reset
                    del self._disabled_until[name]
                    logger.info(f"RPC {name} re-enabled after cooldown")

    # ── Core Query Logic ──────────────────────────────────────────────────

    async def _query_one(
        self,
        endpoint: RpcEndpoint,
        method: str,
        params: list[Any],
    ) -> tuple[str, dict[str, Any] | None, float, str | None]:
        """Query a single endpoint. Returns (name, result_dict, latency, error_string)."""
        await self._maybe_reenable(endpoint.name)
        if endpoint.is_disabled:
            return (endpoint.name, None, 0.0, "endpoint_disabled")

        start = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=self.PER_ENDPOINT_TIMEOUT) as client:
                r = await client.post(
                    endpoint.url,
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": method,
                        "params": params,
                    },
                    headers={"Content-Type": "application/json"},
                )
                latency = time.monotonic() - start
                if r.status_code == 200:
                    data = r.json()
                    await self._record_success(endpoint.name, latency)
                    return (endpoint.name, data, latency, None)
                elif r.status_code == 429:
                    await self._record_failure(endpoint.name, "rate_limited")
                    return (endpoint.name, None, latency, "rate_limited")
                else:
                    await self._record_failure(endpoint.name, f"HTTP_{r.status_code}")
                    return (endpoint.name, None, latency, f"HTTP_{r.status_code}")
        except httpx.TimeoutException:
            latency = time.monotonic() - start
            await self._record_failure(endpoint.name, "timeout")
            return (endpoint.name, None, latency, "timeout")
        except Exception as e:
            latency = time.monotonic() - start
            await self._record_failure(endpoint.name, str(e)[:200])
            return (endpoint.name, None, latency, str(e)[:200])

    async def query_with_consensus(
        self,
        method: str,
        params: list[Any],
        min_agreement: int = 2,
    ) -> ConsensusResult:
        """Query all endpoints in parallel and return consensus result.

        Args:
            method: JSON-RPC method name (e.g., 'getAccountInfo', 'getBalance')
            params: JSON-RPC params list
            min_agreement: Minimum number of agreeing sources for consensus

        Returns:
            ConsensusResult with voted value, confidence %, and source breakdown.
        """
        active = [ep for ep in self._endpoints.values() if not ep.is_disabled]
        if not active:
            logger.error("No active RPC endpoints available")
            return ConsensusResult(
                value=None,
                confidence=0.0,
                failed_sources=[ep.name for ep in self._endpoints.values()],
                total_sources=len(self._endpoints),
            )

        # Fire all queries in parallel with individual timeouts
        tasks = [self._query_one(ep, method, params) for ep in active]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Process results
        responses: dict[str, str] = {}  # name → normalized_value
        failures: dict[str, str] = {}  # name → error
        raw_results: dict[str, Any] = {}  # name → raw_data (for details)

        for r in results:
            if isinstance(r, Exception):
                logger.warning(f"Consensus query exception: {r}")
                continue
            name, data, _latency, error = r
            if error:
                failures[name] = error
            elif data is not None:
                # Normalize: extract 'result' field, use JSON repr for voting
                result_value = data.get("result")
                normalized = self._normalize(result_value)
                responses[name] = normalized
                raw_results[name] = data

        # Count votes per normalized value
        vote_counts: dict[str, list[str]] = {}
        for name, norm in responses.items():
            vote_counts.setdefault(norm, []).append(name)

        # Find the winning value (most votes)
        best_sources: list[str] = []
        for norm, sources in vote_counts.items():
            if len(sources) > len(best_sources):
                best_sources = sources

        # Determine agreed / disagreed / failed
        agreed = best_sources
        disagreed = [n for n in responses if n not in best_sources]
        failed = list(failures.keys())

        # Also count disabled as failed
        failed += [
            ep.name
            for ep in self._endpoints.values()
            if ep.is_disabled and ep.name not in agreed and ep.name not in disagreed
        ]

        total = len(self._endpoints)
        responded = len(responses)

        # Calculate confidence
        if responded < self.MIN_RESPONDERS:
            confidence = 0.0
        elif responded == 1:
            confidence = 30.0  # Single source is weak
        elif len(agreed) >= min_agreement:
            # Confidence = (agreed / responded) * 100, with bonus for supermajority
            agreement_ratio = len(agreed) / responded
            supermajority_bonus = min(0.2, (len(agreed) - min_agreement) / max(total, 1) * 0.2)
            confidence = min(100.0, (agreement_ratio * 80.0) + (supermajority_bonus * 100.0))
        else:
            # Weak consensus - disagreement
            confidence = max(10.0, (len(agreed) / responded) * 50.0)

        # Recover the actual value from the first agreeing source
        value = None
        if agreed:
            first_agreeing = agreed[0]
            if first_agreeing in raw_results:
                value = raw_results[first_agreeing].get("result")

        return ConsensusResult(
            value=value,
            confidence=round(confidence, 1),
            agreed_sources=agreed,
            disagreed_sources=disagreed,
            failed_sources=failed,
            total_sources=total,
            response_count=responded,
            details={
                "method": method,
                "params": params,
                "raw_results": {k: v.get("result") for k, v in raw_results.items()},
                "vote_counts": dict(vote_counts.items()),
            },
        )

    def _normalize(self, value: Any) -> str:
        """Normalize a value for comparison in voting."""
        if value is None:
            return "__NONE__"
        if isinstance(value, (bool, int, float, str)):
            return str(value)
        # For lists and dicts, use JSON representation
        import json

        try:
            return json.dumps(value, sort_keys=True, default=str)
        except (TypeError, ValueError):
            return str(value)

    # ── Convenience Methods ───────────────────────────────────────────────

    async def get_account_info(self, address: str) -> ConsensusResult:
        """Get Solana account info with consensus across providers."""
        return await self.query_with_consensus(
            "getAccountInfo",
            [address, {"encoding": "jsonParsed"}],
        )

    async def get_balance(self, address: str) -> ConsensusResult:
        """Get SOL balance with consensus."""
        return await self.query_with_consensus("getBalance", [address])

    async def get_token_supply(self, mint: str) -> ConsensusResult:
        """Get SPL token supply with consensus."""
        return await self.query_with_consensus("getTokenSupply", [mint])

    async def get_signatures_for_address(
        self,
        address: str,
        limit: int = 20,
    ) -> ConsensusResult:
        """Get transaction signatures with consensus."""
        return await self.query_with_consensus(
            "getSignaturesForAddress",
            [address, {"limit": limit}],
        )

    async def get_token_account_balance(self, token_account: str) -> ConsensusResult:
        """Get SPL token account balance with consensus."""
        return await self.query_with_consensus(
            "getTokenAccountBalance",
            [token_account],
        )

    # ── EVM Chain Support ─────────────────────────────────────────────────

    async def evm_query_with_consensus(
        self,
        chain_id: int,
        method: str,
        params: list[Any],
    ) -> ConsensusResult:
        """Query EVM chain RPC endpoints with consensus.

        For EVM, we use PublicNode + other free providers (1rpc, llama, blastapi).
        Map chain_id to provider list; fall back to PublicNode for unknown chains.

        Args:
            chain_id: Ethereum chain ID (1, 56, 8453, etc.)
            method: eth_* RPC method
            params: JSON-RPC params
        """
        providers = EVM_RPC_PROVIDERS.get(chain_id, [])
        if not providers:
            # Fallback: use the default PublicNode URL for this chain
            default_url = CHAIN_ID_TO_DEFAULT_RPC.get(chain_id)
            if default_url:
                providers = [(f"chain_{chain_id}_publicnode", default_url, False)]
            else:
                return ConsensusResult(
                    value=None,
                    confidence=0.0,
                    failed_sources=[f"unknown_chain_{chain_id}"],
                    total_sources=0,
                )

        # Build temporary endpoints for this query
        evm_endpoints = []
        for name, url, requires_key in providers:
            evm_endpoints.append(
                RpcEndpoint(
                    name=f"evm_{name}",
                    url=url,
                    weight=1.0,
                    requires_key=requires_key,
                )
            )

        # Run queries in parallel
        tasks = [self._query_one(ep, method, params) for ep in evm_endpoints]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        responses: dict[str, str] = {}
        failures: dict[str, str] = {}
        raw_results: dict[str, Any] = {}

        for r in results:
            if isinstance(r, Exception):
                continue
            name, data, _latency, error = r
            if error:
                failures[name] = error
            elif data is not None:
                result_value = data.get("result")
                responses[name] = self._normalize(result_value)
                raw_results[name] = data

        # Voting
        vote_counts: dict[str, list[str]] = {}
        for name, norm in responses.items():
            vote_counts.setdefault(norm, []).append(name)

        best_sources: list[str] = []
        for norm, sources in vote_counts.items():
            if len(sources) > len(best_sources):
                best_sources = sources[:]

        agreed = best_sources
        disagreed = [n for n in responses if n not in best_sources]
        total = len(evm_endpoints)
        responded = len(responses)

        if responded >= 2 and len(agreed) >= 2:
            confidence = min(100.0, (len(agreed) / responded) * 100.0)
        elif responded == 1:
            confidence = 40.0
        else:
            confidence = 0.0

        value = None
        if agreed and agreed[0] in raw_results:
            value = raw_results[agreed[0]].get("result")

        return ConsensusResult(
            value=value,
            confidence=round(confidence, 1),
            agreed_sources=agreed,
            disagreed_sources=disagreed,
            failed_sources=list(failures.keys()),
            total_sources=total,
            response_count=responded,
        )

    # ── Health & Stats ────────────────────────────────────────────────────

    async def health_summary(self) -> dict[str, Any]:
        """Return health report for all configured endpoints."""
        summary = {}
        async with self._lock:
            for name, ep in self._endpoints.items():
                summary[name] = {
                    "url": ep.url[:80],
                    "weight": ep.effective_weight,
                    "success_rate": round(ep.success_rate * 100, 1),
                    "total_calls": ep.total_calls,
                    "success_calls": ep.success_calls,
                    "failure_calls": ep.failure_calls,
                    "last_latency": round(ep.last_latency, 3),
                    "last_error": ep.last_error[:100],
                    "disabled": ep.is_disabled,
                }
        return summary

    async def stats(self) -> dict[str, Any]:
        """Aggregate statistics."""
        return {
            "total_endpoints": len(self._endpoints),
            "active": sum(1 for ep in self._endpoints.values() if not ep.is_disabled),
            "disabled": sum(1 for ep in self._endpoints.values() if ep.is_disabled),
            "health": await self.health_summary(),
        }


# ── Singleton ──────────────────────────────────────────────────────────────

_consensus_client: ConsensusRpcClient | None = None


def get_consensus_rpc() -> ConsensusRpcClient:
    """Get the global ConsensusRpcClient singleton."""
    global _consensus_client
    if _consensus_client is None:
        _consensus_client = ConsensusRpcClient()
    return _consensus_client
