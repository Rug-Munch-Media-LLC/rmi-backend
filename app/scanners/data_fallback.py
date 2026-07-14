"""
Intelligent Data Fallback Chain — Rug Munch Media
=================================================
Priority: OUR DATA → FREE RPCs → FREE APIs → PAID APIs

Never fail a query. Always get the data. This is the backbone
of the RMI intelligence engine — every token scan, wallet lookup,
and market query routes through this chain.

Architecture:
  Layer 1 (us):    NDJSON/Parquet → ClickHouse → Neo4j → Redis
  Layer 2 (free):  Free RPC pools (5+/chain) → TronGrid
  Layer 3 (free):  GoPlus → DexScreener → CoinGecko → DefiLlama → Blockscout → Etherscan
  Layer 4 (paid):  Helius → Moralis → Alchemy → DeepSeek
"""

from app.core.logging import get_logger
import time
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = get_logger(__name__)


class SourceTier(Enum):
    OUR_DATA = 1  # Free, unlimited, fastest
    FREE_RPC = 2  # Free, rate-limited
    FREE_API = 3  # Free, rate-limited
    PAID_API = 4  # Paid, highest quality, last resort


class SourceStatus(Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"  # Slow but working
    RATE_LIMITED = "rate_limited"
    DOWN = "down"
    UNKNOWN = "unknown"


@dataclass
class DataSource:
    """A single data source in the fallback chain."""

    name: str
    tier: SourceTier
    status: SourceStatus = SourceStatus.UNKNOWN
    avg_latency_ms: float = 0
    error_count: int = 0
    success_count: int = 0
    consecutive_failures: int = 0
    rate_limit_reset_at: float = 0  # Unix timestamp when rate limit resets
    max_consecutive_failures: int = 3  # Auto-degrade after this many failures

    @property
    def available(self) -> bool:
        """Is this source currently available?"""
        if self.status == SourceStatus.DOWN:
            return False
        if self.status == SourceStatus.RATE_LIMITED:
            return time.time() > self.rate_limit_reset_at
        return True

    @property
    def health_score(self) -> float:
        """0-100 health score."""
        if self.status == SourceStatus.HEALTHY:
            return 100.0
        elif self.status == SourceStatus.DEGRADED:
            return 50.0
        elif self.status == SourceStatus.RATE_LIMITED:
            return 25.0 if time.time() > self.rate_limit_reset_at else 0
        return 0.0

    @property
    def tier_label(self) -> str:
        return {1: "OUR", 2: "RPC", 3: "API", 4: "PAID"}[self.tier.value]


@dataclass
class DataResult:
    """Result from a data query with source tracing."""

    data: Any
    source: str
    tier: str
    latency_ms: float
    from_cache: bool = False
    fallback_attempts: int = 0
    all_sources_tried: list[str] = field(default_factory=list)


# ══════════════════════════════════════════════════════════════════════
# DATA SOURCE REGISTRY — Ordered by priority
# ══════════════════════════════════════════════════════════════════════

DATA_SOURCES = {
    # ── LAYER 1: Our Own Data ────────────────────────────────────
    "chain_indexer_parquet": DataSource(
        name="Chain Indexer (Parquet)",
        tier=SourceTier.OUR_DATA,
    ),
    "chain_indexer_jsonl": DataSource(
        name="Chain Indexer (JSONL)",
        tier=SourceTier.OUR_DATA,
    ),
    "clickhouse_labels": DataSource(
        name="ClickHouse (39M+ labels)",
        tier=SourceTier.OUR_DATA,
    ),
    "neo4j_entities": DataSource(
        name="Neo4j (169K+ entities)",
        tier=SourceTier.OUR_DATA,
    ),
    "redis_cache": DataSource(
        name="Redis Cache",
        tier=SourceTier.OUR_DATA,
    ),
    # ── LAYER 2: Free RPC Pools ─────────────────────────────────
    "rpc_ethereum": DataSource(
        name="Ethereum RPC Pool (LlamaRPC→DRPC→PublicNode→Ankr→1RPC)",
        tier=SourceTier.FREE_RPC,
    ),
    "rpc_bsc": DataSource(
        name="BSC RPC Pool (LlamaRPC→Binance→Defibit→PublicNode→DRPC)",
        tier=SourceTier.FREE_RPC,
    ),
    "rpc_arbitrum": DataSource(
        name="Arbitrum RPC Pool (Arbitrum.io→DRPC→PublicNode)",
        tier=SourceTier.FREE_RPC,
    ),
    "rpc_base": DataSource(
        name="Base RPC Pool (Base.org→LlamaRPC→DRPC→BlastAPI)",
        tier=SourceTier.FREE_RPC,
    ),
    "rpc_polygon": DataSource(
        name="Polygon RPC Pool (LlamaRPC→PolygonRPC→DRPC→BlastAPI)",
        tier=SourceTier.FREE_RPC,
    ),
    "rpc_optimism": DataSource(
        name="Optimism RPC Pool (Optimism.io→LlamaRPC→DRPC→BlastAPI)",
        tier=SourceTier.FREE_RPC,
    ),
    "rpc_avalanche": DataSource(
        name="Avalanche RPC Pool (Avax→DRPC→MeowRPC)",
        tier=SourceTier.FREE_RPC,
    ),
    "rpc_tron": DataSource(
        name="TronGrid API (free, no key)",
        tier=SourceTier.FREE_RPC,
    ),
    "rpc_solana_public": DataSource(
        name="Solana Public RPC Pool (api.mainnet-beta.solana.com→publicnode)",
        tier=SourceTier.FREE_RPC,
    ),
    # ── LAYER 3: Free APIs ──────────────────────────────────────
    "goplus_token_security": DataSource(
        name="GoPlus Labs (token security, free)",
        tier=SourceTier.FREE_API,
    ),
    "dexscreener": DataSource(
        name="DexScreener (prices, pairs, no key)",
        tier=SourceTier.FREE_API,
    ),
    "coingecko": DataSource(
        name="CoinGecko (market data, free tier)",
        tier=SourceTier.FREE_API,
    ),
    "defillama": DataSource(
        name="DefiLlama (TVL, protocols, no key)",
        tier=SourceTier.FREE_API,
    ),
    "coinpaprika": DataSource(
        name="CoinPaprika (tickers, global, free tier)",
        tier=SourceTier.FREE_API,
    ),
    "blockscout": DataSource(
        name="Blockscout (EVM explorer, no key)",
        tier=SourceTier.FREE_API,
    ),
    "etherscan_free": DataSource(
        name="Etherscan (free tier, 5 req/s)",
        tier=SourceTier.FREE_API,
    ),
    "bscscan_free": DataSource(
        name="BscScan (free tier)",
        tier=SourceTier.FREE_API,
    ),
    "tradingview": DataSource(
        name="TradingView (market data, free)",
        tier=SourceTier.FREE_API,
    ),
    "rugcheck": DataSource(
        name="RugCheck (token safety, free)",
        tier=SourceTier.FREE_API,
    ),
    "gmgn_cli": DataSource(
        name="GMGN (smart money, KOL, signals, trenches, trend, token security — free)",
        tier=SourceTier.FREE_API,
    ),
    "birdeye": DataSource(
        name="Birdeye (token holders, trending, prices, multi-chain, free tier)",
        tier=SourceTier.FREE_API,
    ),
    "crypto_feargreed": DataSource(
        name="Fear & Greed Index (free)",
        tier=SourceTier.FREE_API,
    ),
    "jupiter_api": DataSource(
        name="Jupiter Quote API (Solana prices, free, no key)",
        tier=SourceTier.FREE_API,
    ),
    "dexpaprika": DataSource(
        name="DexPaprika (DEX data, free, no key)",
        tier=SourceTier.FREE_API,
    ),
    "coinstats": DataSource(
        name="CoinStats (market overview, free tier)",
        tier=SourceTier.FREE_API,
    ),
    # ── LAYER 4: Paid APIs (Last Resort) ────────────────────────
    "helius_solana": DataSource(
        name="Helius (Solana RPC, paid)",
        tier=SourceTier.PAID_API,
    ),
    "moralis": DataSource(
        name="Moralis (multi-chain, paid)",
        tier=SourceTier.PAID_API,
    ),
    "alchemy_evm": DataSource(
        name="Alchemy (EVM, paid)",
        tier=SourceTier.PAID_API,
    ),
    "deepseek_api": DataSource(
        name="DeepSeek API (AI reasoning, paid)",
        tier=SourceTier.PAID_API,
    ),
}


# ══════════════════════════════════════════════════════════════════════
# QUERY-TO-SOURCE MAPPING — Which sources can answer which queries
# ══════════════════════════════════════════════════════════════════════

QUERY_FALLBACK_MAP = {
    "token_price": [
        "redis_cache",  # Cache hit first
        "dexscreener",  # Free, no key, live prices — all chains
        "coingecko",  # Free tier, comprehensive
        "coinpaprika",  # Free tier, global + per-token
        "defillama",  # Free, no key, token prices API
        "dexpaprika",  # Free, DEX-level prices
        "coinstats",  # Free tier, real-time
        "tradingview",  # Free, multi-market
        "moralis",  # Paid, last resort
    ],
    "token_security": [
        "redis_cache",
        "goplus_token_security",  # Free, comprehensive, all chains
        "rugcheck",  # Free, contract-level checks
        "dexscreener",  # Free, pair-level safety signals
        "etherscan_free",  # Free tier, contract verification
        "moralis",  # Paid
    ],
    "token_holders": [
        "chain_indexer_parquet",  # Our data — every transfer on 8 chains
        "chain_indexer_jsonl",  # Raw events for chains not yet in Parquet
        "rpc_ethereum",  # Free RPC fallback — eth_call for balances
        "rpc_bsc",  # BSC RPC fallback
        "blockscout",  # Free explorer API
        "etherscan_free",  # Free tier token holder API
        "bscscan_free",  # BSC free tier
        "moralis",  # Paid
    ],
    "token_transfers": [
        "chain_indexer_parquet",  # Our indexed data — 8 chains, every transfer
        "chain_indexer_jsonl",  # Raw NDJSON — Tron + edge cases
        "rpc_ethereum",  # Free RPC pool — eth_getLogs
        "rpc_bsc",  # BSC RPC
        "rpc_arbitrum",  # Arbitrum RPC
        "rpc_base",  # Base RPC
        "rpc_polygon",  # Polygon RPC
        "rpc_optimism",  # Optimism RPC
        "rpc_avalanche",  # Avalanche RPC
        "rpc_tron",  # TronGrid — free, no key
        "blockscout",  # Free explorer API
        "etherscan_free",  # Free tier
        "moralis",  # Paid
    ],
    "wallet_profile": [
        "clickhouse_labels",  # 39M+ labels
        "neo4j_entities",  # 169K+ relationships
        "chain_indexer_parquet",  # Transfer history
        "blockscout",  # Free
        "moralis",  # Paid
    ],
    "wallet_balance": [
        "redis_cache",
        "rpc_ethereum",  # Free RPC pool
        "rpc_bsc",
        "blockscout",
        "alchemy_evm",  # Paid
    ],
    "contract_abi": [
        "blockscout",  # Free verified contracts
        "etherscan_free",
        "bscscan_free",
    ],
    "market_overview": [
        "redis_cache",
        "coingecko",
        "coinpaprika",
        "defillama",
        "tradingview",
    ],
    "trending_tokens": [
        "dexscreener",  # Free, real-time
        "coingecko",
        "coinpaprika",
    ],
    "protocol_tvl": [
        "redis_cache",
        "defillama",  # Free, comprehensive
    ],
    "fear_greed": [
        "redis_cache",
        "crypto_feargreed",  # Free
    ],
    "gas_price": [
        "rpc_ethereum",  # Free RPC
        "rpc_bsc",
        "rpc_arbitrum",
        "etherscan_free",
    ],
    "solana_data": [
        "redis_cache",
        "rpc_solana_public",  # Free public Solana RPCs
        "dexscreener",  # Covers Solana pairs
        "jupiter_api",  # Free Jupiter API for Solana prices
        "helius_solana",  # Paid — last resort
    ],
    "news_sentiment": [
        "redis_cache",
        "coinstats",  # Free tier
        "deepseek_api",  # AI analysis, paid
    ],
    "wallet_label": [
        "redis_cache",
        "clickhouse_labels",  # Our 39M labels
        "neo4j_entities",  # Our graph
        "blockscout",  # Free labels
        "etherscan_free",  # Free labels
        "gmgn_cli",  # GMGN wallet tags (smart_degen, photon, axiom)
    ],
    "smart_money": [
        "redis_cache",
        "gmgn_cli",  # GMGN smart money tracking (primary)
        "clickhouse_labels",  # Our label DB
        "neo4j_entities",  # Entity relationships
    ],
    "kol_tracking": [
        "gmgn_cli",  # GMGN KOL trades (primary — unique to GMGN)
    ],
    "market_signals": [
        "redis_cache",
        "gmgn_cli",  # GMGN signals: price spikes, smart buys
        "dexscreener",  # Pair-level signals
        "tradingview",  # Technical signals
    ],
    "new_launches": [
        "chain_indexer_parquet",  # Our deployer data
        "gmgn_cli",  # GMGN trenches (pump.fun graduation)
        "dexscreener",  # New pairs
        "birdeye",  # Trending new tokens
    ],
}


# ══════════════════════════════════════════════════════════════════════
# FALLBACK CHAIN ENGINE
# ══════════════════════════════════════════════════════════════════════


class DataFallbackChain:
    """
    Intelligent data fallback chain.

    Usage:
        chain = DataFallbackChain()
        result = await chain.query("token_price", token="0x...", chain="ethereum")
        # → tries sources in order, returns first success with source tracing
    """

    def __init__(self):
        self.sources = DATA_SOURCES.copy()
        self.query_map = QUERY_FALLBACK_MAP.copy()
        self._cache: dict[str, tuple[float, Any]] = {}  # simple in-memory cache
        self._stats: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self._last_health_check: float = 0
        self.health_check_interval: float = 60  # seconds

    def get_sources_for_query(self, query_type: str) -> list[str]:
        """Get ordered list of source names for a query type."""
        sources = self.query_map.get(query_type, [])
        if not sources:
            logger.warning(f"No fallback sources defined for query type: {query_type}")
            return []

        # Filter to available sources only
        available = []
        for src_name in sources:
            src = self.sources.get(src_name)
            if src and src.available:
                available.append(src_name)
            elif src:
                logger.debug(f"Source {src_name} unavailable: {src.status.value}")

        return available

    def record_success(self, source_name: str, latency_ms: float):
        """Record a successful query from a source."""
        src = self.sources.get(source_name)
        if not src:
            return

        src.success_count += 1
        src.consecutive_failures = 0
        src.avg_latency_ms = (
            src.avg_latency_ms * (src.success_count - 1) + latency_ms
        ) / src.success_count

        if src.status in (SourceStatus.DOWN, SourceStatus.DEGRADED):
            src.status = SourceStatus.HEALTHY
            logger.info(f"Source recovered: {source_name} → HEALTHY")

        self._stats[source_name]["success"] += 1

    def record_failure(self, source_name: str, error: str = ""):
        """Record a failed query from a source."""
        src = self.sources.get(source_name)
        if not src:
            return

        src.error_count += 1
        src.consecutive_failures += 1
        self._stats[source_name]["failure"] += 1

        if src.consecutive_failures >= src.max_consecutive_failures:
            old_status = src.status
            src.status = SourceStatus.DOWN
            logger.warning(
                f"Source degraded: {source_name} → DOWN "
                f"(was {old_status.value}, {src.consecutive_failures} consecutive failures)"
            )

        # Handle rate limiting specially
        if "429" in error or "rate limit" in error.lower():
            src.status = SourceStatus.RATE_LIMITED
            src.rate_limit_reset_at = time.time() + 60  # Assume 60s reset
            logger.warning(f"Source rate limited: {source_name}, reset in 60s")

    def record_rate_limit(self, source_name: str, retry_after_seconds: float = 60):
        """Record a rate limit hit with known retry time."""
        src = self.sources.get(source_name)
        if not src:
            return
        src.status = SourceStatus.RATE_LIMITED
        src.rate_limit_reset_at = time.time() + retry_after_seconds
        self._stats[source_name]["rate_limited"] += 1

    def get_cached(self, cache_key: str, ttl: float = 60) -> Any | None:
        """Get from local cache if fresh."""
        if cache_key in self._cache:
            timestamp, data = self._cache[cache_key]
            if time.time() - timestamp < ttl:
                return data
            del self._cache[cache_key]
        return None

    def set_cache(self, cache_key: str, data: Any):
        """Store in local cache."""
        self._cache[cache_key] = (time.time(), data)

    def get_health_report(self) -> dict[str, Any]:
        """Generate a health report for all data sources."""
        report = {
            "timestamp": time.time(),
            "sources": {},
            "summary": {
                "total": len(self.sources),
                "healthy": 0,
                "degraded": 0,
                "down": 0,
                "rate_limited": 0,
                "our_data_healthy": 0,
                "free_rpc_healthy": 0,
                "free_api_healthy": 0,
                "paid_api_healthy": 0,
            },
        }

        for name, src in self.sources.items():
            report["sources"][name] = {
                "tier": src.tier_label,
                "status": src.status.value,
                "health_score": src.health_score,
                "latency_ms": round(src.avg_latency_ms, 1),
                "success_count": src.success_count,
                "error_count": src.error_count,
            }

            if src.status == SourceStatus.HEALTHY:
                report["summary"]["healthy"] += 1
                tier_key = f"{src.tier.name.lower()}_healthy"
                if tier_key in report["summary"]:
                    report["summary"][tier_key] += 1
            elif src.status == SourceStatus.DEGRADED:
                report["summary"]["degraded"] += 1
            elif src.status == SourceStatus.DOWN:
                report["summary"]["down"] += 1
            elif src.status == SourceStatus.RATE_LIMITED:
                report["summary"]["rate_limited"] += 1

        # Overall data independence score
        our_healthy = report["summary"]["our_data_healthy"]
        total_queries = sum(src.success_count + src.error_count for src in self.sources.values())
        ours_pct = 0
        if total_queries > 0:
            ours_hits = sum(
                self._stats.get(name, {}).get("success", 0)
                for name in self.sources
                if self.sources[name].tier == SourceTier.OUR_DATA
            )
            ours_pct = round(ours_hits / total_queries * 100, 1)

        report["summary"]["data_independence_pct"] = ours_pct
        report["summary"]["total_queries"] = total_queries

        return report

    def get_source_stats(self) -> dict[str, dict[str, int]]:
        """Get per-source query statistics."""
        return dict(self._stats)


# ══════════════════════════════════════════════════════════════════════
# GLOBAL INSTANCE
# ══════════════════════════════════════════════════════════════════════

_fallback_chain: DataFallbackChain | None = None


def get_fallback_chain() -> DataFallbackChain:
    """Get or create the global DataFallbackChain instance."""
    global _fallback_chain
    if _fallback_chain is None:
        _fallback_chain = DataFallbackChain()
        logger.info("DataFallbackChain initialized")
    return _fallback_chain


def query_with_fallback(
    query_type: str, cache_key: str | None = None, cache_ttl: float = 60, **kwargs
) -> DataResult:
    """
    Query the fallback chain synchronously (health check, stats).
    For async queries, use the DataBus provider system.

    Returns a DataResult with source tracing.
    """
    chain = get_fallback_chain()

    # Check cache
    if cache_key:
        cached = chain.get_cached(cache_key, cache_ttl)
        if cached is not None:
            return DataResult(
                data=cached,
                source="redis_cache",
                tier="OUR",
                latency_ms=0,
                from_cache=True,
            )

    sources = chain.get_sources_for_query(query_type)

    return DataResult(
        data=None,
        source="none",
        tier="none",
        latency_ms=0,
        all_sources_tried=sources,
    )


# ══════════════════════════════════════════════════════════════════════
# SOURCE-DECISION LOGIC — "Can our data answer this?"
# ══════════════════════════════════════════════════════════════════════


def can_answer_from_our_data(query_type: str, chain: str = "ethereum") -> tuple[bool, str]:
    """
    Check if we can answer this query from our own data.
    Returns (can_answer, best_source).
    """
    chain_lower = chain.lower()

    # Token transfers: chain-indexer has every ERC-20 transfer
    if query_type in ("token_transfers", "token_holders"):
        if chain_lower in (
            "ethereum",
            "bsc",
            "arbitrum",
            "base",
            "polygon",
            "optimism",
            "avalanche",
        ):
            return True, "chain_indexer_parquet"
        if chain_lower == "tron":
            return True, "chain_indexer_jsonl"  # Tron has JSONL, not Parquet
        if chain_lower == "solana":
            return False, "helius_solana"  # Free RPC pool not available for Solana — use Helius

    # Wallet labels: ClickHouse has 39M+ entries
    if query_type in ("wallet_label", "wallet_profile"):
        return True, "clickhouse_labels"

    # Market data: we don't store prices ourselves — 8 free API fallbacks
    if query_type in ("token_price", "market_overview", "trending_tokens"):
        return (
            False,
            "free_api_pool",
        )  # DexScreener→CoinGecko→CoinPaprika→DefiLlama→DexPaprika→CoinStats→TradingView

    return False, "unknown_query_type"


# ══════════════════════════════════════════════════════════════════════
# INIT
# ══════════════════════════════════════════════════════════════════════

logger.info(
    f"DataFallbackChain module loaded: {len(DATA_SOURCES)} sources, {len(QUERY_FALLBACK_MAP)} query types"
)
