"""
Bitquery DataBus Provider - Blockchain Intelligence via GraphQL
=================================================================

Bitquery provides deep blockchain data across 40+ chains via GraphQL.
This provider integrates with DataBus for intelligent caching and
rate-limited access.

Status: API key valid, account needs active billing period.
When billing is activated (free tier or paid), all queries work.

Data types supported:
  - token_price: DEX trade prices across chains
  - holder_data: Token holder distribution and whale tracking
  - transaction_trace: Full transaction traces and fund flows
  - dex_volume: DEX trading volume and liquidity
  - smart_contract: Contract creation, calls, and events
  - address_balance: Address balances across chains
  - cross_chain_bridge: Bridge transaction tracking

Cache strategy:
  - Historical data: 24h TTL (immutable)
  - Price/volume: 5min TTL (volatile)
  - Holder data: 1h TTL (semi-volatile)
  - Transaction traces: 24h TTL (immutable)

Rate limits:
  - Free tier: 100k credits/month, ~1 query/sec
  - Developer: $99/mo, higher rate limits
  - Business: $499/mo, highest rate limits
"""

from __future__ import annotations

import hashlib
import time

import httpx

from app.core.logging import get_logger
from app.databus.cache import CacheLayer, get_cache

logger = get_logger("databus.bitquery")

# ── Configuration ──────────────────────────────────────────────
BITQUERY_STREAMING_URL = "https://streaming.bitquery.io/graphql"
BITQUERY_IDE_URL = "https://ide.bitquery.io/graphql"
BITQUERY_API_URL = "https://graphql.bitquery.io/"

# Cache TTLs
CACHE_TTL_PRICE = 300  # 5 min - prices are volatile
CACHE_TTL_VOLUME = 300  # 5 min
CACHE_TTL_HOLDERS = 3600  # 1 hour
CACHE_TTL_TRACE = 86400  # 24 hours - historical
CACHE_TTL_BALANCE = 600  # 10 min

# Rate limits (free tier)
FREE_CREDITS_PER_MONTH = 100_000
MAX_RPS = 1.0


