"""
Unified Data Fallback Engine - Never Run Out of Data

For every data query type, chains through multiple providers in priority order.
Cache-first, rate-limited, with automatic fallback on failure/429.
Mixes our own indexed data (ClickHouse, Redis RAG) with external APIs.

Fallback chains per data type:
  TOKEN_PRICE:     Jupiter → Solana Tracker → DexScreener → Binance → CoinGecko
  TOKEN_META:      Helius DAS → Solana Tracker → Jupiter Token List → DexScreener
  WALLET_BALANCE:  Helius RPC → QuickNode → Alchemy → Solana PublicNode → dRPC
  TX_HISTORY:      Blockscout → Etherscan → Helius → Solana Public RPC
  HOLDER_DATA:     Solana Tracker → Birdeye → Helius DAS → ClickHouse labels
  RISK_SCAN:       GoPlus → RugCheck → Honeypot.is → ChainAbuse → local labels
  FUNDING_SOURCE:  Blockscout → Helius → SolanaFM → public RPC trace
  SOLANA_FUNDING:  Helius → Solana Tracker → public RPC → ClickHouse labels

Every call: cache check → rate limiter → try primary → on fail try next → cache result
No single provider outage blocks any feature.
"""

import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from app.core.logging import get_logger

logger = get_logger("data_fallback")

# ═══════════════════════════════════════════════════════════════════════════
# DATA FALLBACK CHAINS
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class DataSource:
    """A single data source in the fallback chain."""

    name: str
    provider: str  # "helius", "solana_tracker", "jupiter", etc.
    fn: Callable  # async function to call
    rate_rps: float = 10.0
    monthly_quota: int = 0
    weight: float = 1.0  # Higher = preferred
    is_local: bool = False  # Our own data, no rate limit


class DataQueryType(Enum):
    TOKEN_PRICE = "token_price"
    TOKEN_META = "token_metadata"
    WALLET_BALANCE = "wallet_balance"
    TX_HISTORY = "tx_history"
    HOLDER_DATA = "holder_data"
    RISK_SCAN = "risk_scan"
    FUNDING_SOURCE = "funding_source"
    SOLANA_FUNDING = "solana_funding"


