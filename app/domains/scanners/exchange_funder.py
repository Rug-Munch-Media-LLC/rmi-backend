"""
SENTINEL - Exchange-Funded Wallet Detection
==========================================
Identifies wallets funded from known CEX hot wallets.
Professional snipers create 10-50 fresh wallets, fund each from a CEX
to break on-chain link, then consolidate profits back through bridges or DEX swaps.

Key insight: wallets with no prior history whose first tx is a CEX withdrawal,
followed by immediate token purchase, are strong sniper signals.

Uses direct API calls: Solscan (account transfers, funding sources),
Helius (signatures, parsed transactions), Birdeye (token overview).
"""

import os
from collections import defaultdict
from dataclasses import dataclass, field

import httpx

from app.chain_client import ChainClient
from app.chain_registry import get_cex_wallets, is_solana
from app.core.logging import get_logger
from app.free_solscan_client import FreeSolscanClient, is_known_exchange

logger = get_logger("exchange_funder")


@dataclass
class ExchangeWallet:
    address: str
    exchange: str
    chain: str
    label: str = ""


@dataclass
class BuyerFundingInfo:
    buyer_address: str
    first_funding_source: str
    first_funding_label: str = ""
    is_cex_funded: bool = False
    is_exchange: str = ""  # "binance", "coinbase", etc.
    funding_hop_count: int = 0
    is_fresh_wallet: bool = False  # No prior history
    time_from_funding_to_buy_ms: int = 0  # How fast they bought after funding
    bought_same_block: bool = False


@dataclass
class ExchangeFundReport:
    token_address: str
    chain: str
    total_buyers: int
    cex_funded_count: int
    cex_funded_percentage: float
    fresh_wallet_count: int
    fresh_wallet_percentage: float
    instant_buy_count: int  # Funded and bought within 60 seconds
    instant_buy_percentage: float
    exchange_breakdown: dict[str, int]  # exchange_name -> count
    top_funders: list[tuple[str, int]]  # source_address -> count of wallets funded
    risk_score: int = 0  # 0-100
    risk_level: str = "LOW"
    warnings: list[str] = field(default_factory=list)
    buyer_details: list[BuyerFundingInfo] = field(default_factory=list)


