"""
Unified API Key Registry - Multi-Key Pools with Load Balancing

Discovers all API keys from environment variables and groups them
by provider into key pools. Each pool handles:
  - Round-robin key rotation
  - Per-key rate limiting (token bucket)
  - Per-key monthly quota tracking
  - Automatic fallback on 429/401
  - Health scoring (success rate, latency)

Providers managed:
  HELIUS (3 keys)     - Solana RPC
  QUICKNODE (1 key)   - Solana RPC
  ALCHEMY (1 key)     - Solana RPC
  SOLANA_TRACKER (2)  - Indexed Solana data
  BIRDEYE (1 key)     - Token analytics
  SOLSCAN (1 key)     - Block explorer API
  MORALIS (3 keys)    - EVM wallet data
  ETHERSCAN (1 key)   - EVM explorer
  COINGECKO (1 key)   - Price data
  THEGRAPH (1 key)    - Subgraph queries
  GOPLUS (1 key)      - Token security
  NANSEN (1 key)      - Wallet labels
  DUNE (1 key)        - Analytics
  ARKHAM (1 key)      - Entity intelligence
  LUNARCRUSH (1 key)  - Social signals
"""

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field

logger = logging.getLogger("api_registry")

# ═══════════════════════════════════════════════════════════════════════════
# PROVIDER DEFINITIONS
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class ProviderConfig:
    """Provider-level configuration."""

    name: str  # "helius", "solana_tracker", etc.
    env_prefix: str  # "HELIUS_API_KEY" prefix to scan
    rate_rps: float = 10.0  # Per-key rate limit (free tier default)
    burst: int = 15
    monthly_quota: int = 0  # 0 = unlimited
    base_url: str = ""  # API base URL
    auth_header: str = ""  # Header name for auth (x-api-key, Authorization, etc.)
    auth_prefix: str = ""  # Prefix before key value ("Bearer ", "Basic ", etc.)
    auth_in_query: str = ""  # Query param name for key (api_key, key, etc.)
    fallback_providers: list[str] = field(default_factory=list)  # Lower-priority alternatives
    notes: str = ""


