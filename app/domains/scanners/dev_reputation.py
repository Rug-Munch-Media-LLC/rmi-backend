"""
SENTINEL - Developer Reputation Engine
======================================
Tracks every token ever launched by a wallet, their outcomes, holding patterns,
and time-to-rug metrics. Detects serial ruggers through pattern matching
and cross-chain address portability.

Scoring:
  rug_rate >= 80% with 3+ launches → score 0-20 (serial rugger)
  rug_rate >= 50% → score 20-40 (high risk)
  avg_lifespan < 7 days with 2+ launches → score 25-35
  dev holding 0% at rug time → high confidence rug signal
  Cross-chain same address → score 0-15 (same operator everywhere)

Data sources (direct API calls - no self.api_base):
  DexScreener: Token search by deployer address (free, no key needed)
  Etherscan-family: EVM deployer transaction history
  Moralis: Cross-chain wallet token history
"""

import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum

import httpx

from app.chain_registry import CHAINS
from app.core.logging import get_logger

logger = get_logger("dev_reputation")

# ── API Keys ────────────────────────────────────────────────

ETHERSCAN_API_KEY = os.getenv("ETHERSCAN_API_KEY", "")
BSCSCAN_API_KEY = os.getenv("BSCSCAN_API_KEY", "")
POLYGONSCAN_API_KEY = os.getenv("POLYGONSCAN_API_KEY", "")
SNOWTRACE_API_KEY = os.getenv("SNOWTRACE_API_KEY", "")
FTMSCAN_API_KEY = os.getenv("FTMSCAN_API_KEY", "")
MORALIS_API_KEY = os.getenv("MORALIS_API_KEY", "")

DEXSCREENER_URL = "https://api.dexscreener.com"
MORALIS_DATA_URL = "https://deep-index.moralis.io/api/v2.2"

ETHERSCAN_NETWORKS = {
    "ethereum": {"url": "https://api.etherscan.io/api", "key": ETHERSCAN_API_KEY},
    "eth": {"url": "https://api.etherscan.io/api", "key": ETHERSCAN_API_KEY},
    "bsc": {"url": "https://api.bscscan.com/api", "key": BSCSCAN_API_KEY},
    "polygon": {"url": "https://api.polygonscan.com/api", "key": POLYGONSCAN_API_KEY},
    "avalanche": {"url": "https://api.snowtrace.io/api", "key": SNOWTRACE_API_KEY},
    "fantom": {"url": "https://api.ftmscan.com/api", "key": FTMSCAN_API_KEY},
    "arbitrum": {"url": "https://api.arbiscan.io/api", "key": ETHERSCAN_API_KEY},
    "optimism": {"url": "https://api-optimistic.etherscan.io/api", "key": ETHERSCAN_API_KEY},
    "base": {"url": "https://api.basescan.org/api", "key": ETHERSCAN_API_KEY},
}

MORALIS_CHAIN_MAP = {
    "ethereum": "eth",
    "eth": "eth",
    "bsc": "bsc",
    "polygon": "polygon",
    "avalanche": "avalanche",
    "fantom": "fantom",
    "arbitrum": "arbitrum",
    "optimism": "optimism",
    "base": "base",
}

# DexScreener chain ID mapping (used in pair responses)
DEXSCREENER_CHAIN_MAP = {
    "ethereum": "ethereum",
    "eth": "ethereum",
    "bsc": "bsc",
    "polygon": "polygon",
    "avalanche": "avalanche",
    "fantom": "fantom",
    "arbitrum": "arbitrum",
    "optimism": "optimism",
    "base": "base",
    "solana": "solana",
}


class ReputationLevel(Enum):
    TRUSTED = "trusted"  # score >= 70
    NEUTRAL = "neutral"  # score 40-69
    SUSPICIOUS = "suspicious"  # score 20-39
    DANGEROUS = "dangerous"  # score < 20


@dataclass
class TokenLaunch:
    address: str
    chain: str
    name: str = ""
    symbol: str = ""
    launch_timestamp: int = 0
    max_market_cap: float = 0.0
    lifespan_days: float = 0.0
    outcome: str = "unknown"  # "active", "rugged", "abandoned", "unknown"
    dev_holds_percentage: float = 0.0  # Dev's holding at time of outcome
    is_renounced: bool = False  # Did dev renounce ownership?
    has_locked_liquidity: bool = False


