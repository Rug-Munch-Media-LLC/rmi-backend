"""
API Registry - Backend Data Sources
=====================================
Registry of all free/no-auth/self-hosted data sources used by the system.
"""

from __future__ import annotations

from .manager import get_api_manager

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
    # ═══ CEX Price Feeds ═══
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
        "rate_rps": None,
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
    # ═══ AI / LLM ═══
    "deepseek": {
        "type": "paid_api",
        "url": "https://api.deepseek.com/v1",
        "rate_rps": 50.0,
        "burst": 100,
        "ttl_default": 3600,
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
        "rate_rps": None,
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
        "rate_rps": None,
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
        "data": "EVM funding source forensics - traces wallet funding back to origin.",
        "status": "built",
        "module": "funding_tracer.py",
    },
    "consensus_rpc": {
        "type": "self_built",
        "url": "app/consensus_rpc.py",
        "rate_rps": 75.0,
        "burst": 100,
        "ttl_default": 10,
        "data": "Multi-provider Solana RPC consensus voting (5+ providers, N-of-M).",
        "status": "in_use",
        "module": "consensus_rpc.py",
    },
    "unified_scanner": {
        "type": "self_built",
        "url": "app/unified_scanner.py",
        "rate_rps": None,
        "burst": None,
        "ttl_default": 3600,
        "data": "SENTINEL multi-chain scanner - wallet risk, token analysis, whale tracking.",
        "status": "in_use",
        "module": "unified_scanner.py",
    },
}


def list_all_sources() -> dict:
    """Return combined view of all API keys + backend data sources."""
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