# All known providers with their free tier limits
PROVIDER_REGISTRY: dict[str, ProviderConfig] = {
    # ═══ Solana RPC ═══
    "helius": ProviderConfig(
        name="helius",
        env_prefix="HELIUS_API_KEY",
        rate_rps=25.0,
        burst=25,
        monthly_quota=0,
        base_url="https://mainnet.helius-rpc.com",
        auth_in_query="api-key",
        notes="Free: 25 RPS per key. 2 keys configured (primary + _2). DAS API available for indexed token data.",
    ),
    "quicknode": ProviderConfig(
        name="quicknode",
        env_prefix="QUICKNODE_KEY",
        rate_rps=25.0,
        burst=25,
        base_url="https://quiknode.pro",
        notes="Free: 25 RPS. Solana mainnet RPC.",
    ),
    "alchemy_solana": ProviderConfig(
        name="alchemy_solana",
        env_prefix="ALCHEMY_SOLANA_KEY",
        rate_rps=25.0,
        burst=25,
        base_url="https://solana-mainnet.g.alchemy.com/v2",
        notes="Free: 25 RPS, 300M compute units/mo.",
    ),
    # ═══ EVM RPC / Explorer ═══
    "etherscan": ProviderConfig(
        name="etherscan",
        env_prefix="ETHERSCAN_API_KEY",
        rate_rps=5.0,
        burst=5,
        base_url="https://api.etherscan.io/api",
        auth_in_query="apikey",
        notes="Free: 5 RPS, 100K calls/day.",
    ),
    "blockscout": ProviderConfig(
        name="blockscout",
        env_prefix="BLOCKSCOUT_API_KEY",
        rate_rps=5.0,
        burst=5,
        base_url="https://api.blockscout.com",
        auth_in_query="apikey",
        notes="Free: 100K credits/day, 5 RPS, 100+ EVM chains. URL: /{chain_id}/api/v2/",
    ),
    "moralis": ProviderConfig(
        name="moralis",
        env_prefix="MORALIS_API_KEY",
        rate_rps=25.0,
        burst=25,
        base_url="https://deep-index.moralis.io/api/v2.2",
        auth_header="X-API-Key",
        notes="Free: 25 RPS. Keys: _2, _3 configured.",
    ),
    # ═══ Indexed Data / Analytics ═══
    "solana_tracker": ProviderConfig(
        name="solana_tracker",
        env_prefix="SOLANATRACKER",  # Custom key names
        rate_rps=3.0,
        burst=3,
        monthly_quota=2500,
        auth_header="x-api-key",
        notes="Free: 3 RPS, 2,500/mo. 2 accounts (secure + generic).",
    ),
    "birdeye": ProviderConfig(
        name="birdeye",
        env_prefix="BIRDEYE_API_KEY",
        rate_rps=5.0,
        burst=5,
        base_url="https://public-api.birdeye.so",
        auth_header="X-API-KEY",
        notes="Free tier: limited usage. Token prices, trades, holders.",
    ),
    "solscan": ProviderConfig(
        name="solscan",
        env_prefix="SOLSCAN_API_KEY",
        rate_rps=5.0,
        burst=5,
        base_url="https://pro-api.solscan.io",
        auth_header="token",
        notes="Pro API. Token metadata, holders, transactions.",
    ),
    "coingecko": ProviderConfig(
        name="coingecko",
        env_prefix="COINGECKO_API_KEY",
        rate_rps=30.0,
        burst=30,
        base_url="https://pro-api.coingecko.com/api/v3",
        auth_header="x-cg-pro-api-key",
        notes="Demo tier: 30 RPS. Price, market data, metadata.",
    ),
    # ═══ Intelligence ═══
    "goplus": ProviderConfig(
        name="goplus",
        env_prefix="GOPLUS_API_KEY",
        rate_rps=5.0,
        burst=5,
        base_url="https://api.gopluslabs.io/api/v1",
        notes="Token security scanning, honeypot detection.",
    ),
    "nansen": ProviderConfig(
        name="nansen",
        env_prefix="NANSEN_API_KEY",
        rate_rps=5.0,
        burst=5,
        base_url="https://api.nansen.ai",
        auth_in_query="api_key",
        notes="Wallet labels, smart money tracking.",
    ),
    "thegraph": ProviderConfig(
        name="thegraph",
        env_prefix="THEGRAPH_API_KEY",
        rate_rps=5.0,
        burst=5,
        base_url="https://gateway.thegraph.com/api",
        notes="Subgraph queries for DeFi data.",
    ),
    "dune": ProviderConfig(
        name="dune",
        env_prefix="DUNE_API_KEY",
        rate_rps=5.0,
        burst=5,
        base_url="https://api.dune.com/api/v1",
        auth_header="X-Dune-API-Key",
        notes="SQL analytics on blockchain data.",
    ),
    "arkham": ProviderConfig(
        name="arkham",
        env_prefix="ARKHAM_API_KEY",
        rate_rps=5.0,
        burst=5,
        base_url="https://api.arkhamintelligence.com",
        notes="Entity labeling, wallet intelligence.",
    ),
    "lunarcrush": ProviderConfig(
        name="lunarcrush",
        env_prefix="LUNARCRUSH_API_KEY",
        rate_rps=5.0,
        burst=5,
        base_url="https://lunarcrush.com/api/v2",
        notes="Social signals, sentiment analysis.",
    ),
}


# ═══════════════════════════════════════════════════════════════════════════
# KEY DISCOVERY
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class ApiKey:
    """A single API key with runtime state."""

    provider: str
    key_name: str  # "HELIUS_API_KEY", "HELIUS_API_KEY_2"
    value: str
    base_url: str = ""
    # Runtime tracking
    calls_total: int = 0
    calls_this_month: int = 0
    errors: int = 0
    rate_limited: int = 0
    last_used: float = 0.0
    last_error: str = ""
    # Rate limiting
    tokens: float = 0.0
    last_refill: float = 0.0
    rate_limited_until: float = 0.0
    consecutive_429s: int = 0
    disabled: bool = False

    def __post_init__(self):
        cfg = PROVIDER_REGISTRY.get(self.provider)
        burst = cfg.burst if cfg else 15
        self.tokens = float(burst)
        self.last_refill = time.monotonic()


