"""
Price Consensus Engine - Multi-Source Aggregation with MAD Outlier Detection.

Queries 7+ price sources in parallel, applies Median Absolute Deviation (MAD)
outlier filtering (z-score > 3 = outlier), and computes a weighted mean price
using source reliability scores.

Sources: DexScreener, GeckoTerminal, Jupiter (Solana), DIA, CoinGecko,
         CryptoCompare, Coinpaprika - all free tier, no paid keys required.

Depends on: httpx, numpy (for median/percentile), optional env keys.
"""

import asyncio
import os
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
import numpy as np

from app.core.logging import get_logger

logger = get_logger(__name__)

# ── Source Reliability Scores (0.0-1.0, higher = more trusted) ─────────────

# These are initial weights based on historical accuracy, API stability,
# and data freshness. They can be adjusted via _source_stats over time.
DEFAULT_SOURCE_WEIGHTS = {
    "dexscreener": 0.90,  # Direct DEX data, excellent for on-chain tokens
    "geckoterminal": 0.92,  # CoinGecko's DEX aggregator, very reliable
    "jupiter": 0.88,  # Solana's primary aggregator, excellent for Solana
    "dia": 0.85,  # Oracle-grade data, transparent methodology
    "coingecko": 0.88,  # CEX + DEX aggregation, broad coverage
    "cryptocompare": 0.82,  # Institutional-grade, slower updates on microcaps
    "coinpaprika": 0.78,  # Good coverage, slightly less reliable on low-cap
    "birdeye": 0.86,  # Good Solana coverage, needs API key
}

# ── Data Classes ────────────────────────────────────────────────────────────


@dataclass
class PriceSource:
    """A single price data provider."""

    name: str
    weight: float  # Reliability score 0-1
    fetcher: Any = None  # Async callable: (address, chain) → Optional[float]
    last_price: float = 0.0
    last_latency: float = 0.0
    error_count: int = 0


@dataclass
class PriceConsensus:
    """Result of multi-source price consensus."""

    price: float | None = None
    confidence: float = 0.0  # 0-100%
    sources_used: list[str] = field(default_factory=list)
    outlier_sources: list[str] = field(default_factory=list)
    failed_sources: list[str] = field(default_factory=list)
    individual_prices: dict[str, float] = field(default_factory=dict)
    median: float | None = None
    mad: float | None = None
    std_dev: float | None = None
    spread_pct: float | None = None  # (max-min)/median * 100

    @property
    def is_reliable(self) -> bool:
        return self.confidence >= 60.0 and self.price is not None


# ── Price Consensus Engine ─────────────────────────────────────────────────


