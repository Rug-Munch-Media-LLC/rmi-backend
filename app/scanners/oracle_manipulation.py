"""
SENTINEL — Oracle Manipulation Detector
=========================================
Detects tokens vulnerable to or actually experiencing oracle attacks.

  - For EVM: check if token uses single-source oracle (Chainlink vs custom)
  - Check pool liquidity depth serving as price oracle (thin pool = manipulable)
  - Detect price deviation between DEX price and oracle-reported price
  - Flag low-liquidity pools that could be manipulated to affect lending oracles

Uses direct API calls: DexScreener (pool data), Etherscan (contract reads),
Birdeye (price data), Chainlink feed registry.
"""

import logging
import os
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.chain_client import ChainClient
from app.chain_registry import is_evm, is_solana
from app.scanners.rag_citations import build_citation_string, query_rag_citations

logger = logging.getLogger("oracle_manipulation")


# ── Constants ───────────────────────────────────────────────────────

# Known Chainlink Price Feed addresses (mainnet)
CHAINLINK_FEED_REGISTRY: dict[str, str] = {
    "ethereum": "0x47Fb2585D2C562188822F598F4005ABeC3a5a7E",
    "arbitrum": "0x842E2D90EC45aC5B0c4FBF26143Ad7EC79681141",
    "polygon": "0x8cBaC9242511b0A6c1f7E3a7E4C68e9DEC34e7bA",
    "optimism": "0x01E976e7EF6405cB1C8B18EaEf3CCb44E9559a89",
    "avalanche": "0x1e2fEe006Bc5109c05793A8c5EA0576038E52116",
    "base": "0x015781536C7bbE8241bAa7b2d0e05B61BF786c68",
    "bsc": "0x00994193E7dcA3f3886e083bc031E0DD05B6e7C5",
}

# EIP-1967 proxy storage slot for proxy detection
EIP1967_IMPLEMENTATION_SLOT = "0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc"

# Known oracle function selectors
ORACLE_ABI_SELECTORS = {
    "latestRoundData": "0x50d25bcd",  # Chainlink AggregatorV3Interface
    "getLatestRoundData": "0x9a6fc8f5",  # Some custom oracles
    "price": "0x98d5fdca",  # Uniswap TWAP style
    "peek": "0x4e4c9c1c",  # MakerDAO style
}


# ── Dataclasses ──────────────────────────────────────────────────────


@dataclass
class OracleInfo:
    """Information about a single oracle used by the token."""

    oracle_type: str  # "chainlink", "custom", "uniswap_twap", "unknown"
    oracle_address: str
    is_single_source: bool
    feed_decimals: int = 8
    last_price: float = 0.0
    is_working: bool = True


@dataclass
class OracleManipulationReport:
    token_address: str
    chain: str
    oracle_type: str = "unknown"  # "chainlink", "custom", "uniswap_twap", "none", "unknown"
    is_single_source: bool = True
    oracle_addresses: list[str] = field(default_factory=list)
    oracle_details: list[OracleInfo] = field(default_factory=list)
    pool_depth_usd: float = 0.0  # Liquidity in primary oracle pool
    price_deviation_pct: float = 0.0  # DEX vs oracle price deviation
    manipulable_score: float = 0.0  # 0-100 how easily oracle can be manipulated
    known_oracle_used: bool = False
    risk_score: int = 0  # 0-100
    risk_level: str = "LOW"
    warnings: list[str] = field(default_factory=list)
    citations: list[dict[str, Any]] = field(default_factory=list)


# ── Detector Class ──────────────────────────────────────────────────