def discover_keys() -> dict[str, list[ApiKey]]:
    """Scan environment variables and build key pools per provider.

    For each provider in PROVIDER_REGISTRY, scans env for:
      - {PREFIX} (primary key)
      - {PREFIX}_2, {PREFIX}_3, ... (additional keys)

    Also handles special cases (Solana Tracker custom keys, Moralis multi-key).
    """
    pools: dict[str, list[ApiKey]] = {}

    for prov_name, cfg in PROVIDER_REGISTRY.items():
        keys: list[ApiKey] = []

        # Special: Solana Tracker uses custom key naming
        if prov_name == "solana_tracker":
            # Secure endpoint (auth baked into subdomain)
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
            # Generic endpoint keys
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
                    # Build base URL with key if needed
                    if cfg.auth_in_query and key_val:
                        # Key goes in path for some (Alchemy, Helius)
                        pass  # Handled at call time

                    keys.append(
                        ApiKey(
                            provider=prov_name,
                            key_name=env_name,
                            value=key_val,
                            base_url=cfg.base_url if cfg.base_url else "",
                        )
                    )

            # Helius special: also check HELIUS_RPC_URL for custom endpoints
            if prov_name == "helius":
                custom_url = os.getenv("HELIUS_RPC_URL", "")
                if custom_url:
                    for k in keys:
                        k.base_url = custom_url

        if keys:
            pools[prov_name] = keys
            logger.info(f"API Registry: {prov_name} -> {len(keys)} key(s)")

    return pools


# ═══════════════════════════════════════════════════════════════════════════
# KEY POOL LOAD BALANCER
# ═══════════════════════════════════════════════════════════════════════════


class KeyPool:
    """Round-robin key pool with health tracking and rate limiting."""

    def __init__(self, provider: str, keys: list[ApiKey], config: ProviderConfig):
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

                # Refill token bucket
                elapsed = now - key.last_refill
                rate = self.config.rate_rps
                key.tokens = min(float(self.config.burst), key.tokens + elapsed * rate)
                key.last_refill = now

                if key.tokens < 1.0:
                    continue

                # Monthly quota check
                if self.config.monthly_quota > 0 and key.calls_this_month >= self.config.monthly_quota:
                    continue

                key.tokens -= 1.0
                key.calls_this_month += 1
                key.calls_total += 1
                key.last_used = now
                self.total_calls += 1
                return key

            return None

    def mark_success(self, key: ApiKey):
        key.consecutive_429s = 0
        key.rate_limited_until = 0.0

    def mark_rate_limited(self, key: ApiKey):
        now = time.monotonic()
        key.consecutive_429s += 1
        key.rate_limited += 1
        backoff = min(120, 5 * (2 ** min(key.consecutive_429s, 5)))
        key.rate_limited_until = now + backoff

    def mark_error(self, key: ApiKey, error: str):
        key.errors += 1
        key.last_error = error[:100]

    def stats(self) -> dict:
        now = time.monotonic()
        key_stats = []
        for k in self.keys:
            k.tokens = min(float(self.config.burst), k.tokens + (now - k.last_refill) * self.config.rate_rps)
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
            "active_keys": sum(1 for k in self.keys if not k.disabled and not (now < k.rate_limited_until)),
            "total_calls": self.total_calls,
            "rate_rps": self.config.rate_rps,
            "burst": self.config.burst,
            "monthly_quota": self.config.monthly_quota,
            "keys": key_stats,
        }


# ═══════════════════════════════════════════════════════════════════════════
# UNIFIED API MANAGER
# ═══════════════════════════════════════════════════════════════════════════