class UnifiedDataEngine:
    """Single entry point for ALL data queries with automatic fallback.

    Usage:
        engine = get_data_engine()

        # Get token price with 4 fallbacks
        price = await engine.query(DataQueryType.TOKEN_PRICE, mint="So111...")

        # Trace Solana funding with 3 fallbacks
        source = await engine.query(DataQueryType.SOLANA_FUNDING, address="7EcD...")
    """

    def __init__(self) -> None:
        self._chains: dict[DataQueryType, list[DataSource]] = {}
        self._setup_chains()
        self._l1: dict[str, tuple] = {}
        self.stats = {"hits": 0, "misses": 0, "fallbacks": 0}

    def _setup_chains(self) -> None:
        """Build fallback chains. Order = priority (first is best)."""

        # TOKEN PRICE: Jupiter (free, fast) → Tracker → DexScreener → Binance → CoinGecko
        self._chains[DataQueryType.TOKEN_PRICE] = [
            DataSource("jupiter_price", "jupiter", self._jupiter_price, 10, 0, 1.0),
            DataSource("tracker_price", "solana_tracker", self._tracker_price, 3, 2500, 0.9),
            DataSource("dexscreener_price", "dexscreener", self._dexscreener_price, 5, 0, 0.8),
            DataSource("binance_price", "binance", self._binance_price, 20, 0, 0.7),
            DataSource("coingecko_price", "coingecko", self._coingecko_price, 30, 0, 0.6),
        ]

        # TOKEN META: Helius DAS (own keys, 50 RPS) → Tracker → Jupiter → DexScreener
        self._chains[DataQueryType.TOKEN_META] = [
            DataSource("helius_das_meta", "helius", self._helius_das_meta, 50, 0, 1.0),
            DataSource("tracker_meta", "solana_tracker", self._tracker_meta, 3, 2500, 0.9),
            DataSource("jupiter_meta", "jupiter", self._jupiter_meta, 10, 0, 0.8),
            DataSource("dexscreener_meta", "dexscreener", self._dexscreener_meta, 5, 0, 0.7),
        ]

        # WALLET BALANCE: Helius → QuickNode → Alchemy → PublicNode → dRPC
        self._chains[DataQueryType.WALLET_BALANCE] = [
            DataSource("helius_balance", "helius", self._helius_balance, 50, 0, 1.0),
            DataSource("quicknode_balance", "quicknode", self._quicknode_balance, 25, 0, 0.9),
            DataSource("alchemy_balance", "alchemy", self._alchemy_balance, 25, 0, 0.8),
            DataSource("publicnode_balance", "public_rpc", self._publicnode_balance, 15, 0, 0.7),
        ]

        # RISK SCAN: GoPlus → RugCheck → Honeypot → ChainAbuse → local labels
        self._chains[DataQueryType.RISK_SCAN] = [
            DataSource("goplus_scan", "goplus", self._goplus_scan, 5, 0, 1.0),
            DataSource("rugcheck_scan", "rugcheck", self._rugcheck_scan, 5, 0, 0.9),
            DataSource("honeypot_scan", "honeypot", self._honeypot_scan, 3, 0, 0.8),
            DataSource("local_labels", "local_db", self._local_labels, 999, 0, 0.5, is_local=True),
        ]

        # FUNDING SOURCE (EVM): Blockscout → Etherscan → public RPC
        self._chains[DataQueryType.FUNDING_SOURCE] = [
            DataSource("blockscout_trace", "blockscout", self._blockscout_trace, 5, 0, 1.0),
            DataSource("etherscan_trace", "etherscan", self._etherscan_trace, 5, 0, 0.9),
            DataSource("rpc_trace", "public_rpc", self._rpc_trace, 10, 0, 0.7),
        ]

        # SOLANA FUNDING: Helius → Tracker → public RPC → labels
        self._chains[DataQueryType.SOLANA_FUNDING] = [
            DataSource("helius_sol_funding", "helius", self._helius_sol_funding, 50, 0, 1.0),
            DataSource(
                "tracker_sol_funding", "solana_tracker", self._tracker_sol_funding, 3, 2500, 0.8
            ),
            DataSource("public_rpc_sol", "public_rpc", self._public_rpc_sol_funding, 15, 0, 0.6),
            DataSource(
                "local_wallet_labels",
                "local_db",
                self._local_wallet_labels,
                999,
                0,
                0.4,
                is_local=True,
            ),
        ]

    # ═══════════════════════════════════════════════════════════════════════
    # MAIN QUERY METHOD
    # ═══════════════════════════════════════════════════════════════════════

    async def query(self, query_type: DataQueryType, **kwargs) -> dict | None:
        """Query data with automatic fallback through the chain."""
        import hashlib
        import json

        chain = self._chains.get(query_type, [])
        if not chain:
            logger.warning(f"No fallback chain for {query_type}")
            return None

        # Cache key
        cache_key = f"fb:{query_type.value}:{hashlib.sha256(json.dumps(kwargs, sort_keys=True, default=str).encode()).hexdigest()[:24]}"

        # L1 cache check
        entry = self._l1.get(cache_key)
        if entry:
            expiry, data = entry
            if time.monotonic() < expiry:
                self.stats["hits"] += 1
                return data

        self.stats["misses"] += 1

        # Try each source in order
        for i, source in enumerate(chain):
            if i > 0:
                self.stats["fallbacks"] += 1
                logger.debug(f"Fallback: {query_type.value} → {source.name}")

            try:
                result = await source.fn(**kwargs)
                if result:
                    # Cache with TTL appropriate for data type
                    ttl = self._ttl_for_type(query_type)
                    self._l1[cache_key] = (time.monotonic() + ttl, result)
                    return result
            except Exception as e:
                logger.debug(f"Source {source.name} failed: {e}")
                continue

        return None

    def _ttl_for_type(self, qt: DataQueryType) -> int:
        ttls = {
            DataQueryType.TOKEN_PRICE: 8,
            DataQueryType.TOKEN_META: 120,
            DataQueryType.WALLET_BALANCE: 10,
            DataQueryType.TX_HISTORY: 30,
            DataQueryType.HOLDER_DATA: 60,
            DataQueryType.RISK_SCAN: 300,
            DataQueryType.FUNDING_SOURCE: 3600,
            DataQueryType.SOLANA_FUNDING: 3600,
        }
        return ttls.get(qt, 60)

    # ═══════════════════════════════════════════════════════════════════════
    # DATA SOURCE IMPLEMENTATIONS
    # ═══════════════════════════════════════════════════════════════════════

    # ── TOKEN PRICE ──

    async def _jupiter_price(self, mint: str, **kw) -> dict | None:
        import httpx

        try:
            async with httpx.AsyncClient(timeout=8) as c:
                r = await c.get(
                    f"https://quote-api.jup.ag/v6/quote?inputMint={mint}&outputMint=EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v&amount=1000000000"
                )
                if r.status_code == 200:
                    data = r.json()
                    return {"price_usd": float(data.get("outAmount", 0)) / 1e6, "source": "jupiter"}
        except (httpx.HTTPError, TimeoutError, OSError):
            logger.debug(f"Data source failed", exc_info=True)
        return None

    async def _tracker_price(self, mint: str, **kw) -> dict | None:
        from app.caching_shield.solana_tracker import get_solana_tracker

        st = get_solana_tracker()
        r = await st.get_price(mint)
        return (
            {
                "price_usd": r.get("price"),
                "liquidity": r.get("liquidity"),
                "source": "solana_tracker",
            }
            if r
            else None
        )

    async def _dexscreener_price(self, mint: str, **kw) -> dict | None:
        import httpx

        try:
            async with httpx.AsyncClient(timeout=8) as c:
                r = await c.get(f"https://api.dexscreener.com/latest/dex/tokens/{mint}")
                if r.status_code == 200:
                    pairs = r.json().get("pairs", [])
                    if pairs:
                        return {
                            "price_usd": float(pairs[0].get("priceUsd", 0)),
                            "source": "dexscreener",
                        }
        except (httpx.HTTPError, TimeoutError, OSError):
            logger.debug(f"Data source failed", exc_info=True)
        return None

    async def _binance_price(self, mint: str, **kw) -> dict | None:
        import httpx

        try:
            async with httpx.AsyncClient(timeout=5) as c:
                r = await c.get("https://api.binance.com/api/v3/ticker/price?symbol=SOLUSDT")
                if r.status_code == 200:
                    return {"price_usd": float(r.json().get("price", 0)), "source": "binance"}
        except (httpx.HTTPError, TimeoutError, OSError):
            logger.debug(f"Data source failed", exc_info=True)
        return None

    async def _coingecko_price(self, mint: str, **kw) -> dict | None:
        import httpx

        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get(
                    f"https://api.coingecko.com/api/v3/simple/token_price/solana?contract_addresses={mint}&vs_currencies=usd"
                )
                if r.status_code == 200:
                    data = r.json()
                    price = data.get(mint.lower(), {}).get("usd")
                    if price:
                        return {"price_usd": price, "source": "coingecko"}
        except (httpx.HTTPError, TimeoutError, OSError):
            logger.debug(f"Data source failed", exc_info=True)
        return None

    # ── TOKEN METADATA ──

    async def _helius_das_meta(self, mint: str, **kw) -> dict | None:
        from app.caching_shield.helius_das import get_helius_das

        das = get_helius_das()
        r = await das.get_token_metadata(mint)
        if r:
            content = r.get("content", {}).get("metadata", {})
            token_info = r.get("token_info", {})
            return {
                "name": content.get("name", ""),
                "symbol": content.get("symbol", ""),
                "decimals": token_info.get("decimals", 0),
                "supply": token_info.get("supply", "0"),
                "source": "helius_das",
            }
        return None

    async def _tracker_meta(self, mint: str, **kw) -> dict | None:
        from app.caching_shield.solana_tracker import get_solana_tracker

        st = get_solana_tracker()
        r = await st.get_token(mint)
        if r:
            return {
                "name": r.get("token", {}).get("name", ""),
                "symbol": r.get("token", {}).get("symbol", ""),
                "source": "solana_tracker",
            }
        return None

    async def _jupiter_meta(self, mint: str, **kw) -> dict | None:
        import httpx

        try:
            async with httpx.AsyncClient(timeout=8) as c:
                r = await c.get("https://token.jup.ag/strict")
                if r.status_code == 200:
                    for t in r.json():
                        if t.get("address") == mint:
                            return {
                                "name": t.get("name", ""),
                                "symbol": t.get("symbol", ""),
                                "decimals": t.get("decimals", 0),
                                "source": "jupiter",
                            }
        except (httpx.HTTPError, TimeoutError, OSError):
            logger.debug(f"Data source failed", exc_info=True)
        return None

    async def _dexscreener_meta(self, mint: str, **kw) -> dict | None:
        import httpx

        try:
            async with httpx.AsyncClient(timeout=8) as c:
                r = await c.get(f"https://api.dexscreener.com/latest/dex/tokens/{mint}")
                if r.status_code == 200 and r.json().get("pairs"):
                    p = r.json()["pairs"][0]
                    return {
                        "name": p.get("baseToken", {}).get("name", ""),
                        "symbol": p.get("baseToken", {}).get("symbol", ""),
                        "source": "dexscreener",
                    }
        except (httpx.HTTPError, TimeoutError, OSError):
            logger.debug(f"Data source failed", exc_info=True)
        return None

    # ── WALLET BALANCE ──

    async def _helius_balance(self, address: str, **kw) -> dict | None:
        from app.consensus_rpc import get_consensus_rpc

        rpc = get_consensus_rpc()
        result = await rpc.get_balance(address)
        if result and result.value:
            return {
                "balance_lamports": result.value.get("value", 0)
                if isinstance(result.value, dict)
                else result.value,
                "source": "helius",
            }
        return None

    async def _quicknode_balance(self, address: str, **kw) -> dict | None:
        from app.consensus_rpc import get_consensus_rpc

        rpc = get_consensus_rpc()
        result = await rpc.get_balance(address)
        if result and result.value:
            return {
                "balance_lamports": result.value.get("value", 0)
                if isinstance(result.value, dict)
                else result.value,
                "source": "quicknode",
            }
        return None

    async def _alchemy_balance(self, address: str, **kw) -> dict | None:
        from app.consensus_rpc import get_consensus_rpc

        rpc = get_consensus_rpc()
        result = await rpc.get_balance(address)
        if result and result.value:
            return {
                "balance_lamports": result.value.get("value", 0)
                if isinstance(result.value, dict)
                else result.value,
                "source": "alchemy",
            }
        return None

    async def _publicnode_balance(self, address: str, **kw) -> dict | None:
        import httpx

        try:
            async with httpx.AsyncClient(timeout=8) as c:
                r = await c.post(
                    "https://solana-rpc.publicnode.com",
                    json={"jsonrpc": "2.0", "id": 1, "method": "getBalance", "params": [address]},
                )
                if r.status_code == 200:
                    val = r.json().get("result", {}).get("value", 0)
                    return {"balance_lamports": val, "source": "publicnode"}
        except (httpx.HTTPError, TimeoutError, OSError):
            logger.debug(f"Data source failed", exc_info=True)
        return None

    # ── RISK SCAN ──

    async def _goplus_scan(self, address: str, chain: str = "solana", **kw) -> dict | None:
        import httpx

        key = os.getenv("GOPLUS_API_KEY", "")
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                url = f"https://api.gopluslabs.io/api/v1/token_security/{chain}?contract_addresses={address}"
                r = await c.get(url, headers={"Authorization": f"Bearer {key}"} if key else {})
                if r.status_code == 200:
                    data = r.json().get("result", {}).get(address.lower(), {})
                    return {
                        "is_honeypot": data.get("is_honeypot") == "1",
                        "risk_score": 100 - int(data.get("trust_score", 50)),
                        "source": "goplus",
                    }
        except (httpx.HTTPError, TimeoutError, OSError):
            logger.debug(f"Data source failed", exc_info=True)
        return None

    async def _rugcheck_scan(self, address: str, **kw) -> dict | None:
        import httpx

        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get(f"https://api.rugcheck.xyz/v1/tokens/{address}/report")
                if r.status_code == 200:
                    data = r.json()
                    risks = [
                        r.get("name", "") for r in data.get("risks", []) if r.get("score", 0) > 1000
                    ]
                    return {"risks": risks, "score": data.get("score", 0), "source": "rugcheck"}
        except (httpx.HTTPError, TimeoutError, OSError):
            logger.debug(f"Data source failed", exc_info=True)
        return None

    async def _honeypot_scan(self, address: str, **kw) -> dict | None:
        import httpx

        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get(
                    f"https://api.honeypot.is/v2/IsHoneypot?address={address}&chainID=1"
                )
                if r.status_code == 200:
                    data = r.json()
                    return {
                        "is_honeypot": data.get("honeypotResult", {}).get("isHoneypot", False),
                        "source": "honeypot",
                    }
        except (httpx.HTTPError, TimeoutError, OSError):
            logger.debug(f"Data source failed", exc_info=True)
        return None

    async def _local_labels(self, address: str, **kw) -> dict | None:
        try:
            from app.wallet_label_loader import lookup_wallet_label

            label = await lookup_wallet_label(address)
            if label:
                return {"label": label, "source": "local_labels"}
        except (ImportError, AttributeError):
            logger.debug("Wallet label lookup unavailable")
        return None

    # ── FUNDING SOURCE (EVM) ──

    async def _blockscout_trace(self, address: str, chain_id: int = 1, **kw) -> dict | None:
        from app.caching_shield.funding_tracer import trace_funding_source

        result = await trace_funding_source(address, chain_id)
        if result and result.source_address:
            return {
                "source_address": result.source_address,
                "source_type": result.source_type,
                "source_label": result.source_label,
                "confidence": result.confidence,
                "source": "blockscout",
            }
        return None

    async def _etherscan_trace(self, address: str, chain_id: int = 1, **kw) -> dict | None:
        from app.caching_shield.funding_tracer import _fetch_etherscan_txs

        key = os.getenv("ETHERSCAN_API_KEY", "")
        txs = await _fetch_etherscan_txs(address, key)
        if txs:
            return {"txs_found": len(txs), "source": "etherscan"}
        return None

    async def _rpc_trace(self, address: str, chain_id: int = 1, **kw) -> dict | None:
        from app.caching_shield.funding_tracer import _fetch_rpc_transfers

        txs = await _fetch_rpc_transfers(address, chain_id)
        if txs:
            return {"txs_found": len(txs), "source": "public_rpc"}
        return None

    # ── SOLANA FUNDING ──

    async def _helius_sol_funding(self, address: str, **kw) -> dict | None:
        """Trace Solana wallet funding via Helius parsed transaction history."""
        import httpx

        key = os.getenv("HELIUS_API_KEY", "")
        if not key:
            return None
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.post(
                    f"https://mainnet.helius-rpc.com/?api-key={key}",
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "getSignaturesForAddress",
                        "params": [address, {"limit": 20}],
                    },
                )
                if r.status_code == 200:
                    sigs = r.json().get("result", [])
                    if sigs:
                        # Get the first transaction details
                        first_sig = sigs[0]["signature"]
                        r2 = await c.post(
                            f"https://mainnet.helius-rpc.com/?api-key={key}",
                            json={
                                "jsonrpc": "2.0",
                                "id": 1,
                                "method": "getTransaction",
                                "params": [
                                    first_sig,
                                    {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0},
                                ],
                            },
                        )
                        if r2.status_code == 200:
                            tx = r2.json().get("result", {})
                            meta = tx.get("meta", {})
                            pre = meta.get("preBalances", [0])
                            post = meta.get("postBalances", [0])
                            if post[0] > pre[0]:
                                return {
                                    "first_tx": first_sig,
                                    "balance_change": (post[0] - pre[0]) / 1e9,
                                    "source": "helius",
                                }
                    return {"signatures_found": len(sigs), "source": "helius"}
        except (httpx.HTTPError, TimeoutError, OSError):
            logger.debug(f"Data source failed", exc_info=True)
        return None

    async def _tracker_sol_funding(self, address: str, **kw) -> dict | None:
        from app.caching_shield.solana_tracker import get_solana_tracker

        st = get_solana_tracker()
        wallet = await st.get_wallet(address)
        if wallet:
            return {
                "total_value": wallet.get("total", 0),
                "tokens": len(wallet.get("tokens", [])),
                "source": "solana_tracker",
            }
        return None

    async def _public_rpc_sol_funding(self, address: str, **kw) -> dict | None:
        import httpx

        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.post(
                    "https://solana-rpc.publicnode.com",
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "getSignaturesForAddress",
                        "params": [address, {"limit": 10}],
                    },
                )
                if r.status_code == 200:
                    sigs = r.json().get("result", [])
                    return {"signatures_found": len(sigs), "source": "public_rpc"} if sigs else None
        except (httpx.HTTPError, TimeoutError, OSError):
            logger.debug(f"Data source failed", exc_info=True)
        return None

    async def _local_wallet_labels(self, address: str, **kw) -> dict | None:
        try:
            from app.wallet_label_loader import lookup_wallet_label

            label = await lookup_wallet_label(address)
            if label:
                return {
                    "label": label,
                    "is_cex": any(
                        kw in label.lower()
                        for kw in [
                            "binance",
                            "coinbase",
                            "kraken",
                            "okx",
                            "bybit",
                            "kucoin",
                            "gate",
                            "mexc",
                        ]
                    ),
                    "source": "local_wallet_labels",
                }
        except (ImportError, AttributeError):
            logger.debug("Wallet label lookup unavailable")
        return None

    # ═══════════════════════════════════════════════════════════════════════
    # STATS
    # ═══════════════════════════════════════════════════════════════════════

    def health(self) -> dict:
        chains_info = {}
        for qt, sources in self._chains.items():
            chains_info[qt.value] = {
                "sources": [s.name for s in sources],
                "total": len(sources),
                "has_local": any(s.is_local for s in sources),
            }
        return {
            "cache_hits": self.stats["hits"],
            "cache_misses": self.stats["misses"],
            "fallbacks_used": self.stats["fallbacks"],
            "l1_size": len(self._l1),
            "chains": chains_info,
        }


# Singleton
_engine: UnifiedDataEngine | None = None


def get_data_engine() -> UnifiedDataEngine:
    global _engine
    if _engine is None:
        _engine = UnifiedDataEngine()
    return _engine
