"""
API Registry - Provider Configuration
======================================
ProviderConfig dataclass and the full PROVIDER_REGISTRY definition.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.logging import get_logger

logger = get_logger("api_registry")


@dataclass
class ProviderConfig:
    """Provider-level configuration."""

    name: str
    env_prefix: str
    rate_rps: float = 10.0
    burst: int = 15
    monthly_quota: int = 0
    base_url: str = ""
    auth_header: str = ""
    auth_prefix: str = ""
    auth_in_query: str = ""
    fallback_providers: list[str] = field(default_factory=list)
    notes: str = ""


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
        notes="Free: 25 RPS per key. 2 keys configured (primary + _2). DAS API available.",
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
        notes="Free: 100K credits/day, 5 RPS, 100+ EVM chains.",
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
        env_prefix="SOLANATRACKER",
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