class UnifiedApiManager:
    """Singleton manager for all API key pools.

    Usage:
        mgr = get_api_manager()
        key = await mgr.acquire("helius")
        # ... make API call with key.value ...
        if success:
            mgr.mark_success("helius", key)
        elif rate_limited:
            mgr.mark_rate_limited("helius", key)
    """

    def __init__(self):
        self.pools: dict[str, KeyPool] = {}
        self._load()

    def _load(self):
        pools = discover_keys()
        for provider, keys in pools.items():
            config = PROVIDER_REGISTRY.get(provider)
            if config:
                self.pools[provider] = KeyPool(provider, keys, config)

    def reload(self):
        """Rescan environment for new keys."""
        self._load()

    async def acquire(self, provider: str) -> ApiKey | None:
        pool = self.pools.get(provider)
        if pool:
            return await pool.acquire()
        return None

    def mark_success(self, provider: str, key: ApiKey):
        pool = self.pools.get(provider)
        if pool:
            pool.mark_success(key)

    def mark_rate_limited(self, provider: str, key: ApiKey):
        pool = self.pools.get(provider)
        if pool:
            pool.mark_rate_limited(key)

    def mark_error(self, provider: str, key: ApiKey, error: str):
        pool = self.pools.get(provider)
        if pool:
            pool.mark_error(key, error)

    def get_pool(self, provider: str) -> KeyPool | None:
        return self.pools.get(provider)

    def list_providers(self) -> list[str]:
        return sorted(self.pools.keys())

    def capacity_report(self) -> dict:
        """Generate capacity analysis: RPS, quotas, bottlenecks."""
        providers = {}
        total_rps = 0.0
        bottlenecks = []

        for name, pool in self.pools.items():
            cfg = pool.config
            total_keys = len(pool.keys)
            combined_rps = cfg.rate_rps * total_keys
            combined_quota = cfg.monthly_quota * total_keys if cfg.monthly_quota > 0 else None
            total_rps += combined_rps

            status = "OK"
            if combined_rps < 10:
                status = "LOW_RPS"
                bottlenecks.append(f"{name}: only {combined_rps:.0f} RPS across {total_keys} key(s)")
            if combined_quota and combined_quota < 10000:
                status = "LOW_QUOTA"
                bottlenecks.append(f"{name}: only {combined_quota}/mo across {total_keys} key(s)")
            if total_keys == 1 and cfg.rate_rps < 10:
                status = "SINGLE_KEY_LIMITED"
                bottlenecks.append(f"{name}: single key at {cfg.rate_rps} RPS - get more accounts")

            providers[name] = {
                "keys": total_keys,
                "rps_per_key": cfg.rate_rps,
                "combined_rps": combined_rps,
                "monthly_quota": combined_quota,
                "status": status,
                "notes": cfg.notes,
            }

        return {
            "total_providers": len(providers),
            "total_combined_rps": round(total_rps, 1),
            "providers": providers,
            "bottlenecks": bottlenecks,
            "recommendations": _generate_recommendations(bottlenecks, providers),
        }


def _generate_recommendations(bottlenecks: list, providers: dict) -> list:
    """Generate actionable recommendations based on bottlenecks."""
    recs = []

    if any("helius" in b.lower() or "solana_tracker" in b.lower() for b in bottlenecks):
        recs.append("HIGH: Get 2-3 more Helius free accounts (email signup, instant key)")

    if any("solana_tracker" in b.lower() for b in bottlenecks):
        recs.append("HIGH: Get 1-2 more Solana Tracker free accounts (email signup)")

    if any("birdeye" in b.lower() for b in bottlenecks):
        recs.append("MEDIUM: Get 1 more Birdeye free account")

    if any("etherscan" in b.lower() for b in bottlenecks):
        recs.append("MEDIUM: Get 1 more Etherscan free account")

    if any("solscan" in b.lower() for b in bottlenecks):
        recs.append("LOW: Get 1 more Solscan Pro API key if needed for holder data")

    if any("coingecko" in b.lower() for b in bottlenecks):
        recs.append("LOW: CoinGecko Demo tier is generous at 30 RPS - likely sufficient")

    if not recs:
        recs.append("All providers have adequate capacity for current load.")

    return recs


# ═══════════════════════════════════════════════════════════════════════════
# SINGLETON
# ═══════════════════════════════════════════════════════════════════════════

_api_manager: UnifiedApiManager | None = None


def get_api_manager() -> UnifiedApiManager:
    global _api_manager
    if _api_manager is None:
        _api_manager = UnifiedApiManager()
    return _api_manager


# ═══════════════════════════════════════════════════════════════════════════
# BACKEND DATA SOURCES (No-Auth / Free / Self-Hosted / Scraped)
# ═══════════════════════════════════════════════════════════════════════════