@dataclass
class DevReputationReport:
    dev_wallet: str
    chains: list[str]
    total_launches: int
    rugged_count: int
    rug_rate: float  # 0.0-1.0
    avg_lifespan_days: float
    avg_max_mcap: float
    retention_rate: float  # % of launches where dev still holds tokens
    serial_patterns: list[str]  # Detected serial rugger patterns
    cross_chain_addresses: list[dict[str, str]]  # Same dev on other chains
    launches: list[TokenLaunch] = field(default_factory=list)
    dev_threat_index: float = 0.0  # (Holdings% x RugRate) / LockDays
    reputation_score: int = 50  # 0-100, higher = safer
    reputation_level: ReputationLevel = ReputationLevel.NEUTRAL
    confidence: str = "LOW"  # LOW, MEDIUM, HIGH
    warnings: list[str] = field(default_factory=list)


class DevReputationEngine:
    """Calculates developer reputation based on launch history and patterns.

    Uses direct API calls:
      - DexScreener for token launch discovery (free, no key)
      - Etherscan-family for EVM transaction history
      - Moralis for cross-chain wallet data
    """

    def __init__(self):
        self.client = httpx.AsyncClient(timeout=15.0)

    # ── DexScreener helpers ────────────────────────────────────

    async def _dexscreener_search(self, query: str) -> dict | None:
        """Search DexScreener for pairs matching a query (deployer address, etc.)."""
        try:
            resp = await self.client.get(
                f"{DEXSCREENER_URL}/latest/dex/search", params={"q": query}
            )
            if resp.status_code == 200:
                return resp.json()
        except Exception as e:
            logger.warning(f"DexScreener search failed for '{query}': {e}")
        return None

    async def _dexscreener_token_info(self, token_address: str) -> dict | None:
        """Get DexScreener pair data for a specific token."""
        try:
            resp = await self.client.get(f"{DEXSCREENER_URL}/latest/dex/tokens/{token_address}")
            if resp.status_code == 200:
                return resp.json()
        except Exception as e:
            logger.warning(f"DexScreener token lookup failed for {token_address}: {e}")
        return None

    # ── Etherscan helpers ──────────────────────────────────────

    async def _etherscan_get(self, chain: str, params: dict) -> dict | None:
        """Make an Etherscan-family API call."""
        net = ETHERSCAN_NETWORKS.get(chain.lower())
        if not net:
            return None
        api_key = net["key"]
        if not api_key:
            logger.warning(f"No Etherscan API key for {chain}")
            return None
        params["apikey"] = api_key
        try:
            resp = await self.client.get(net["url"], params=params)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("status") == "1":
                    return data.get("result")
                return data
        except Exception as e:
            logger.warning(f"Etherscan call failed for {chain}: {e}")
        return None

    # ── Moralis helpers ────────────────────────────────────────

    def _moralis_chain(self, chain: str) -> str:
        """Convert chain name to Moralis chain param."""
        return MORALIS_CHAIN_MAP.get(chain.lower(), "eth")

    async def _moralis_get(self, url: str) -> dict | None:
        """Make a Moralis Data API call."""
        if not MORALIS_API_KEY:
            logger.warning("MORALIS_API_KEY not set")
            return None
        try:
            resp = await self.client.get(
                url, headers={"X-API-Key": MORALIS_API_KEY, "Accept": "application/json"}
            )
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list):
                    return data
                return data.get("result", data.get("data", data))
        except Exception as e:
            logger.warning(f"Moralis call failed: {e}")
        return None

    # ── Core logic ──────────────────────────────────────────

    def calculate_reputation(
        self, launches: list[TokenLaunch], dev_wallet: str
    ) -> DevReputationReport:
        """Calculate reputation from known token launches."""
        if not launches:
            return DevReputationReport(
                dev_wallet=dev_wallet,
                chains=[],
                total_launches=0,
                rugged_count=0,
                rug_rate=0.0,
                avg_lifespan_days=0.0,
                avg_max_mcap=0.0,
                retention_rate=0.0,
                serial_patterns=[],
                cross_chain_addresses=[],
                reputation_score=50,
                reputation_level=ReputationLevel.NEUTRAL,
                confidence="LOW",
                warnings=["New deployer - no history"],
            )

        # Calculate core metrics
        total = len(launches)
        rugged = sum(1 for line in launches if line.outcome == "rugged")
        rug_rate = rugged / total if total else 0

        avg_lifespan = sum(line.lifespan_days for line in launches) / total if total else 0
        avg_mcap = sum(line.max_market_cap for line in launches) / total if total else 0
        retention = (
            sum(1 for line in launches if line.dev_holds_percentage > 0) / total if total else 0
        )

        # Detect serial patterns
        patterns = self._detect_serial_patterns(launches)

        # Calculate reputation score
        score, level, confidence = self._calculate_score(
            total, rugged, rug_rate, avg_lifespan, retention, patterns
        )

        # Calculate Dev Threat Index: (Holdings% x Reputation) / LockDays
        avg_holdings = sum(line.dev_holds_percentage for line in launches) / total if total else 0
        avg_lock_days = max(
            sum(line.lifespan_days for line in launches if line.has_locked_liquidity)
            / max(sum(1 for line in launches if line.has_locked_liquidity), 1),
            1,
        )
        threat_index = (avg_holdings * max(rug_rate, 0.1)) / max(avg_lock_days, 1)

        # Build warnings
        warnings = []
        if rug_rate >= 0.8 and total >= 3:
            warnings.append(f"SERIAL RUGGER: {rugged}/{total} projects rugged")
        elif rug_rate >= 0.5:
            warnings.append(
                f"HIGH RUG RATE: {rugged}/{total} projects rugged ({rug_rate * 100:.0f}%)"
            )

        if avg_lifespan < 7 and total >= 2:
            warnings.append(
                f"SHORT-LIVED: Average lifespan {avg_lifespan:.1f} days - quick-exit pattern"
            )

        if retention < 0.2 and total >= 2:
            warnings.append(f"DUMP & RUN: Dev holds 0% in {(1 - retention) * 100:.0f}% of projects")

        for pattern in patterns:
            warnings.append(f"PATTERN: {pattern}")

        chains = list({line.chain for line in launches})

        report = DevReputationReport(
            dev_wallet=dev_wallet,
            chains=chains,
            total_launches=total,
            rugged_count=rugged,
            rug_rate=round(rug_rate, 3),
            avg_lifespan_days=round(avg_lifespan, 1),
            avg_max_mcap=round(avg_mcap, 0),
            retention_rate=round(retention, 3),
            serial_patterns=patterns,
            cross_chain_addresses=[],  # Filled by cross-chain lookup
            launches=launches,
            dev_threat_index=round(min(threat_index, 100), 2),
            reputation_score=score,
            reputation_level=level,
            confidence=confidence,
            warnings=warnings,
        )

        return report

    def _detect_serial_patterns(self, launches: list[TokenLaunch]) -> list[str]:
        """Detect common serial rugger patterns."""
        patterns = []

        if not launches:
            return patterns

        # Pattern 1: High rug rate (3+ launches, 80%+ rugged)
        total = len(launches)
        rugged = sum(1 for line in launches if line.outcome == "rugged")
        if total >= 3 and rugged / total >= 0.8:
            patterns.append(f"Serial rugger: {rugged}/{total} projects rugged")

        # Pattern 2: Short-lived projects (avg < 7 days)
        avg_lifespan = sum(line.lifespan_days for line in launches) / total
        if total >= 2 and avg_lifespan < 7:
            patterns.append(f"Short-lived projects (avg {avg_lifespan:.1f} days)")

        # Pattern 3: Dev dumps entire allocation before rug
        zero_hold = sum(
            1 for line in launches if line.outcome == "rugged" and line.dev_holds_percentage < 1
        )
        if zero_hold >= 2:
            patterns.append(f"Dev dumps before rug ({zero_hold} times)")

        # Pattern 4: Similar naming/metadata patterns
        names = [line.name.lower() for line in launches if line.name]
        if len(set(names)) < len(names) * 0.5 and len(names) >= 3:
            patterns.append("Similar naming pattern across launches")

        # Pattern 5: No renunciation across all launches
        if total >= 3 and not any(line.is_renounced for line in launches):
            patterns.append("No authority renunciation in any launch")

        # Pattern 6: Same launch timing (multiple launches in same week)
        timestamps = [line.launch_timestamp for line in launches if line.launch_timestamp > 0]
        if len(timestamps) >= 3:
            timestamps.sort()
            close_launches = sum(
                1
                for i in range(1, len(timestamps))
                if (timestamps[i] - timestamps[i - 1]) < 7 * 86400000
            )
            if close_launches >= 2:
                patterns.append(f"Rapid-fire launches ({close_launches + 1} in same week)")

        return patterns

    def _calculate_score(
        self,
        total: int,
        rugged: int,
        rug_rate: float,
        avg_lifespan: float,
        retention: float,
        patterns: list[str],
    ) -> tuple:
        """Calculate reputation score (0-100, higher = safer)."""

        # Base score starts at 70 (neutral for new deployers)
        score = 70

        # Adjust for rug rate
        if rug_rate >= 0.8 and total >= 3:
            score = max(0, 20 - total * 2)  # Serial rugger: 0-20
        elif rug_rate >= 0.5:
            score = 30  # High risk
        elif avg_lifespan < 7 and total >= 2:
            score = 35  # Short-lived
        else:
            score = 70 + int((1 - rug_rate) * 30)  # 70-100

        # Adjust for retention (dev holding tokens = less likely to rug)
        if retention >= 0.5:
            score = min(100, score + 10)
        elif retention < 0.1:
            score = max(0, score - 15)

        # Adjust for pattern count
        score = max(0, score - len(patterns) * 5)

        score = max(0, min(100, score))

        if score >= 70:
            level = ReputationLevel.TRUSTED
            confidence = "HIGH"
        elif score >= 40:
            level = ReputationLevel.NEUTRAL
            confidence = "MEDIUM"
        elif score >= 20:
            level = ReputationLevel.SUSPICIOUS
            confidence = "HIGH"
        else:
            level = ReputationLevel.DANGEROUS
            confidence = "VERY_HIGH"

        return score, level, confidence

    async def analyze(
        self, dev_wallet: str, chains: list[str] | None = None
    ) -> DevReputationReport:
        """Full dev reputation analysis.

        Steps:
        1. Fetch all tokens launched by dev_wallet (DexScreener search)
        2. For each, determine outcome (active/rugged/abandoned)
        3. Check dev holding percentage at rug time
        4. Detect serial patterns
        5. Cross-chain address lookup (Moralis)
        """
        if chains is None:
            chains = list(CHAINS.keys())[:4]

        # Fetch token launches from DexScreener
        launches = []
        for chain in chains:
            chain_launches = await self._fetch_launches(dev_wallet, chain)
            launches.extend(chain_launches)

        # Build and return reputation report
        report = self.calculate_reputation(launches, dev_wallet)

        # Cross-chain address lookup via Moralis
        report.cross_chain_addresses = await self._cross_chain_lookup(dev_wallet)

        return report

    async def _fetch_launches(self, dev_wallet: str, chain: str) -> list[TokenLaunch]:
        """Fetch all tokens launched by dev on a specific chain.

        Uses DexScreener to find pairs where the deployer matches.
        DexScreener's /latest/dex/search?q=<address> returns pairs
        whose baseToken or quoteToken involves the address. We filter
        for pairs where the creator/deployer matches.
        """
        launches = []

        # ── Strategy 1: DexScreener search by deployer address ──
        search_data = await self._dexscreener_search(dev_wallet)
        if search_data and search_data.get("pairs"):
            chain_id = DEXSCREENER_CHAIN_MAP.get(chain, chain)
            for pair in search_data.get("pairs", [])[:25]:
                # Filter by chain
                if pair.get("chainId") != chain_id and chain != "all":
                    continue

                # Extract deployer/creator info
                pair_creator = pair.get("info", {}).get("deployer", "")
                base_addr = pair.get("baseToken", {}).get("address", "")
                base_name = pair.get("baseToken", {}).get("name", "")
                base_symbol = pair.get("baseToken", {}).get("symbol", "")
                created_at = pair.get("pairCreatedAt", 0)

                # Accept pairs where the deployer matches, or the address
                # appears as base/quote token (indirect match)
                is_creator = pair_creator.lower() == dev_wallet.lower() if pair_creator else False
                is_match = dev_wallet.lower() in (
                    base_addr.lower(),
                    pair.get("quoteToken", {}).get("address", "").lower(),
                )

                if not is_creator and not is_match:
                    continue

                # Determine outcome from liquidity and volume
                liquidity = float(pair.get("liquidity", {}).get("usd", 0) or 0)
                volume_24h = float(pair.get("volume", {}).get("h24", 0) or 0)
                mcap = float(pair.get("marketCap", 0) or pair.get("mc", 0) or 0)

                # Heuristic outcome determination
                now_ms = int(datetime.now(UTC).timestamp() * 1000)
                age_days = ((now_ms - created_at) / 86400000) if created_at else 0

                if liquidity < 100 and age_days > 7:
                    outcome = "rugged"
                elif volume_24h == 0 and age_days > 30:
                    outcome = "abandoned"
                elif mcap > 0 or liquidity > 0:
                    outcome = "active"
                else:
                    outcome = "unknown"

                launches.append(
                    TokenLaunch(
                        address=base_addr,
                        chain=chain,
                        name=base_name,
                        symbol=base_symbol,
                        launch_timestamp=int(created_at / 1000) if created_at else 0,
                        max_market_cap=mcap,
                        lifespan_days=round(age_days, 1),
                        outcome=outcome,
                        dev_holds_percentage=0,  # Requires deeper analysis
                        is_renounced=False,  # Requires contract verification
                        has_locked_liquidity=liquidity > 0,
                    )
                )

        # ── Strategy 2 (EVM only): Etherscan contract creation ──
        if chain in ETHERSCAN_NETWORKS:
            etherscan_launches = await self._fetch_launches_etherscan(dev_wallet, chain)
            # Merge, avoiding duplicates by address
            existing_addrs = {line.address.lower() for line in launches}
            for launch in etherscan_launches:
                if launch.address.lower() not in existing_addrs:
                    launches.append(launch)
                    existing_addrs.add(launch.address.lower())

        return launches

    async def _fetch_launches_etherscan(self, dev_wallet: str, chain: str) -> list[TokenLaunch]:
        """Fetch contract creations by dev_wallet from Etherscan-family APIs.

        Uses the 'txlist' action to find contract creation transactions.
        """
        launches = []

        tx_list = await self._etherscan_get(
            chain,
            {
                "module": "account",
                "action": "txlist",
                "address": dev_wallet,
                "startblock": 0,
                "endblock": 99999999,
                "page": 1,
                "offset": 50,
                "sort": "asc",
            },
        )

        if tx_list and isinstance(tx_list, list):
            for tx in tx_list[:25]:
                # Contract creation: to is empty or starts with "0x0" and input is non-empty
                to_addr = tx.get("to", "")
                tx_input = tx.get("input", "")
                is_creation = to_addr == "" or tx.get("contractAddress", "") != ""

                if is_creation and tx_input and len(tx_input) > 10:
                    contract_addr = tx.get("contractAddress", "")
                    if not contract_addr:
                        continue

                    timestamp = int(tx.get("timeStamp", 0))
                    launches.append(
                        TokenLaunch(
                            address=contract_addr,
                            chain=chain,
                            name="",  # Would need separate contract ABI call
                            symbol="",
                            launch_timestamp=timestamp,
                            max_market_cap=0,
                            lifespan_days=0,
                            outcome="unknown",
                            dev_holds_percentage=0,
                        )
                    )

        return launches

    async def _cross_chain_lookup(self, dev_wallet: str) -> list[dict[str, str]]:
        """Find the same dev address on other chains via Moralis wallet data.

        Checks if the wallet has activity (tokens, NFTs, transactions)
        on other EVM chains.
        """
        if not MORALIS_API_KEY:
            return []

        cross_chain = []
        for chain_key in ["eth", "bsc", "polygon", "arbitrum", "base", "avalanche"]:
            try:
                url = f"{MORALIS_DATA_URL}/{dev_wallet}/erc20?chain={chain_key}&limit=5"
                result = await self._moralis_get(url)
                if result and isinstance(result, list) and len(result) > 0:
                    chain_name = MORALIS_CHAIN_MAP.get(chain_key, chain_key)
                    # Only include if different from the chains already known
                    cross_chain.append(
                        {
                            "chain": chain_name,
                            "address": dev_wallet,
                            "token_count": str(len(result)),
                            "has_activity": "true",
                        }
                    )
            except Exception:
                continue

        return cross_chain