class PriceConsensusEngine:
    """Multi-source price aggregation with MAD-based outlier rejection.

    Fetches from all configured sources in parallel, removes statistical
    outliers (z-score > 3 using Median Absolute Deviation), and computes
    a weighted mean of the remaining prices. Falls back gracefully if
    fewer than 2 sources respond.
    """

    # Timeout per source fetch
    PER_SOURCE_TIMEOUT = 10.0

    # If a source fails this many times consecutively, lower its effective weight
    MAX_CONSECUTIVE_ERRORS = 5

    def __init__(self):
        self._sources: dict[str, PriceSource] = {}
        self._lock = asyncio.Lock()
        self._setup_sources()

    def _setup_sources(self):
        """Register all price sources with their fetcher callables."""
        sources = [
            ("dexscreener", self._fetch_dexscreener, DEFAULT_SOURCE_WEIGHTS["dexscreener"]),
            ("geckoterminal", self._fetch_geckoterminal, DEFAULT_SOURCE_WEIGHTS["geckoterminal"]),
            ("jupiter", self._fetch_jupiter, DEFAULT_SOURCE_WEIGHTS["jupiter"]),
            ("dia", self._fetch_dia, DEFAULT_SOURCE_WEIGHTS["dia"]),
            ("coingecko", self._fetch_coingecko, DEFAULT_SOURCE_WEIGHTS["coingecko"]),
            ("cryptocompare", self._fetch_cryptocompare, DEFAULT_SOURCE_WEIGHTS["cryptocompare"]),
            ("coinpaprika", self._fetch_coinpaprika, DEFAULT_SOURCE_WEIGHTS["coinpaprika"]),
        ]

        # Birdeye if key is available
        birdeye_key = os.getenv("BIRDEYE_API_KEY", "")
        if birdeye_key and birdeye_key != "your_birdeye_key_here":
            sources.append(("birdeye", self._fetch_birdeye, DEFAULT_SOURCE_WEIGHTS["birdeye"]))

        for name, fetcher, weight in sources:
            self._sources[name] = PriceSource(name=name, weight=weight, fetcher=fetcher)

        logger.info(
            f"PriceConsensusEngine: {len(self._sources)} sources registered: {list(self._sources.keys())}"
        )

    # ── Source Fetchers ──────────────────────────────────────────────────

    async def _fetch_dexscreener(self, address: str, chain: str) -> float | None:
        """DexScreener free API - no key required."""
        try:
            async with httpx.AsyncClient(timeout=self.PER_SOURCE_TIMEOUT) as client:
                r = await client.get(
                    f"https://api.dexscreener.com/latest/dex/tokens/{address}",
                    headers={"Accept": "application/json"},
                )
                if r.status_code == 200:
                    data = r.json()
                    pairs = data.get("pairs", [])
                    if pairs:
                        # Find the pair with highest liquidity
                        best = max(
                            pairs, key=lambda p: float(p.get("liquidity", {}).get("usd", 0) or 0)
                        )
                        price = best.get("priceUsd")
                        if price:
                            return float(price)
                return None
        except Exception as e:
            logger.debug(f"DexScreener fetch error: {e}")
            return None

    async def _fetch_geckoterminal(self, address: str, chain: str) -> float | None:
        """GeckoTerminal free API - no key required."""
        network = self._chain_to_gecko_network(chain)
        try:
            async with httpx.AsyncClient(timeout=self.PER_SOURCE_TIMEOUT) as client:
                r = await client.get(
                    f"https://api.geckoterminal.com/api/v2/networks/{network}/tokens/{address}",
                    headers={"Accept": "application/json"},
                )
                if r.status_code == 200:
                    data = r.json()
                    token_data = data.get("data", {})
                    attrs = token_data.get("attributes", {})
                    price = attrs.get("price_usd")
                    if price:
                        return float(price)
                return None
        except Exception as e:
            logger.debug(f"GeckoTerminal fetch error: {e}")
            return None

    async def _fetch_jupiter(self, address: str, chain: str) -> float | None:
        """Jupiter price API - free, Solana only."""
        if chain.lower() not in ("solana", "sol"):
            return None
        try:
            async with httpx.AsyncClient(timeout=self.PER_SOURCE_TIMEOUT) as client:
                r = await client.get(
                    f"https://price.jup.ag/v6/price?ids={address}",
                    headers={"Accept": "application/json"},
                )
                if r.status_code == 200:
                    data = r.json()
                    token_data = data.get("data", {}).get(address)
                    if token_data:
                        price = token_data.get("price")
                        if price:
                            return float(price)
                return None
        except Exception as e:
            logger.debug(f"Jupiter fetch error: {e}")
            return None

    async def _fetch_dia(self, address: str, chain: str) -> float | None:
        """DIA oracle price feed - free, no key."""
        dia_chain = self._chain_to_dia_chain(chain)
        if not dia_chain:
            return None
        try:
            async with httpx.AsyncClient(timeout=self.PER_SOURCE_TIMEOUT) as client:
                r = await client.get(
                    f"https://api.diadata.org/v1/assetQuotation/{dia_chain}/{address}",
                    headers={"Accept": "application/json"},
                )
                if r.status_code == 200:
                    data = r.json()
                    price = data.get("Price")
                    if price:
                        return float(price)
                return None
        except Exception as e:
            logger.debug(f"DIA fetch error: {e}")
            return None

    async def _fetch_coingecko(self, address: str, chain: str) -> float | None:
        """CoinGecko token price by contract - free tier."""
        cg_chain = self._chain_to_coingecko_platform(chain)
        if not cg_chain:
            return None
        api_key = os.getenv("COINGECKO_API_KEY", "")
        headers = {"Accept": "application/json"}
        if api_key:
            headers["x-cg-demo-api-key"] = api_key
        try:
            async with httpx.AsyncClient(timeout=self.PER_SOURCE_TIMEOUT) as client:
                r = await client.get(
                    f"https://api.coingecko.com/api/v3/simple/token_price/{cg_chain}",
                    params={
                        "contract_addresses": address,
                        "vs_currencies": "usd",
                    },
                    headers=headers,
                )
                if r.status_code == 200:
                    data = r.json()
                    price = data.get(address.lower(), {}).get("usd")
                    if price:
                        return float(price)
                return None
        except Exception as e:
            logger.debug(f"CoinGecko fetch error: {e}")
            return None

    async def _fetch_cryptocompare(self, address: str, chain: str) -> float | None:
        """CryptoCompare price API - free tier."""
        api_key = os.getenv("CRYPTOCOMPARE_API_KEY", "")
        headers = {"Accept": "application/json"}
        if api_key:
            headers["authorization"] = f"Apikey {api_key}"
        try:
            async with httpx.AsyncClient(timeout=self.PER_SOURCE_TIMEOUT) as client:
                r = await client.get(
                    "https://min-api.cryptocompare.com/data/price",
                    params={
                        "fsym": address,
                        "tsyms": "USD",
                    },
                    headers=headers,
                )
                if r.status_code == 200:
                    data = r.json()
                    price = data.get("USD")
                    if price:
                        return float(price)
                return None
        except Exception as e:
            logger.debug(f"CryptoCompare fetch error: {e}")
            return None

    async def _fetch_coinpaprika(self, address: str, chain: str) -> float | None:
        """Coinpaprika free API - no key required."""
        try:
            async with httpx.AsyncClient(timeout=self.PER_SOURCE_TIMEOUT) as client:
                # Try by contract address lookup
                r = await client.get(
                    f"https://api.coinpaprika.com/v1/contracts/{chain}/{address}",
                    headers={"Accept": "application/json"},
                )
                if r.status_code == 200:
                    data = r.json()
                    coin_id = data.get("id")
                    if coin_id:
                        # Get ticker for this coin
                        r2 = await client.get(
                            f"https://api.coinpaprika.com/v1/tickers/{coin_id}",
                            headers={"Accept": "application/json"},
                        )
                        if r2.status_code == 200:
                            ticker = r2.json()
                            price = ticker.get("quotes", {}).get("USD", {}).get("price")
                            if price:
                                return float(price)
                return None
        except Exception as e:
            logger.debug(f"Coinpaprika fetch error: {e}")
            return None

    async def _fetch_birdeye(self, address: str, chain: str) -> float | None:
        """Birdeye price API - requires BIRDEYE_API_KEY."""
        api_key = os.getenv("BIRDEYE_API_KEY", "")
        if not api_key:
            return None
        try:
            async with httpx.AsyncClient(timeout=self.PER_SOURCE_TIMEOUT) as client:
                r = await client.get(
                    "https://public-api.birdeye.so/defi/price",
                    params={"address": address},
                    headers={
                        "X-API-KEY": api_key,
                        "accept": "application/json",
                    },
                )
                if r.status_code == 200:
                    data = r.json()
                    price = data.get("data", {}).get("value")
                    if price:
                        return float(price)
                return None
        except Exception as e:
            logger.debug(f"Birdeye fetch error: {e}")
            return None

    # ── Chain Name Normalization ──────────────────────────────────────────

    @staticmethod
    def _chain_to_gecko_network(chain: str) -> str:
        mapping = {
            "solana": "solana",
            "sol": "solana",
            "ethereum": "eth",
            "eth": "eth",
            "1": "eth",
            "base": "base",
            "8453": "base",
            "bsc": "bsc",
            "56": "bsc",
            "bnb": "bsc",
            "arbitrum": "arbitrum",
            "42161": "arbitrum",
            "polygon": "polygon_pos",
            "137": "polygon_pos",
            "matic": "polygon_pos",
            "optimism": "optimism",
            "10": "optimism",
            "avalanche": "avax",
            "43114": "avax",
            "fantom": "fantom",
            "250": "fantom",
        }
        return mapping.get(chain.lower(), chain.lower())

    @staticmethod
    def _chain_to_dia_chain(chain: str) -> str | None:
        mapping = {
            "solana": "Solana",
            "sol": "Solana",
            "ethereum": "Ethereum",
            "eth": "Ethereum",
            "1": "Ethereum",
            "base": "Base",
            "8453": "Base",
            "bsc": "BSC",
            "56": "BSC",
            "bnb": "BSC",
            "arbitrum": "Arbitrum",
            "42161": "Arbitrum",
            "polygon": "Polygon",
            "137": "Polygon",
            "optimism": "Optimism",
            "10": "Optimism",
        }
        return mapping.get(chain.lower())

    @staticmethod
    def _chain_to_coingecko_platform(chain: str) -> str | None:
        mapping = {
            "solana": "solana",
            "sol": "solana",
            "ethereum": "ethereum",
            "eth": "ethereum",
            "1": "ethereum",
            "base": "base",
            "8453": "base",
            "bsc": "binance-smart-chain",
            "56": "binance-smart-chain",
            "bnb": "binance-smart-chain",
            "arbitrum": "arbitrum-one",
            "42161": "arbitrum-one",
            "polygon": "polygon-pos",
            "137": "polygon-pos",
            "matic": "polygon-pos",
            "optimism": "optimistic-ethereum",
            "10": "optimistic-ethereum",
            "avalanche": "avalanche",
            "43114": "avalanche",
            "fantom": "fantom",
            "250": "fantom",
        }
        return mapping.get(chain.lower())

    # ── Core Consensus Logic ──────────────────────────────────────────────

    async def get_consensus_price(
        self,
        token_address: str,
        chain: str = "solana",
    ) -> PriceConsensus:
        """Fetch prices from all sources and compute consensus.

        Args:
            token_address: Token contract address / mint
            chain: Blockchain identifier (solana, ethereum, base, etc.)

        Returns:
            PriceConsensus with consensus price, confidence, and breakdown.
        """
        if not self._sources:
            return PriceConsensus(
                price=None,
                confidence=0.0,
                failed_sources=["no_sources_configured"],
            )

        # Fire all source fetchers in parallel
        tasks = []
        source_names = []
        for name, source in self._sources.items():
            tasks.append(source.fetcher(token_address, chain))
            source_names.append(name)

        start = time.monotonic()
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Collect successful prices and track failures
        prices: dict[str, float] = {}
        failed: list[str] = []

        for name, result in zip(source_names, results, strict=False):
            if isinstance(result, Exception):
                logger.debug(f"Source {name} exception: {result}")
                failed.append(name)
                async with self._lock:
                    if name in self._sources:
                        self._sources[name].error_count += 1
            elif result is not None and isinstance(result, (int, float)):
                if result > 0:
                    prices[name] = float(result)
                    latency = time.monotonic() - start
                    async with self._lock:
                        if name in self._sources:
                            self._sources[name].last_price = float(result)
                            self._sources[name].last_latency = latency
                            self._sources[name].error_count = 0
            else:
                failed.append(name)

        # If no sources returned a price, return null consensus
        if not prices:
            logger.warning(f"No price sources responded for {token_address} on {chain}")
            return PriceConsensus(
                price=None,
                confidence=0.0,
                failed_sources=failed,
            )

        price_values = list(prices.values())
        price_names = list(prices.keys())

        # Single source: return it but with low confidence
        if len(price_values) == 1:
            return PriceConsensus(
                price=price_values[0],
                confidence=30.0,
                sources_used=price_names,
                failed_sources=failed,
                individual_prices=prices,
                median=price_values[0],
            )

        # ── MAD-based Outlier Detection ─────────────────────────────────

        arr = np.array(price_values)
        median = float(np.median(arr))
        mad = float(np.median(np.abs(arr - median)))

        # If MAD is zero (all prices identical), no outliers
        if mad == 0:
            weighted_avg = self._weighted_mean(prices)
            return PriceConsensus(
                price=weighted_avg,
                confidence=95.0 if len(price_values) >= 3 else 70.0,
                sources_used=price_names,
                outlier_sources=[],
                failed_sources=failed,
                individual_prices=prices,
                median=median,
                mad=0.0,
                std_dev=0.0,
                spread_pct=0.0,
            )

        # Compute modified z-scores using MAD
        # z_i = 0.6745 * (x_i - median) / MAD
        z_scores = 0.6745 * (arr - median) / mad

        # Outlier threshold: |z| > 3 (very conservative - classic threshold)
        inliers_mask = np.abs(z_scores) <= 3.0
        outliers_mask = ~inliers_mask

        inlier_prices = {
            name: price
            for name, price, is_inlier in zip(price_names, price_values, inliers_mask, strict=False)
            if is_inlier
        }
        outlier_names = [
            name
            for name, price, is_outlier in zip(
                price_names, price_values, outliers_mask, strict=False
            )
            if is_outlier
        ]

        # If all prices are outliers, fall back to all with low confidence
        if not inlier_prices:
            logger.warning(
                f"All prices flagged as outliers for {token_address} - using all with low confidence"
            )
            weighted_avg = self._weighted_mean(prices)
            return PriceConsensus(
                price=weighted_avg,
                confidence=10.0,
                sources_used=price_names,
                outlier_sources=[],
                failed_sources=failed,
                individual_prices=prices,
                median=median,
                mad=float(mad),
                std_dev=float(np.std(arr)),
                spread_pct=self._spread_pct(price_values),
            )

        # Compute weighted mean of inliers
        consensus_price = self._weighted_mean(inlier_prices)

        # Confidence calculation
        total_sources = len(self._sources)
        inlier_count = len(inlier_prices)
        responder_count = len(price_values)

        # Base confidence from inlier agreement ratio
        if inlier_count >= 3:
            agreement_ratio = inlier_count / responder_count
            confidence = agreement_ratio * 85.0 + 10.0  # 70-95 range
        elif inlier_count == 2:
            confidence = 55.0
        else:
            confidence = 35.0

        # Penalize if we had many failures
        failure_penalty = (len(failed) / max(total_sources, 1)) * 20.0
        confidence = max(5.0, confidence - failure_penalty)

        # Bonus for low spread among inliers
        inlier_values = list(inlier_prices.values())
        if len(inlier_values) >= 2:
            spread = self._spread_pct(inlier_values)
            if spread is not None and spread < 2.0:
                confidence = min(100.0, confidence + 10.0)

        return PriceConsensus(
            price=round(consensus_price, 12),
            confidence=round(confidence, 1),
            sources_used=list(inlier_prices.keys()),
            outlier_sources=outlier_names,
            failed_sources=failed,
            individual_prices=prices,
            median=round(median, 12),
            mad=round(float(mad), 12) if mad else None,
            std_dev=round(float(np.std(arr)), 12),
            spread_pct=self._spread_pct(price_values),
        )

    # ── Helpers ───────────────────────────────────────────────────────────

    def _weighted_mean(self, prices: dict[str, float]) -> float:
        """Weighted mean using source reliability weights, adjusted by error history."""
        if not prices:
            return 0.0
        total_weight = 0.0
        weighted_sum = 0.0
        for name, price in prices.items():
            source = self._sources.get(name)
            if source:
                # Reduce weight if source has errors
                error_penalty = min(0.5, source.error_count * 0.1)
                weight = source.weight * (1.0 - error_penalty)
            else:
                weight = 0.5
            weighted_sum += price * weight
            total_weight += weight
        return weighted_sum / total_weight if total_weight > 0 else 0.0

    @staticmethod
    def _spread_pct(values: list[float]) -> float | None:
        """(max - min) / median * 100. Lower = more consensus."""
        if len(values) < 2:
            return None
        arr = np.array(values)
        median = float(np.median(arr))
        if median == 0:
            return None
        return round(float((arr.max() - arr.min()) / median * 100), 2)

    # ── Stats ─────────────────────────────────────────────────────────────

    async def stats(self) -> dict[str, Any]:
        """Return per-source stats and aggregate metrics."""
        source_stats = {}
        async with self._lock:
            for name, src in self._sources.items():
                source_stats[name] = {
                    "weight": src.weight,
                    "effective_weight": round(
                        src.weight * (1.0 - min(0.5, src.error_count * 0.1)), 3
                    ),
                    "last_price": src.last_price,
                    "last_latency": round(src.last_latency, 3),
                    "error_count": src.error_count,
                }
        return {
            "total_sources": len(self._sources),
            "sources": source_stats,
        }


# ── Singleton ─────────────────────────────────────────────────────────────

_price_engine: PriceConsensusEngine | None = None


def get_price_consensus() -> PriceConsensusEngine:
    """Get the global PriceConsensusEngine singleton."""
    global _price_engine
    if _price_engine is None:
        _price_engine = PriceConsensusEngine()
    return _price_engine