BACKEND_SOURCES = {
    # ═══ DEX / Price Data (no auth, public APIs) ═══
    "dexscreener": {
        "type": "free_api",
        "url": "https://api.dexscreener.com",
        "rate_rps": 5.0,
        "burst": 5,
        "ttl_default": 30,
        "data": "Token pairs, liquidity, volume, price across all DEXs",
        "status": "in_use",
        "module": "unified_scanner.py, all_connectors.py",
    },
    "jupiter": {
        "type": "free_api",
        "url": "https://quote-api.jup.ag/v6",
        "rate_rps": 10.0,
        "burst": 15,
        "ttl_default": 10,
        "data": "Price quotes, swap routes, token list, strict list",
        "status": "in_use",
        "module": "all_connectors.py, unified_scanner.py",
    },
    "gecko_terminal": {
        "type": "free_api",
        "url": "https://api.geckoterminal.com/api/v2",
        "rate_rps": 5.0,
        "burst": 5,
        "ttl_default": 30,
        "data": "DEX pairs, OHLCV, token info across 100+ networks",
        "status": "available",
        "module": "not yet wired",
    },
    "coingecko_free": {
        "type": "free_api",
        "url": "https://api.coingecko.com/api/v3",
        "rate_rps": 10.0,
        "burst": 15,
        "ttl_default": 60,
        "data": "Price, market cap, volume (free tier - no key needed for basic)",
        "status": "in_use",
        "module": "coingecko_connector.py",
    },
    "coinpaprika": {
        "type": "free_api",
        "url": "https://api.coinpaprika.com/v1",
        "rate_rps": 5.0,
        "burst": 5,
        "ttl_default": 60,
        "data": "Prices, market data, exchanges - free, no auth",
        "status": "available",
        "module": "not yet wired",
    },
    "defillama": {
        "type": "free_api",
        "url": "https://yields.llama.fi",
        "rate_rps": 2.0,
        "burst": 2,
        "ttl_default": 300,
        "data": "TVL, yields, protocol data - free, no key",
        "status": "in_use",
        "module": "all_connectors.py",
    },
    "binance_public": {
        "type": "free_api",
        "url": "https://api.binance.com/api/v3",
        "rate_rps": 20.0,
        "burst": 30,
        "ttl_default": 5,
        "data": "CEX spot prices, tickers (public, no key)",
        "status": "in_use",
        "module": "web3.binance.com, adapters/",
    },
    # ═══ CEX Price Feeds (free, public) ═══
    "ccxt": {
        "type": "library",
        "url": "ccxt library (30+ exchanges)",
        "rate_rps": 10.0,
        "burst": 15,
        "ttl_default": 30,
        "data": "Unified CEX API: Binance, OKX, Bybit, Gate, MEXC, KuCoin, etc.",
        "status": "in_use",
        "module": "tools_integration.py (ccxt_get_prices, ccxt_arbitrage)",
    },
    # ═══ Public RPC Nodes (free, no key) ═══
    "solana_public_rpc": {
        "type": "public_rpc",
        "url": "api.mainnet-beta.solana.com, solana-rpc.publicnode.com, solana.drpc.org",
        "rate_rps": 15.0,
        "burst": 20,
        "ttl_default": 10,
        "data": "Solana RPC: getBalance, getAccountInfo, getSignaturesForAddress, etc.",
        "status": "in_use",
        "module": "consensus_rpc.py (3 public endpoints)",
    },
    "evm_public_rpc": {
        "type": "public_rpc",
        "url": "9 chains: ethereum, bsc, polygon, base, arbitrum, optimism, avalanche, fantom, gnosis",
        "rate_rps": 20.0,
        "burst": 25,
        "ttl_default": 10,
        "data": "EVM RPC via PublicNode, 1RPC, LlamaRPC, BlastAPI",
        "status": "in_use",
        "module": "consensus_rpc.py (EVM_RPC_PROVIDERS)",
    },
    # ═══ Security / Scam Detection ═══
    "chainabuse": {
        "type": "free_api",
        "url": "https://api.chainabuse.com",
        "rate_rps": 2.0,
        "burst": 2,
        "ttl_default": 3600,
        "data": "Scam reports, blacklisted addresses - free, no key",
        "status": "in_use",
        "module": "all_connectors.py, security_defense.py",
    },
    "cryptoscamdb": {
        "type": "free_api",
        "url": "https://api.cryptoscamdb.org/v1",
        "rate_rps": 2.0,
        "burst": 2,
        "ttl_default": 3600,
        "data": "Scam database, reported addresses - free, no key",
        "status": "in_use",
        "module": "all_connectors.py",
    },
    "honeypot_is": {
        "type": "free_api",
        "url": "https://api.honeypot.is/v2",
        "rate_rps": 3.0,
        "burst": 3,
        "ttl_default": 60,
        "data": "Honeypot detection for EVM tokens - free, no key",
        "status": "in_use",
        "module": "unified_scanner.py, all_connectors.py",
    },
    "rugcheck": {
        "type": "free_api",
        "url": "https://api.rugcheck.xyz/v1",
        "rate_rps": 5.0,
        "burst": 5,
        "ttl_default": 60,
        "data": "Solana token rug check, risk analysis - free, no key",
        "status": "available",
        "module": "not yet wired (could replace solsniffer)",
    },
    "blowfish": {
        "type": "free_api",
        "url": "https://api.blowfish.xyz",
        "rate_rps": 5.0,
        "burst": 5,
        "ttl_default": 120,
        "data": "Transaction simulation, scam detection - free tier available",
        "status": "available",
        "module": "all_connectors.py",
    },
    # ═══ Blockchain Explorers (public) ═══
    "solscan_public": {
        "type": "free_api",
        "url": "https://api-v2.solscan.io",
        "rate_rps": 3.0,
        "burst": 3,
        "ttl_default": 60,
        "data": "Solana account, token, tx data - public tier (rate limited)",
        "status": "in_use",
        "module": "free_solscan_client.py, unified_scanner.py",
    },
    "etherscan_public": {
        "type": "free_api",
        "url": "https://api.etherscan.io/api",
        "rate_rps": 1.0,
        "burst": 1,
        "ttl_default": 300,
        "data": "EVM contract verification, ABI - free tier 1 RPS (no key)",
        "status": "in_use",
        "module": "unified_scanner.py (fallback when keyed fails)",
    },
    # ═══ Self-Hosted / Imported Data ═══
    "wallet_labels_imported": {
        "type": "imported_data",
        "url": "local files: whalegod, sigmod, etherscan, solana labels",
        "rate_rps": None,  # no API calls - local DB
        "burst": None,
        "ttl_default": 86400,
        "data": "CEX wallets, scam labels, dapp labels, OFAC - pre-loaded into ClickHouse",
        "status": "in_use",
        "module": "wallet_memory/label_importer.py, wallet_label_loader.py",
    },
    "clickhouse_wallet_memory": {
        "type": "local_db",
        "url": "ClickHouse (langfuse-clickhouse-1:9000)",
        "rate_rps": None,
        "burst": None,
        "ttl_default": 86400,
        "data": "Wallet history, labels, risk scores - our own indexed DB",
        "status": "in_use",
        "module": "wallet_memory/storage.py",
    },
    "redis_rag": {
        "type": "local_db",
        "url": "Redis (rmi-redis:6379)",
        "rate_rps": None,
        "burst": None,
        "ttl_default": 86400,
        "data": "RAG vector embeddings, token analysis cache, alert history",
        "status": "in_use",
        "module": "rag_service.py, crypto_embeddings.py",
    },
    # ═══ RSS / News (free, public) ═══
    "crypto_news_rss": {
        "type": "rss_feed",
        "url": "65+ RSS feeds: CoinDesk, TheBlock, Decrypt, Bankless, etc.",
        "rate_rps": 0.1,  # polled on schedule, not per-request
        "burst": 1,
        "ttl_default": 900,
        "data": "Crypto news aggregation for bulletin + Telegram",
        "status": "in_use",
        "module": "news_service.py (15+ sources), all_connectors.py",
    },
    "blogwatcher": {
        "type": "cli_tool",
        "url": "blogwatcher CLI - local execution",
        "rate_rps": 0.05,
        "burst": 1,
        "ttl_default": 3600,
        "data": "Blog monitoring, RSS feed aggregation",
        "status": "in_use",
        "module": "tools_integration.py (blogwatcher_fetch)",
    },
    # ═══ AI / LLM ═══
    "deepseek": {
        "type": "paid_api",
        "url": "https://api.deepseek.com/v1",
        "rate_rps": 50.0,
        "burst": 100,
        "ttl_default": 3600,  # prompt cache
        "data": "LLM: DeepSeek V4 Pro + Flash (primary inference)",
        "status": "in_use",
        "module": "ai_router.py, hallucination_guard.py, rag_agentic.py",
    },
    "openrouter": {
        "type": "paid_api",
        "url": "https://openrouter.ai/api/v1",
        "rate_rps": 20.0,
        "burst": 30,
        "ttl_default": 3600,
        "data": "LLM: OpenRouter fallback (multi-model routing)",
        "status": "in_use",
        "module": "hallucination_guard.py, ai_router.py",
    },
    # ═══ Self-Hosted Services ═══
    "dify": {
        "type": "self_hosted",
        "url": "http://docker-api-1:5001",
        "rate_rps": None,  # local Docker
        "burst": None,
        "ttl_default": 300,
        "data": "Dify AI agent platform - chat, workflows, knowledge base",
        "status": "in_use",
        "module": "routers/admin_extensions.py (Dify chat proxy)",
    },
    "x402_gateway": {
        "type": "self_hosted",
        "url": "CF Workers: x402-base, x402-sol",
        "rate_rps": 100.0,
        "burst": 200,
        "ttl_default": 30,
        "data": "x402 MCP gateway - 231 tools, 14 categories, 13 chains",
        "status": "in_use",
        "module": "Cloudflare Workers, facilitators/",
    },
    "mcp_server": {
        "type": "self_hosted",
        "url": "FastAPI /mcp/* endpoints",
        "rate_rps": None,  # same process
        "burst": None,
        "ttl_default": 30,
        "data": "RMI MCP server - all token scanning, wallet analysis tools",
        "status": "in_use",
        "module": "routers/mcp_server.py",
    },
    # === Self-Built Modules ===
    "funding_tracer": {
        "type": "self_built",
        "url": "app/caching_shield/funding_tracer.py",
        "rate_rps": None,
        "burst": None,
        "ttl_default": 3600,
        "data": "EVM funding source forensics - traces wallet funding back to origin (CEX/DEX/bridge/mixer/contract).",
        "status": "built",
        "module": "funding_tracer.py",
    },
    "consensus_rpc": {
        "type": "self_built",
        "url": "app/consensus_rpc.py",
        "rate_rps": 75.0,
        "burst": 100,
        "ttl_default": 10,
        "data": "Multi-provider Solana RPC consensus voting (5+ providers, N-of-M). EVM via PublicNode/1RPC/BlastAPI.",
        "status": "in_use",
        "module": "consensus_rpc.py",
    },
    "unified_scanner": {
        "type": "self_built",
        "url": "app/unified_scanner.py",
        "rate_rps": None,
        "burst": None,
        "ttl_default": 3600,
        "data": "SENTINEL multi-chain scanner - wallet risk, token analysis, whale tracking. 15+ enrichment modules.",
        "status": "in_use",
        "module": "unified_scanner.py",
    },
}


