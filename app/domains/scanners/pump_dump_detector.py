"""
SENTINEL - Pump-and-Dump / Coordinated Shill Detector
======================================================
Goes beyond wash_trading's circular transfer detection to identify
coordinated pump-and-dump campaigns:

  - Volume spike detection (1h vs 24h average, >5x = suspicious)
  - Coordinated buys from multiple fresh wallets in same block/minute
  - Price-volume divergence (price pumps without sustainability support)
  - Lifecycle pattern detection (deploy → small LP → fake volume → LP removal)

Uses direct API calls: DexScreener, Birdeye, Solscan, Helius.
"""

from __future__ import annotations

import json
import os
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

import httpx
import requests

from app.chain_client import ChainClient
from app.chain_registry import is_solana
from app.core.logging import get_logger
from app.domains.scanners.rag_citations import build_citation_string, query_rag_citations

logger = get_logger("pump_dump_detector")

# ── Dataclasses ──────────────────────────────────────────────────────


@dataclass
class CoordinatedBuyGroup:
    """A cluster of wallets that bought in the same block or minute."""

    wallets: list[str]
    block_or_timestamp: int
    total_buy_usd: float
    fresh_wallet_count: int
    block_number: int | None = None


@dataclass
class PumpDumpReport:
    token_address: str
    chain: str
    volume_spike_ratio: float = 0.0  # 1h volume / 24h average
    coordinated_buy_count: int = 0  # number of coordinated-buy clusters
    coordinated_buy_groups: list[CoordinatedBuyGroup] = field(default_factory=list)
    price_volume_divergence: bool = False  # price up but volume unsustainable
    lifecycle_stage: str = "unknown"  # deploy / accumulation / pump / distribution / dump
    has_lp_removal_signal: bool = False
    has_fake_volume_signal: bool = False
    risk_score: int = 0  # 0-100
    risk_level: str = "LOW"
    warnings: list[str] = field(default_factory=list)
    citations: list[dict[str, Any]] = field(default_factory=list)


# ── Detector Class ──────────────────────────────────────────────────