class ExchangeFunderDetector:
    """Detects wallets funded from centralized exchanges.

    Fetches data directly from Solscan, Helius, and Birdeye.
    """

    # Dead/burn addresses for renounce verification
    DEAD_ADDRESSES = {  # noqa: RUF012
        "ethereum": [
            "0x000000000000000000000000000000000000dEaD",
            "0x0000000000000000000000000000000000000000",
        ],
        "bsc": [
            "0x000000000000000000000000000000000000dEaD",
            "0x0000000000000000000000000000000000000000",
        ],
        "base": [
            "0x000000000000000000000000000000000000dEaD",
            "0x0000000000000000000000000000000000000000",
        ],
    }

    def __init__(self):
        self._http = httpx.AsyncClient(timeout=15.0)
        self._chain = ChainClient()
        self._solscan = FreeSolscanClient()
        self._helius_key = os.getenv("HELIUS_API_KEY", "")

    # ── Direct API fetchers ───────────────────────────────────────────

    async def _fetch_solscan_funding_sources(self, wallet: str, days: int = 30) -> list[dict]:
        """Fetch funding sources for a wallet from Solscan."""
        try:
            sources = self._solscan.get_wallet_funding_sources(wallet, days=days)
            return sources or []
        except Exception as e:
            logger.warning(f"Solscan funding sources failed for {wallet}: {e}")
            return []

    async def _fetch_solscan_transfers(
        self, address: str, page: int = 1, page_size: int = 100
    ) -> list[dict]:
        """Fetch token transfers for an address from Solscan."""
        try:
            result = self._solscan.account_transfers(address, page=page, page_size=page_size)
            return result or []
        except Exception as e:
            logger.warning(f"Solscan transfers failed for {address}: {e}")
            return []

    async def _fetch_solscan_account_info(self, address: str) -> dict | None:
        """Fetch account info from Solscan."""
        try:
            return self._solscan.account_info(address)
        except Exception as e:
            logger.warning(f"Solscan account info failed for {address}: {e}")
            return None

    async def _fetch_helius_signatures(self, address: str, limit: int = 5) -> list[dict]:
        """Fetch first few signatures for a wallet to determine if it's fresh."""
        result = await self._chain.rpc_call("getSignaturesForAddress", [address, {"limit": limit}])
        if result and "result" in result:
            return result["result"]
        return []

    async def _fetch_dexscreener_boosts(self, token_address: str) -> dict | None:
        """Fetch DexScreener boosted token info (free, no key)."""
        try:
            resp = await self._http.get(
                f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"
            )
            if resp.status_code == 200:
                data = resp.json()
                pairs = data.get("pairs") or []
                return pairs[0] if pairs else None
        except Exception as e:
            logger.warning(f"DexScreener fetch failed for {token_address}: {e}")
        return None

    # ── Pure computation methods (unchanged) ──────────────────────────

    def identify_exchange(self, address: str, chain: str) -> str:
        """Check if an address is a known CEX hot wallet."""
        addr_lower = address.lower()

        # Check chain's CEX wallets from registry
        cex_wallets = get_cex_wallets(chain)
        for exchange_name, addresses in cex_wallets.items():
            if address in addresses or addr_lower in [a.lower() for a in addresses]:
                return exchange_name

        # Also check the Solscan known exchange list for Solana
        if is_solana(chain):
            result = is_known_exchange(address) or ""
            return result

        return ""

    def calculate_risk_score(self, report: ExchangeFundReport) -> tuple[int, str, list[str]]:
        """Calculate risk score based on CEX-funded wallet concentration."""
        score = 0
        warnings = []

        # CEX-funded concentration
        cex_pct = report.cex_funded_percentage
        if cex_pct > 50:
            score += 40
            warnings.append(
                f"CRITICAL: {cex_pct:.1f}% of buyers funded from CEX - likely coordinated snipers"
            )
        elif cex_pct > 30:
            score += 25
            warnings.append(f"HIGH: {cex_pct:.1f}% CEX-funded buyers - possible sniper operation")
        elif cex_pct > 15:
            score += 12
            warnings.append(f"MODERATE: {cex_pct:.1f}% CEX-funded - some sniper activity")

        # Fresh wallet percentage
        fresh_pct = report.fresh_wallet_percentage
        if fresh_pct > 40:
            score += 30
            warnings.append(
                f"CRITICAL: {fresh_pct:.1f}% of buyers are fresh wallets - classic sniper pattern"
            )
        elif fresh_pct > 20:
            score += 15
            warnings.append(f"MODERATE: {fresh_pct:.1f}% fresh wallets")

        # Instant buy percentage (funded → bought within 60s)
        instant_pct = report.instant_buy_percentage
        if instant_pct > 30:
            score += 25
            warnings.append(f"HIGH: {instant_pct:.1f}% bought within 60s of funding - bot behavior")
        elif instant_pct > 10:
            score += 10

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

    async def analyze(
        self, token_address: str, chain: str, buyer_addresses: list[str] | None = None
    ) -> ExchangeFundReport:
        """Full exchange-funded analysis for a token's early buyers.

        If buyer_addresses are not provided, attempts to derive them from
        Solscan token holder data and Helius transaction history.
        """
        # If no buyer addresses provided, try to fetch from Solscan (Solana only)
        if not buyer_addresses:
            buyer_addresses = []
            if is_solana(chain):
                # Get top holders from Solscan as proxy for early buyers
                try:
                    holders = self._solscan.get_holder_wallets(token_address, top_n=20)
                    if holders:
                        buyer_addresses = [
                            h.get("address", h.get("owner", ""))
                            for h in holders
                            if h.get("address") or h.get("owner")
                        ]
                        # Filter out empty strings
                        buyer_addresses = [a for a in buyer_addresses if a]
                except Exception as e:
                    logger.warning(f"Could not fetch holder wallets for {token_address}: {e}")

        total = len(buyer_addresses) or 1

        # Classify each buyer using direct API calls
        buyer_details: list[BuyerFundingInfo] = []
        cex_count = 0
        fresh_count = 0
        instant_count = 0
        exchange_counts: dict[str, int] = defaultdict(int)
        funder_counts: dict[str, int] = defaultdict(int)

        for buyer in buyer_addresses:
            info = BuyerFundingInfo(buyer_address=buyer, first_funding_source="unknown")

            if is_solana(chain):
                # Trace funding sources via Solscan
                funding_sources = await self._fetch_solscan_funding_sources(buyer, days=30)
                if funding_sources:
                    first_funding = funding_sources[0] if funding_sources else {}
                    source_addr = first_funding.get("from", "")
                    info.first_funding_source = source_addr
                    info.funding_hop_count = len(funding_sources)

                    # Check if funding source is a known CEX
                    exchange = self.identify_exchange(source_addr, chain)
                    if exchange:
                        info.is_cex_funded = True
                        info.is_exchange = exchange
                        info.first_funding_label = exchange
                        cex_count += 1
                        exchange_counts[exchange] += 1
                        funder_counts[source_addr] += 1

                # Check if wallet is fresh (very few transactions)
                sigs = await self._fetch_helius_signatures(buyer, limit=3)
                if len(sigs) <= 2:
                    info.is_fresh_wallet = True
                    fresh_count += 1

            else:
                # EVM chains - check against known CEX wallets list
                # Fundsource tracing requires Etherscan API for EVM; use exchange lookup
                exchange = self.identify_exchange(buyer, chain)
                if exchange:
                    info.first_funding_source = buyer
                    info.is_cex_funded = True
                    info.is_exchange = exchange
                    info.first_funding_label = exchange
                    cex_count += 1
                    exchange_counts[exchange] += 1
                    funder_counts[buyer] += 1

            buyer_details.append(info)

        # Calculate percentages
        cex_pct = (cex_count / total * 100) if total else 0
        fresh_pct = (fresh_count / total * 100) if total else 0
        instant_pct = (instant_count / total * 100) if total else 0

        # Top funders (source address → number of wallets funded)
        top_funders = sorted(funder_counts.items(), key=lambda x: x[1], reverse=True)[:10]

        # Build report
        report = ExchangeFundReport(
            token_address=token_address,
            chain=chain,
            total_buyers=len(buyer_addresses),
            cex_funded_count=cex_count,
            cex_funded_percentage=round(cex_pct, 2),
            fresh_wallet_count=fresh_count,
            fresh_wallet_percentage=round(fresh_pct, 2),
            instant_buy_count=instant_count,
            instant_buy_percentage=round(instant_pct, 2),
            exchange_breakdown=dict(exchange_counts),
            top_funders=top_funders,
            buyer_details=buyer_details,
        )

        # Calculate risk
        report.risk_score, report.risk_level, report.warnings = self.calculate_risk_score(report)

        return report