def list_all_sources() -> dict:
    """Return combined view of all API keys + backend data sources."""
    from app.caching_shield.api_registry import get_api_manager

    mgr = get_api_manager()

    api_providers = {}
    for name, pool in mgr.pools.items():
        api_providers[name] = {
            "type": "api_key",
            "keys": pool.stats(),
            "config": {
                "rate_rps": pool.config.rate_rps,
                "monthly_quota": pool.config.monthly_quota,
            },
        }

    return {
        "api_key_providers": api_providers,
        "backend_sources": BACKEND_SOURCES,
        "total_api_keys": sum(len(pool.keys) for pool in mgr.pools.values()),
        "total_free_sources": len(BACKEND_SOURCES),
        "summary": _summarize_all(mgr),
    }


def _summarize_all(mgr) -> dict:
    """Generate a unified capacity summary across all sources."""
    free_rps = sum(s.get("rate_rps", 0) or 0 for s in BACKEND_SOURCES.values() if s.get("rate_rps"))
    by_type = {}
    for s in BACKEND_SOURCES.values():
        t = s["type"]
        by_type[t] = by_type.get(t, 0) + 1

    api_providers = mgr.list_providers()
    api_keys_total = sum(len(mgr.pools[p].keys) for p in api_providers)
    api_rps = sum(mgr.pools[p].config.rate_rps * len(mgr.pools[p].keys) for p in api_providers)

    return {
        "api_key_providers": len(api_providers),
        "api_keys_total": api_keys_total,
        "api_rps_combined": api_rps,
        "free_sources": len(BACKEND_SOURCES),
        "free_sources_by_type": by_type,
        "free_sources_rps": free_rps,
        "local_databases": ["ClickHouse (wallet_memory)", "Redis (cache + RAG)"],
        "self_hosted_services": ["Dify", "MCP Server", "x402 Gateway", "n8n"],
        "data_imported_locally": [
            "WhaleGod labels",
            "SigMod labels",
            "Etherscan labels (51K)",
            "Solana CEX/Dapp/DeFi labels (106K+)",
            "OFAC sanctions list",
            "Phishing scam DB (6.2K)",
        ],
    }