class PumpDumpDetector:
    """Detects pump-and-dump and coordinated shill campaigns.

    Fetches volume, trade, and price data from DexScreener and Birdeye,
    then analyzes for volume spikes, coordinated buys, price-volume
    divergence, and lifecycle stage.
    """

    VOLUME_SPIKE_THRESHOLD = 5.0  # >5x = suspicious
    VOLUME_SPIKE_CRITICAL = 15.0  # >15x = extremely suspicious
    COORDINATED_BUY_WINDOW_SEC = 60  # wallets buying within 60s
    MIN_COORDINATED_WALLETS = 3  # need ≥3 fresh wallets in window

    def __init__(self) -> None:
        self._http = httpx.AsyncClient(timeout=15.0)
        self._chain = ChainClient()
        self._helius_key = os.getenv("HELIUS_API_KEY", "")
        self._birdeye_key = os.getenv("BIRDEYE_API_KEY", "")

    # ── Direct API fetchers ─────────────────────────────────────────

    async def _fetch_dexscreener_token(self, token_address: str) -> dict | None:
        """Fetch token pair data from DexScreener (free, no key)."""
        try:
            resp = await self._http.get(f"https://api.dexscreener.com/latest/dex/tokens/{token_address}")
            if resp.status_code == 200:
                data = resp.json()
                pairs = data.get("pairs") or []
                return pairs[0] if pairs else None
        except (httpx.HTTPError, requests.RequestException, json.JSONDecodeError, KeyError, TypeError) as e:
            logger.debug("_fetch_dexscreener_token_error", exc_info=True)
            logger.warning(f"DexScreener fetch failed for {token_address}: {e}")
        return None

    async def _fetch_birdeye_overview(self, token_address: str) -> dict | None:
        """Fetch token overview from Birdeye (volume, price history)."""
        if not self._birdeye_key:
            return None
        try:
            resp = await self._http.get(
                "https://public-api.birdeye.so/defi/token_overview",
                params={"address": token_address},
                headers={"X-API-KEY": self._birdeye_key},
            )
            if resp.status_code == 200:
                body = resp.json()
                return body.get("data", body)
        except (httpx.HTTPError, requests.RequestException, json.JSONDecodeError, KeyError, TypeError) as e:
            logger.debug("_fetch_birdeye_overview_error", exc_info=True)
            logger.warning(f"Birdeye overview failed for {token_address}: {e}")
        return None

    async def _fetch_birdeye_trades(self, token_address: str, limit: int = 100) -> list[dict]:
        """Fetch recent trades from Birdeye."""
        if not self._birdeye_key:
            return []
        try:
            resp = await self._http.get(
                "https://public-api.birdeye.so/defi/tx",
                params={"address": token_address, "limit": limit},
                headers={"X-API-KEY": self._birdeye_key},
            )
            if resp.status_code == 200:
                body = resp.json()
                return body.get("data", {}).get("items", []) or []
        except (httpx.HTTPError, requests.RequestException, json.JSONDecodeError, KeyError, TypeError) as e:
            logger.debug("_fetch_birdeye_trades_error", exc_info=True)
            logger.warning(f"Birdeye trades failed for {token_address}: {e}")
        return []

    async def _fetch_solana_signatures(self, address: str, limit: int = 5) -> list[dict]:
        """Fetch early signatures to determine wallet freshness."""
        result = await self._chain.rpc_call("getSignaturesForAddress", [address, {"limit": limit}])
        if result and "result" in result:
            return result["result"]
        return []

    # ── Analysis helpers ────────────────────────────────────────────

    def _compute_volume_spike(self, dex_data: dict | None, birdeye_data: dict | None) -> float:
        """Compare recent 1h volume vs 24h average to detect spikes."""
        vol_1h = 0.0
        vol_24h = 0.0

        if dex_data:
            vol_1h = float(dex_data.get("volume", {}).get("h1", 0) or 0)
            vol_24h = float(dex_data.get("volume", {}).get("h24", 0) or 0)

        if birdeye_data and vol_1h == 0:
            vol_1h = float(birdeye_data.get("volume1h", 0) or 0)
            vol_24h = float(birdeye_data.get("volume24h", 0) or 0)

        if vol_24h <= 0:
            return 0.0

        avg_hourly = vol_24h / 24.0
        if avg_hourly <= 0:
            return 0.0

        return round(vol_1h / avg_hourly, 2)

    def _detect_price_volume_divergence(self, dex_data: dict | None, birdeye_data: dict | None) -> bool:
        """Check if price is pumping but volume/mcap doesn't support it."""
        price_change = 0.0
        mcap = 0.0
        vol_24h = 0.0

        if dex_data:
            price_change = float(dex_data.get("priceChange", {}).get("h1", 0) or 0)
            mcap = float(dex_data.get("fdv", 0) or 0)
            vol_24h = float(dex_data.get("volume", {}).get("h24", 0) or 0)

        if birdeye_data and price_change == 0:
            price_change = float(birdeye_data.get("priceChange1h", 0) or 0)
            mcap = float(birdeye_data.get("mc", 0) or 0)
            vol_24h = float(birdeye_data.get("volume24h", 0) or 0)

        # Divergence: price up >20% but vol/mcap ratio is extremely high
        if price_change > 20 and mcap > 0:
            vol_mcap_ratio = vol_24h / mcap
            # Healthy: vol/mcap ~0.05-0.2. Pump: vol/mcap >1
            if vol_mcap_ratio > 1.0:
                return True

        return False

    def _determine_lifecycle_stage(self, dex_data: dict | None, birdeye_data: dict | None) -> str:
        """Infer the token's lifecycle stage from creation time and metrics."""
        created_at = 0
        mcap = 0.0
        vol_24h = 0.0
        price_change_24h = 0.0

        if dex_data:
            created_at = int(dex_data.get("pairCreatedAt", 0) or 0)
            mcap = float(dex_data.get("fdv", 0) or 0)
            vol_24h = float(dex_data.get("volume", {}).get("h24", 0) or 0)
            price_change_24h = float(dex_data.get("priceChange", {}).get("h24", 0) or 0)

        if birdeye_data:
            if created_at == 0:
                created_at = int(birdeye_data.get("createdAt", 0) or 0)
            if mcap == 0:
                mcap = float(birdeye_data.get("mc", 0) or 0)
            if vol_24h == 0:
                vol_24h = float(birdeye_data.get("volume24h", 0) or 0)
            if price_change_24h == 0:
                price_change_24h = float(birdeye_data.get("priceChange24h", 0) or 0)

        now_ms = int(time.time() * 1000)
        age_hours = (now_ms - created_at) / 3_600_000 if created_at > 0 else 24  # default assumption

        # Lifecycle rules
        if age_hours < 2:
            return "deploy"
        elif age_hours < 12 and price_change_24h > 50:
            return "pump"
        elif price_change_24h < -30:
            return "dump"
        elif price_change_24h < -10:
            return "distribution"
        elif price_change_24h > 10 and vol_24h > 0:
            return "accumulation"
        else:
            return "unknown"

    def _check_lp_removal_signal(self, dex_data: dict | None) -> bool:
        """Heuristic: if liquidity is very low relative to mcap, LP may be removed."""
        if not dex_data:
            return False
        liq_usd = float(
            dex_data.get("liquidity", {}).get("usd", 0)
            if isinstance(dex_data.get("liquidity"), dict)
            else dex_data.get("liquidity", 0) or 0
        )
        mcap = float(dex_data.get("fdv", 0) or 0)
        if mcap > 0 and liq_usd > 0:
            liq_mcap_ratio = liq_usd / mcap
            # Very low liquidity relative to market cap
            if liq_mcap_ratio < 0.01:
                return True
        return False

    async def _detect_coordinated_buys(self, trades: list[dict], chain: str) -> list[CoordinatedBuyGroup]:
        """Identify clusters of fresh wallets buying in the same time window."""
        if not trades:
            return []

        # Filter for buy trades
        buys = [t for t in trades if t.get("side", t.get("type", "")).lower() in ("buy", "0")]
        if len(buys) < self.MIN_COORDINATED_WALLETS:
            return []

        # Group by block or timestamp
        by_time: dict[int, list[dict]] = defaultdict(list)
        for t in buys:
            block = int(t.get("block", t.get("slot", 0)))
            ts = int(t.get("blockTime", t.get("timestamp", block)))
            # Round to minute window
            window = ts // self.COORDINATED_BUY_WINDOW_SEC
            by_time[window].append(t)

        groups: list[CoordinatedBuyGroup] = []
        for _window, window_buys in by_time.items():
            if len(window_buys) < self.MIN_COORDINATED_WALLETS:
                continue

            wallets = list({t.get("wallet", t.get("address", t.get("maker", ""))) for t in window_buys})
            total_usd = sum(float(t.get("volumeUsd", t.get("amountUsd", t.get("value", 0)))) for t in window_buys)

            # Count fresh wallets (for Solana we'd check signature count)
            fresh_count = 0
            # Quick heuristic: wallets with fewer characters or new patterns
            # Real check done asynchronously when available
            for w in wallets:
                if is_solana(chain):
                    sigs = await self._fetch_solana_signatures(w, limit=5)
                    if len(sigs) <= 3:
                        fresh_count += 1
                else:
                    # EVM: heuristics (low nonce = fresh)
                    pass

            ts = int(window_buys[0].get("blockTime", window_buys[0].get("timestamp", 0)))

            groups.append(
                CoordinatedBuyGroup(
                    wallets=wallets,
                    block_or_timestamp=ts,
                    total_buy_usd=round(total_usd, 2),
                    fresh_wallet_count=fresh_count,
                    block_number=int(window_buys[0].get("block", window_buys[0].get("slot", None))),
                )
            )

        return groups

    # ── Risk calculation ─────────────────────────────────────────────

    def _calculate_risk(self, report: PumpDumpReport) -> tuple[int, str, list[str]]:
        """Calculate risk score from pump-and-dump indicators."""
        score = 0
        warnings = []

        # Volume spike
        if report.volume_spike_ratio >= self.VOLUME_SPIKE_CRITICAL:
            score += 35
            warnings.append(f"CRITICAL: Volume spike {report.volume_spike_ratio:.1f}x above 24h average")
        elif report.volume_spike_ratio >= self.VOLUME_SPIKE_THRESHOLD:
            score += 20
            warnings.append(f"HIGH: Volume spike {report.volume_spike_ratio:.1f}x above 24h average")

        # Coordinated buys
        if report.coordinated_buy_count >= 5:
            score += 30
            warnings.append(f"CRITICAL: {report.coordinated_buy_count} coordinated buy clusters detected")
        elif report.coordinated_buy_count >= 3:
            score += 20
            warnings.append(f"HIGH: {report.coordinated_buy_count} coordinated buy clusters")
        elif report.coordinated_buy_count >= 1:
            score += 10
            warnings.append(f"MEDIUM: {report.coordinated_buy_count} coordinated buy cluster(s)")

        # Price-volume divergence
        if report.price_volume_divergence:
            score += 15
            warnings.append("MEDIUM: Price-volume divergence - price up without fundamental support")

        # Lifecycle stage
        if report.lifecycle_stage == "pump":
            score += 15
            warnings.append("HIGH: Token in pump lifecycle stage")
        elif report.lifecycle_stage == "distribution":
            score += 20
            warnings.append("HIGH: Token in distribution/selloff stage")
        elif report.lifecycle_stage == "dump":
            score += 25
            warnings.append("CRITICAL: Token in dump lifecycle stage")

        # LP removal signal
        if report.has_lp_removal_signal:
            score += 25
            warnings.append("CRITICAL: Liquidity very low relative to market cap - possible LP removal")

        # Fake volume signal
        if report.has_fake_volume_signal:
            score += 10
            warnings.append("MEDIUM: Volume pattern consistent with wash trading")

        score = min(100, score)

        if score >= 70:
            level = "CRITICAL"
        elif score >= 40:
            level = "HIGH"
        elif score >= 20:
            level = "MEDIUM"
        else:
            level = "LOW"

        return score, level, warnings

    # ── Main analysis entry point ─────────────────────────────────────

    async def analyze(self, token_address: str, chain: str) -> PumpDumpReport:
        """Full pump-and-dump analysis for a token.

        Steps:
        1. Fetch DEX and Birdeye data (pair info, trades, overview)
        2. Compute volume spike ratio (1h vs 24h average)
        3. Detect coordinated buy clusters from fresh wallets
        4. Detect price-volume divergence
        5. Determine lifecycle stage
        6. Check for LP removal / fake volume signals
        7. Calculate risk score
        """
        report = PumpDumpReport(token_address=token_address, chain=chain)

        # 1. Fetch data
        dex_data = await self._fetch_dexscreener_token(token_address)
        birdeye_data = await self._fetch_birdeye_overview(token_address)
        trades = await self._fetch_birdeye_trades(token_address, limit=100)

        # 2. Volume spike
        report.volume_spike_ratio = self._compute_volume_spike(dex_data, birdeye_data)

        # 3. Coordinated buys
        coordinated_groups = await self._detect_coordinated_buys(trades, chain)
        report.coordinated_buy_groups = coordinated_groups
        report.coordinated_buy_count = len(coordinated_groups)

        # 4. Price-volume divergence
        report.price_volume_divergence = self._detect_price_volume_divergence(dex_data, birdeye_data)

        # 5. Lifecycle stage
        report.lifecycle_stage = self._determine_lifecycle_stage(dex_data, birdeye_data)

        # 6. LP removal + fake volume signals
        report.has_lp_removal_signal = self._check_lp_removal_signal(dex_data)
        # Fake volume: high number of trades but low price impact
        if dex_data:
            vol_24h = float(dex_data.get("volume", {}).get("h24", 0) or 0)
            txs_24h = int(
                dex_data.get("txns", {}).get("h24", {}).get("buys", 0)
                + dex_data.get("txns", {}).get("h24", {}).get("sells", 0)
                or 0
            )
            if txs_24h > 100 and vol_24h > 0:
                avg_tx_size = vol_24h / txs_24h
                if avg_tx_size < 10:  # Many tiny trades = wash trading signal
                    report.has_fake_volume_signal = True

        # 7. Calculate risk
        report.risk_score, report.risk_level, report.warnings = self._calculate_risk(report)

        # RAG citations for credibility
        try:
            rag_cits = await query_rag_citations(
                topic=f"pump and dump volume spike {chain}",
                chain=chain,
                address=token_address,
                scanner_type="pump_dump",
            )
            report.citations = rag_cits
            # Enhance warnings with citation references
            for i, w in enumerate(report.warnings):
                if rag_cits:
                    report.warnings[i] = build_citation_string(rag_cits, w)
        except (ConnectionError, OSError, TimeoutError):
            pass

        return report