class BitqueryProvider:
    """
    Bitquery GraphQL API provider with intelligent caching.

    Handles:
    - GraphQL query construction for common blockchain data types
    - Rate limiting (credit-based)
    - Response caching with DataBus
    - Error handling for billing/auth issues
    """

    def __init__(self, cache: CacheLayer = None) -> None:
        self.cache = cache or get_cache()
        self._client: httpx.AsyncClient | None = None
        self._api_key: str | None = None
        self._oauth_token: str | None = None
        self._credits_used = 0
        self._monthly_reset = time.time()
        self._loaded = False

    async def _load_creds(self) -> None:
        """Load Bitquery credentials - env vars first, vault as fallback."""
        if self._loaded:
            return
        import os

        self._api_key = os.getenv("BITQUERY_API_KEY", "")
        self._oauth_token = os.getenv("BITQUERY_OAUTH_TOKEN", "")
        if self._api_key:
            self._loaded = True
            logger.info(
                f"Bitquery creds from env (key={'✓' if self._api_key else '✗'}, oauth={'✓' if self._oauth_token else '✗'})"
            )
            return
        # Fallback: vault
        try:
            import subprocess

            result = subprocess.run(
                ["python3", "/root/.secrets/vault.py", "get", "backend/bitquery_api_key"],
                capture_output=True,
                text=True,
                timeout=10,
            shell=False)
            key = result.stdout.strip()
            if key and len(key) > 10:
                self._api_key = key
            self._loaded = True
            logger.info(f"Bitquery creds from vault (key={'✓' if self._api_key else '✗'})")
        except Exception as e:
            logger.error(f"Failed to load Bitquery credentials: {e}")

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=BITQUERY_STREAMING_URL,
                timeout=30.0,
                headers={"Content-Type": "application/json"},
            )
        return self._client

    def _check_billing(self) -> bool:
        """Check if credits are available. Returns False if billing period inactive."""
        now = time.time()
        if now - self._monthly_reset > 30 * 86400:  # ~30 days
            self._credits_used = 0
            self._monthly_reset = now
        return self._credits_used < FREE_CREDITS_PER_MONTH

    async def _query(self, graphql_query: str, variables: dict | None = None) -> dict | None:
        """
        Execute a Bitquery GraphQL query with caching and rate limiting.
        Returns None on billing error (402) or auth error.
        """
        await self._load_creds()

        if not self._check_billing():
            logger.warning("Bitquery monthly credit limit reached")
            return {"error": "Monthly credit limit reached", "code": 429}

        # Generate cache key from query hash
        query_hash = hashlib.sha256(graphql_query.encode()).hexdigest()[:16]

        cached = await self._get_cached(query_hash, graphql_query)
        if cached:
            return cached

        client = await self._get_client()

        payload = {"query": graphql_query}
        if variables:
            payload["variables"] = variables

        headers = {"X-API-KEY": self._api_key}
        if self._oauth_token:
            headers["Authorization"] = f"Bearer {self._oauth_token}"

        try:
            resp = await client.post("", json=payload, headers=headers)
            self._credits_used += 1

            if resp.status_code == 402:
                logger.error("Bitquery: No active billing period. Activate at bitquery.io")
                return {
                    "error": "No active billing period",
                    "code": 402,
                    "fix": "Log into bitquery.io and activate a plan (even free tier)",
                }

            if resp.status_code == 401:
                logger.error("Bitquery: Invalid API key or OAuth token")
                return {"error": "Authentication failed", "code": 401}

            resp.raise_for_status()
            data = resp.json()

            if "errors" in data:
                logger.warning(f"Bitquery GraphQL errors: {data['errors']}")
                return {"error": "GraphQL error", "details": data["errors"]}

            # Cache successful response
            await self._cache_response(query_hash, graphql_query, data)

            return data.get("data", {})

        except httpx.HTTPStatusError as e:
            logger.error(f"Bitquery HTTP error: {e.response.status_code}")
            return {"error": f"HTTP {e.response.status_code}", "code": e.response.status_code}
        except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
            logger.error(f"Bitquery query failed: {e}")
            return {"error": str(e)}

    async def _get_cached(self, query_hash: str, query_text: str) -> dict | None:
        """Get cached response if available and not stale."""
        cache_key = f"bitquery:{query_hash}"

        # Determine TTL based on query type
        ttl = CACHE_TTL_TRACE  # default
        if any(k in query_text.lower() for k in ["price", "volume", "trade"]):
            ttl = CACHE_TTL_PRICE
        elif "holder" in query_text.lower() or "balance" in query_text.lower():
            ttl = CACHE_TTL_HOLDERS

        cached = await self.cache.get(cache_key)
        if cached:
            # Handle tuple (value, is_stale) from SWR cache
            if isinstance(cached, tuple):
                cached = cached[0]
            if cached is not None:
                # Check if cache is still fresh
                cached_time = cached.get("_cached_at", 0)
                if time.time() - cached_time < ttl:
                    return cached.get("data")

        return None

    async def _cache_response(self, query_hash: str, query_text: str, data: dict) -> None:
        """Cache successful response with metadata."""
        cache_key = f"bitquery:{query_hash}"

        ttl = CACHE_TTL_TRACE
        if any(k in query_text.lower() for k in ["price", "volume", "trade"]):
            ttl = CACHE_TTL_PRICE
        elif "holder" in query_text.lower() or "balance" in query_text.lower():
            ttl = CACHE_TTL_HOLDERS

        cache_data = {
            "data": data,
            "_cached_at": time.time(),
            "_query_hash": query_hash,
        }

        await self.cache.set(cache_key, cache_data, ttl=ttl)

    # ── Public Data Methods ──────────────────────────────────────

    async def get_token_price(self, network: str, token_address: str) -> dict | None:
        """
        Get latest DEX price for a token.

        Args:
            network: ethereum, bsc, solana, etc.
            token_address: Contract address or mint
        """
        query = f"""
        query {{
            {network}(network: {network}) {{
                dexTrades(
                    options: {{limit: 1, desc: "block.timestamp.time"}}
                    baseCurrency: {{is: "{token_address}"}}
                ) {{
                    transaction {{
                        hash
                    }}
                    tradeAmount(in: USD)
                    price
                    baseCurrency {{
                        symbol
                        name
                    }}
                    quoteCurrency {{
                        symbol
                    }}
                    block {{
                        timestamp {{
                            time(format: "%Y-%m-%d %H:%M:%S")
                        }}
                    }}
                }}
            }}
        }}
        """
        return await self._query(query)

    async def get_holder_distribution(self, network: str, token_address: str, limit: int = 100) -> dict | None:
        """
        Get top token holders and their balances.

        Args:
            network: blockchain network
            token_address: Token contract/mint
            limit: Number of holders to return
        """
        query = f"""
        query {{
            {network}(network: {network}) {{
                address(
                    address: {{is: "{token_address}"}}
                ) {{
                    balances {{
                        currency {{
                            symbol
                            tokenType
                        }}
                        value
                        history {{
                            block
                            timestamp
                            value
                        }}
                    }}
                }}
            }}
        }}
        """
        return await self._query(query)

    async def get_transaction_trace(self, network: str, tx_hash: str) -> dict | None:
        """
        Get full transaction trace including internal calls.

        Args:
            network: blockchain network
            tx_hash: Transaction hash
        """
        query = f"""
        query {{
            {network}(network: {network}) {{
                transactions(
                    txHash: {{is: "{tx_hash}"}}
                ) {{
                    hash
                    block {{
                        height
                        timestamp {{
                            time(format: "%Y-%m-%d %H:%M:%S")
                        }}
                    }}
                    sender {{
                        address
                    }}
                    receiver {{
                        address
                    }}
                    amount
                    gasValue
                    success
                    internalTransactions {{
                        sender {{
                            address
                        }}
                        receiver {{
                            address
                        }}
                        amount
                        gasValue
                        success
                    }}
                }}
            }}
        }}
        """
        return await self._query(query)

    async def get_dex_volume(
        self, network: str, pool_address: str | None = None, timeframe: str = "24h"
    ) -> dict | None:
        """
        Get DEX trading volume for a pool or network.

        Args:
            network: blockchain network
            pool_address: Optional specific pool
            timeframe: 1h, 6h, 24h, 7d
        """
        pool_filter = f'poolAddress: {{is: "{pool_address}"}}' if pool_address else ""

        query = f"""
        query {{
            {network}(network: {network}) {{
                dexTrades(
                    {pool_filter}
                    options: {{desc: "block.timestamp.time", limit: 100}}
                ) {{
                    count
                    tradeAmount(in: USD)
                    volume: tradeAmount(in: USD, calculate: sum)
                    block {{
                        timestamp {{
                            time(format: "%Y-%m-%d %H:%M:%S")
                        }}
                    }}
                }}
            }}
        }}
        """
        return await self._query(query)

    async def get_address_balance(self, network: str, address: str) -> dict | None:
        """
        Get all token balances for an address.

        Args:
            network: blockchain network
            address: Wallet address
        """
        query = f"""
        query {{
            {network}(network: {network}) {{
                address(
                    address: {{is: "{address}"}}
                ) {{
                    balances {{
                        currency {{
                            symbol
                            name
                            tokenType
                            address
                        }}
                        value
                    }}
                }}
            }}
        }}
        """
        return await self._query(query)

    async def get_cross_chain_transfers(self, address: str, networks: list[str] | None = None) -> dict | None:
        """
        Track token transfers across multiple chains for an address.

        Args:
            address: Wallet address
            networks: List of networks to check (default: ethereum, bsc, solana)
        """
        networks = networks or ["ethereum", "bsc", "solana"]

        # Build multi-network query
        queries = []
        for net in networks:
            queries.append(f"""
                {net}: {net}(network: {net}) {{
                    transfers(
                        sender: {{is: "{address}"}}
                        options: {{limit: 10, desc: "block.timestamp.time"}}
                    ) {{
                        transaction {{
                            hash
                        }}
                        amount
                        currency {{
                            symbol
                            name
                        }}
                        receiver {{
                            address
                        }}
                        block {{
                            timestamp {{
                                time(format: "%Y-%m-%d %H:%M:%S")
                            }}
                        }}
                    }}
                }}
            """)

        query = f"query {{ {' '.join(queries)} }}"
        return await self._query(query)

    async def get_smart_contract_events(
        self, network: str, contract: str, event_signature: str | None = None, limit: int = 50
    ) -> dict | None:
        """
        Get smart contract events/logs.

        Args:
            network: blockchain network
            contract: Contract address
            event_signature: Optional event signature hash
            limit: Number of events
        """
        event_filter = f'smartContractEvent: {{is: "{event_signature}"}}' if event_signature else ""

        query = f"""
        query {{
            {network}(network: {network}) {{
                smartContractEvents(
                    smartContractAddress: {{is: "{contract}"}}
                    {event_filter}
                    options: {{limit: {limit}, desc: "block.timestamp.time"}}
                ) {{
                    arguments {{
                        name
                        value
                        type
                    }}
                    smartContract {{
                        contractType
                        currency {{
                            symbol
                            name
                        }}
                    }}
                    block {{
                        timestamp {{
                            time(format: "%Y-%m-%d %H:%M:%S")
                        }}
                    }}
                }}
            }}
        }}
        """
        return await self._query(query)

    # ── Health & Status ─────────────────────────────────────────

    async def health(self) -> dict:
        """Provider health check."""
        await self._load_creds()

        # Try a minimal query to check status
        test_query = """
        query {
            ethereum(network: ethereum) {
                blocks(options: {limit: 1}) {
                    height
                }
            }
        }
        """

        result = await self._query(test_query)

        if result and "error" in result:
            error_code = result.get("code", 0)
            if error_code == 402:
                return {
                    "status": "billing_required",
                    "api_key_valid": True,
                    "billing_active": False,
                    "credits_used": self._credits_used,
                    "credits_remaining": FREE_CREDITS_PER_MONTH - self._credits_used,
                    "fix": "Activate billing at bitquery.io (free tier available)",
                    "message": "Account needs active billing period",
                }
            elif error_code == 401:
                return {
                    "status": "auth_error",
                    "api_key_valid": False,
                    "message": "Invalid API key or OAuth token",
                }
            else:
                return {"status": "error", "error": result.get("error"), "code": error_code}

        return {
            "status": "healthy",
            "api_key_valid": True,
            "billing_active": True,
            "credits_used": self._credits_used,
            "credits_remaining": FREE_CREDITS_PER_MONTH - self._credits_used,
            "networks_supported": 40,
            "data_types": [
                "token_price",
                "holder_data",
                "transaction_trace",
                "dex_volume",
                "smart_contract_events",
                "address_balance",
                "cross_chain_transfers",
            ],
        }