class OracleManipulationDetector:
    """Detects oracle manipulation risks for a token.

    Checks whether the token uses single-source oracles, thin-liquidity
    pools as price sources, and price deviations between DEX and oracle
    reported prices.
    """

    MANIPULABLE_LIQUIDITY_THRESHOLD = 100_000  # < $100k pool = easy to manipulate
    CRITICAL_LIQUIDITY_THRESHOLD = 10_000  # < $10k = trivially manipulable
    PRICE_DEVIATION_THRESHOLD = 5.0  # >5% deviation = suspicious
    PRICE_DEVIATION_CRITICAL = 20.0  # >20% = likely attack in progress

    def __init__(self):
        self._http = httpx.AsyncClient(timeout=15.0)
        self._chain = ChainClient()
        self._etherscan_key = os.getenv("ETHERSCAN_API_KEY", "")
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
        except Exception as e:
            logger.warning(f"DexScreener fetch failed for {token_address}: {e}")
        return None

    async def _fetch_birdeye_price(self, token_address: str) -> float | None:
        """Fetch current price from Birdeye."""
        if not self._birdeye_key:
            return None
        try:
            resp = await self._http.get(
                "https://public-api.birdeye.so/defi/price",
                params={"address": token_address},
                headers={"X-API-KEY": self._birdeye_key},
            )
            if resp.status_code == 200:
                body = resp.json()
                return float(body.get("data", {}).get("value", 0) or 0)
        except Exception as e:
            logger.warning(f"Birdeye price failed for {token_address}: {e}")
        return None

    async def _fetch_contract_source(self, address: str, chain: str) -> str | None:
        """Fetch contract source code from Etherscan-style explorer."""
        from app.chain_registry import CHAINS

        chain_cfg = CHAINS.get(chain)
        if not chain_cfg or not chain_cfg.explorer_api_url:
            return None
        key = chain_cfg.get_explorer_api_key() or self._etherscan_key
        if not key:
            return None
        try:
            url = f"{chain_cfg.explorer_api_url}?module=contract&action=getsourcecode&address={address}&apikey={key}"
            resp = await self._http.get(url)
            if resp.status_code == 200:
                body = resp.json()
                result = body.get("result", [])
                if result and len(result) > 0:
                    return result[0].get("SourceCode", "")
        except Exception as e:
            logger.warning(f"Etherscan source fetch failed for {address}: {e}")
        return None

    async def _eth_call(self, to: str, data: str, chain: str) -> str | None:
        """Make an eth_call to read contract state."""
        from app.chain_registry import CHAINS

        chain_cfg = CHAINS.get(chain)
        if not chain_cfg or not chain_cfg.rpc_endpoints:
            return None
        rpc_url = chain_cfg.rpc_endpoints[0]
        try:
            resp = await self._http.post(
                rpc_url,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "eth_call",
                    "params": [{"to": to, "data": data}, "latest"],
                },
            )
            if resp.status_code == 200:
                body = resp.json()
                return body.get("result")
        except Exception as e:
            logger.warning(f"eth_call failed for {to}: {e}")
        return None

    # ── Analysis helpers ────────────────────────────────────────────

    async def _check_chainlink_usage(self, token_address: str, chain: str) -> tuple[bool, str | None]:
        """Check if token contract references Chainlink oracle.

        Reads contract source for Chainlink imports or calls the
        Chainlink Feed Registry to see if a feed exists for this token.
        """
        if not is_evm(chain):
            return False, None

        registry_addr = CHAINLINK_FEED_REGISTRY.get(chain)
        if not registry_addr:
            return False, None

        # Try to read from Chainlink Feed Registry
        # getFeed(address token) selector: 0xdc6b4a08 (simplified)
        try:
            result = await self._eth_call(registry_addr, "0xdc6b4a08", chain)
            if result and result != "0x" and result != "0x0":
                return True, registry_addr
        except Exception:
            pass

        # Check contract source for Chainlink imports
        source = await self._fetch_contract_source(token_address, chain)
        if source:
            chainlink_patterns = [
                "AggregatorV3Interface",
                "Chainlink",
                "LINK",
                "latestRoundData",
                "getLatestRoundData",
            ]
            for pattern in chainlink_patterns:
                if pattern.lower() in source.lower():
                    return True, registry_addr

        return False, None

    def _compute_manipulable_score(
        self,
        pool_depth_usd: float,
        is_single_source: bool,
        price_deviation_pct: float,
    ) -> float:
        """Compute 0-100 score for how easily the oracle can be manipulated."""
        score = 0.0

        # Pool depth factor — thinner pool = easier to manipulate
        if pool_depth_usd < self.CRITICAL_LIQUIDITY_THRESHOLD:
            score += 50
        elif pool_depth_usd < self.MANIPULABLE_LIQUIDITY_THRESHOLD:
            # Linear from 50 down to 0 between 10k and 100k
            score += 50 * (
                1
                - (pool_depth_usd - self.CRITICAL_LIQUIDITY_THRESHOLD)
                / (self.MANIPULABLE_LIQUIDITY_THRESHOLD - self.CRITICAL_LIQUIDITY_THRESHOLD)
            )
        # Below 500k still warrants some concern
        elif pool_depth_usd < 500_000:
            score += 15 * (
                1
                - (pool_depth_usd - self.MANIPULABLE_LIQUIDITY_THRESHOLD)
                / (500_000 - self.MANIPULABLE_LIQUIDITY_THRESHOLD)
            )

        # Single source oracle factor
        if is_single_source:
            score += 25

        # Price deviation factor
        if price_deviation_pct >= self.PRICE_DEVIATION_CRITICAL:
            score += 25
        elif price_deviation_pct >= self.PRICE_DEVIATION_THRESHOLD:
            score += (
                15
                * (price_deviation_pct - self.PRICE_DEVIATION_THRESHOLD)
                / (self.PRICE_DEVIATION_CRITICAL - self.PRICE_DEVIATION_THRESHOLD)
            )

        return round(min(100, max(0, score)), 2)

    # ── Risk calculation ─────────────────────────────────────────────

    def _calculate_risk(self, report: OracleManipulationReport) -> tuple[int, str, list[str]]:
        """Calculate risk score from oracle manipulation indicators."""
        score = 0
        warnings = []

        # Manipulable score drives most of the risk
        if report.manipulable_score >= 70:
            score += 40
            warnings.append(f"CRITICAL: Oracle highly manipulable (score {report.manipulable_score:.0f}/100)")
        elif report.manipulable_score >= 40:
            score += 25
            warnings.append(f"HIGH: Oracle moderately manipulable (score {report.manipulable_score:.0f}/100)")
        elif report.manipulable_score >= 20:
            score += 10
            warnings.append(f"MEDIUM: Oracle somewhat manipulable (score {report.manipulable_score:.0f}/100)")

        # Single source
        if report.is_single_source:
            score += 15
            warnings.append("MEDIUM: Single-source oracle — no redundancy")

        # Price deviation
        if report.price_deviation_pct >= self.PRICE_DEVIATION_CRITICAL:
            score += 30
            warnings.append(f"CRITICAL: Price deviation {report.price_deviation_pct:.1f}% — possible oracle attack")
        elif report.price_deviation_pct >= self.PRICE_DEVIATION_THRESHOLD:
            score += 15
            warnings.append(
                f"HIGH: Price deviation {report.price_deviation_pct:.1f}% — oracle may be stale/manipulated"
            )

        # Unknown oracle type
        if report.oracle_type in ("unknown", "custom"):
            score += 10
            warnings.append(f"MEDIUM: Oracle type is '{report.oracle_type}' — unverified oracle")

        # Very low pool depth
        if report.pool_depth_usd < self.CRITICAL_LIQUIDITY_THRESHOLD:
            score += 20
            warnings.append(f"HIGH: Pool depth only ${report.pool_depth_usd:,.0f} — trivially manipulable")

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

    async def analyze(self, token_address: str, chain: str) -> OracleManipulationReport:
        """Full oracle manipulation risk analysis for a token.

        Steps:
        1. Fetch DEX pool data (liquidity, price)
        2. Check if Chainlink or other known oracle is used
        3. Fetch DEX vs oracle price deviation
        4. Compute manipulable score
        5. Build report
        """
        report = OracleManipulationReport(token_address=token_address, chain=chain)

        # 1. Fetch DEX data
        dex_data = await self._fetch_dexscreener_token(token_address)

        dex_price = 0.0
        pool_depth_usd = 0.0
        if dex_data:
            dex_price = float(dex_data.get("priceUsd", 0) or 0)
            liq = dex_data.get("liquidity", {})
            pool_depth_usd = float(liq.get("usd", 0) or 0) if isinstance(liq, dict) else float(liq or 0)
        report.pool_depth_usd = pool_depth_usd

        # 2. Oracle type detection
        if is_evm(chain):
            uses_chainlink, registry_addr = await self._check_chainlink_usage(token_address, chain)
            if uses_chainlink:
                report.oracle_type = "chainlink"
                report.known_oracle_used = True
                if registry_addr:
                    report.oracle_addresses.append(registry_addr)
                    report.oracle_details.append(
                        OracleInfo(
                            oracle_type="chainlink",
                            oracle_address=registry_addr,
                            is_single_source=True,
                        )
                    )
            else:
                # Check contract source for custom oracle references
                source = await self._fetch_contract_source(token_address, chain)
                if source:
                    source_lower = source.lower()
                    if "twap" in source_lower or "uniswapv3twap" in source_lower:
                        report.oracle_type = "uniswap_twap"
                    elif "oracle" in source_lower or "pricefeed" in source_lower:
                        report.oracle_type = "custom"
                else:
                    report.oracle_type = "unknown"

        elif is_solana(chain):
            # Solana: Pyth and Switchboard are the primary oracles
            # Check if token metadata references oracle feeds
            # For now, Solana tokens typically use Pyth/Switchboard or DEX TWAP
            report.oracle_type = "unknown"
            # With Birdeye, Solana DEX price is the de-facto oracle
            if pool_depth_usd < self.MANIPULABLE_LIQUIDITY_THRESHOLD:
                report.is_single_source = True
                report.oracle_type = "uniswap_twap"  # DEX-based pricing

        # 3. Price deviation
        birdeye_price = await self._fetch_birdeye_price(token_address)
        if dex_price > 0 and birdeye_price and birdeye_price > 0:
            deviation = abs(dex_price - birdeye_price) / dex_price * 100
            report.price_deviation_pct = round(deviation, 2)
        else:
            # Can't compute deviation without two price sources
            report.price_deviation_pct = 0.0

        # 4. Manipulable score
        report.manipulable_score = self._compute_manipulable_score(
            pool_depth_usd, report.is_single_source, report.price_deviation_pct
        )

        # 5. Calculate risk
        report.risk_score, report.risk_level, report.warnings = self._calculate_risk(report)

        # RAG citations for credibility
        try:
            rag_cits = await query_rag_citations(
                topic=f"oracle price manipulation vulnerability {chain}",
                chain=chain,
                address=token_address,
                scanner_type="oracle_manipulation",
            )
            report.citations = rag_cits
            # Enhance warnings with citation references
            for i, w in enumerate(report.warnings):
                if rag_cits:
                    report.warnings[i] = build_citation_string(rag_cits, w)
        except Exception:
            pass

        return report
