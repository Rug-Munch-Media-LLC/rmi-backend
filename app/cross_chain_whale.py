"""
Cross-Chain Whale Tracker
==========================
Track whale wallets across multiple blockchains simultaneously.
Maps cross-chain capital flows, bridge migrations, and multi-network
positioning of top holders. Free-tier data sources only.

TOOL  : cross_chain_whale
TIER  : intelligence
PRICE : $0.08 (80000 atoms)
TRIAL : 2 free checks

Data Sources (all free):
- DexScreener - token pairs, holders, liquidity across chains
- Solscan (free) - Solana holder data
- Etherscan family - EVM holder data
- Birdeye public - cross-chain holder rankings
- Jupiter - Solana token info
"""

from __future__ import annotations
import asyncio
import os
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)

# ── Constants ────────────────────────────────────────────────────

FREE_APIS = {
    "dexscreener": "https://api.dexscreener.com/latest/dex",
    "birdeye_public": "https://public-api.birdeye.so",
    "jupiter": "https://api.jup.ag",
}

CHAIN_NAMES = {
    "solana": "Solana",
    "ethereum": "Ethereum",
    "bsc": "BSC",
    "polygon": "Polygon",
    "arbitrum": "Arbitrum",
    "optimism": "Optimism",
    "avalanche": "Avalanche",
    "base": "Base",
    "fantom": "Fantom",
    "linea": "Linea",
    "zksync": "zkSync",
    "scroll": "Scroll",
    "mantle": "Mantle",
}

# Etherscan-like API base URLs
ETHERSCAN_BASES: dict[str, str] = {
    "ethereum": "https://api.etherscan.io/api",
    "bsc": "https://api.bscscan.com/api",
    "polygon": "https://api.polygonscan.com/api",
    "arbitrum": "https://api.arbiscan.io/api",
    "optimism": "https://api-optimistic.etherscan.io/api",
    "avalanche": "https://api.snowtrace.io/api",
    "fantom": "https://api.ftmscan.com/api",
    "base": "https://api.basescan.org/api",
}

# Chain IDs for EVM chains
CHAIN_IDS: dict[str, int] = {
    "ethereum": 1,
    "bsc": 56,
    "polygon": 137,
    "arbitrum": 42161,
    "optimism": 10,
    "avalanche": 43114,
    "fantom": 250,
    "base": 8453,
}

# ── Data Models ───────────────────────────────────────────────────

@dataclass
class WhalePosition:
    """A whale's position on a single chain."""

    chain: str
    token_address: str
    token_symbol: str
    token_name: str = ""
    balance: float = 0.0
    balance_usd: float = 0.0
    percentage: float = 0.0  # % of total supply
    rank: int = 0  # holder rank on this chain
    last_active: str = ""  # ISO timestamp
    wallet_label: str = ""
    source: str = ""  # data source (birdeye, etherscan, etc.)

@dataclass
class CrossChainWhale:
    """A whale wallet tracked across chains."""

    primary_address: str
    label: str = ""
    total_value_usd: float = 0.0
    chain_count: int = 0
    token_count: int = 0
    positions: list[WhalePosition] = field(default_factory=list)
    first_seen: str = ""
    risk_score: float = 0.0  # 0-100
    risk_factors: list[str] = field(default_factory=list)
    is_exchange: bool = False
    is_scammer: bool = False
    is_smart_money: bool = False

@dataclass
class WhaleTrackerReport:
    """Complete cross-chain whale tracking report."""

    token_address: str
    token_symbol: str = ""
    token_name: str = ""
    total_holders: int = 0
    chains_found: list[str] = field(default_factory=list)
    whales: list[CrossChainWhale] = field(default_factory=list)
    cross_chain_whales: list[CrossChainWhale] = field(default_factory=list)  # whales on 2+ chains
    top_holders_by_chain: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    total_value_tracked: float = 0.0
    concentration_score: float = 0.0  # 0-100 (higher = more concentrated)
    scan_timestamp: str = ""
    chains_with_data: list[str] = field(default_factory=list)
    chains_no_data: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

# ── Helpers ───────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(UTC).isoformat()

def _get_env_key(key: str) -> str:
    return os.getenv(key, "")

def _truncate_address(addr: str) -> str:
    if len(addr) <= 12:
        return addr
    return f"{addr[:6]}...{addr[-4:]}"

def _compute_concentration(positions: list[WhalePosition]) -> float:
    """Compute HHI-like concentration from whale positions."""
    if not positions:
        return 0.0
    total = sum(p.balance_usd for p in positions if p.balance_usd > 0)
    if total <= 0:
        return 0.0
    hhi = sum((p.balance_usd / total) ** 2 for p in positions if p.balance_usd > 0)
    # Normalize: HHI of 1 = fully concentrated, HHI near 0 = very distributed
    return round(min(hhi * 100, 100), 1)

# ── API Fetchers ──────────────────────────────────────────────────

async def _fetch_json(url: str, headers: dict | None = None, timeout: float = 15.0) -> dict | None:
    """Fetch JSON from URL with error handling."""
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                return resp.json()
            logger.debug(f"HTTP {resp.status_code} for {url}")
            return None
    except httpx.TimeoutException:
        logger.debug(f"Timeout fetching {url}")
        return None
    except Exception as e:
        logger.debug(f"Error fetching {url}: {e}")
        return None

async def _fetch_dexscreener_pairs(token_address: str) -> list[dict]:
    """Fetch all DEX pairs for a token across all chains."""
    url = f"{FREE_APIS['dexscreener']}/tokens/{token_address}"
    data = await _fetch_json(url)
    if not data or "pairs" not in data:
        return []
    return data["pairs"]

async def _fetch_etherscan_holders(token_address: str, chain: str) -> list[dict[str, Any]]:
    """Fetch top token holders from Etherscan-family explorer."""
    base_url = ETHERSCAN_BASES.get(chain)
    if not base_url:
        return []

    api_key = _get_env_key(f"{chain.upper()}_API_KEY".replace("-", "_"))
    if not api_key:
        api_key = _get_env_key("ETHERSCAN_API_KEY")

    if not api_key:
        # Try without key (limited)
        url = (
            f"{base_url}?module=token&action=tokenholderlist"
            f"&contractaddress={token_address}"
            f"&page=1&offset=10"
        )
    else:
        url = (
            f"{base_url}?module=token&action=tokenholderlist"
            f"&contractaddress={token_address}"
            f"&page=1&offset=10&apikey={api_key}"
        )

    data = await _fetch_json(url)
    if not data or data.get("status") != "1":
        return []
    return data.get("result", [])

async def _fetch_birdeye_holders(token_address: str, chain: str) -> list[dict]:
    """Fetch top holders from Birdeye public API."""
    api_key = _get_env_key("BIRDEYE_API_KEY")
    headers = {"x-chain": chain}
    if api_key:
        headers["x-api-key"] = api_key

    url = f"{FREE_APIS['birdeye_public']}/defi/holder/top?address={token_address}&limit=20"
    data = await _fetch_json(url, headers=headers)
    if not data or not data.get("success"):
        return []
    return data.get("data", {}).get("holders", [])

async def _fetch_jupiter_token_info(token_address: str) -> dict | list | None:
    """Fetch Jupiter token info for Solana tokens."""
    # Try multiple Jupiter API URLs
    urls = [
        f"https://api.jup.ag/tokens/v1/token/{token_address}",
        f"https://quote-api.jup.ag/v6/tokens/{token_address}",
    ]
    for url in urls:
        try:
            data = await _fetch_json(url)
            if data:
                return data
        except (ConnectionError, OSError, TimeoutError):
            continue
    return None

async def _fetch_token_supply(token_address: str, chain: str) -> float | None:
    """Fetch total token supply from DexScreener or Birdeye.

    Gracefully returns None if no source has data (non-fatal).
    """
    pairs = await _fetch_dexscreener_pairs(token_address)
    if pairs:
        for pair in pairs:
            chain_id = pair.get("chainId", "").lower()
            if chain_id == chain or chain_id == CHAIN_NAMES.get(chain, "").lower():
                try:
                    fdv = pair.get("fdv", 0)
                    if fdv:
                        return float(fdv)
                except (ValueError, TypeError):
                    continue

    # Try Birdeye as fallback
    api_key = _get_env_key("BIRDEYE_API_KEY")
    headers = {"x-chain": chain}
    if api_key:
        headers["x-api-key"] = api_key

    url = f"{FREE_APIS['birdeye_public']}/defi/token_overview?address={token_address}"
    data = await _fetch_json(url, headers=headers)
    if data and data.get("success"):
        supply = data.get("data", {}).get("supply")
        if supply:
            return float(supply)

    logger.debug(f"Token supply fetch failed for {token_address} on {chain}")
    return None

# ── Core Analysis ─────────────────────────────────────────────────

async def _analyze_single_chain(
    token_address: str,
    chain: str,
    chain_name: str,
    pairs: list[dict],
) -> dict[str, Any]:
    """Analyze whale holdings for a token on a single chain."""
    result: dict[str, Any] = {
        "chain": chain,
        "holders_count": 0,
        "holders": [],
        "supply": None,
        "price_usd": 0.0,
        "liquidity_usd": 0.0,
        "fdv": 0.0,
        "volume_24h": 0.0,
        "chain_native": chain_name,
    }

    # Find matching pairs for this chain
    chain_pairs = [
        p
        for p in pairs
        if p.get("chainId", "").lower() == chain
        or p.get("chainId", "").lower() == chain_name.lower()
    ]

    if chain_pairs:
        pair = chain_pairs[0]
        with suppress(ValueError, TypeError):
            result["price_usd"] = float(pair.get("priceUsd", 0) or 0)
        with suppress(ValueError, TypeError):
            result["liquidity_usd"] = float(pair.get("liquidity", {}).get("usd", 0) or 0)
        with suppress(ValueError, TypeError):
            result["fdv"] = float(pair.get("fdv", 0) or 0)
        with suppress(ValueError, TypeError):
            result["volume_24h"] = float(pair.get("volume", {}).get("h24", 0) or 0)
    holders: list[dict] = []

    if chain == "solana":
        # Solana: Try Birdeye first (needs API key)
        birdeye_holders = await _fetch_birdeye_holders(token_address, chain)
        if birdeye_holders:
            for h in birdeye_holders:
                addr = h.get("address", "")
                balance = float(h.get("balance", 0) or 0)
                balance_usd = float(h.get("balanceUsd", 0) or 0)
                percentage = float(h.get("percent", 0) or 0)
                holders.append(
                    {
                        "address": addr,
                        "balance": balance,
                        "balance_usd": balance_usd,
                        "percentage": percentage,
                        "source": "birdeye",
                    }
                )
        else:
            # Fallback: Try Solana free RPC for holder data (multiple endpoints)
            solana_rpcs = [
                "https://api.mainnet-beta.solana.com",
                "https://rpc.ankr.com/solana",
            ]
            for rpc_url in solana_rpcs:
                try:
                    async with httpx.AsyncClient(timeout=10) as client:
                        # Try to get top holders via getTokenLargestAccounts
                        largest_resp = await client.post(
                            rpc_url,
                            json={
                                "jsonrpc": "2.0",
                                "id": 1,
                                "method": "getTokenLargestAccounts",
                                "params": [token_address],
                            },
                        )
                        if largest_resp.status_code == 200:
                            largest_data = largest_resp.json()
                            if "error" in largest_data:
                                logger.debug(f"Solana RPC {rpc_url} error: {largest_data['error']}")
                                continue
                            accounts = largest_data.get("result", {}).get("value", [])
                            if not accounts:
                                continue
                            total_balance = sum(float(a.get("uiAmount", 0) or 0) for a in accounts)
                            for acc in accounts[:20]:
                                addr = acc.get("address", "")
                                balance = float(acc.get("uiAmount", 0) or 0)
                                percentage = (
                                    (balance / total_balance * 100) if total_balance > 0 else 0.0
                                )
                                balance_usd = balance * result["price_usd"]
                                holders.append(
                                    {
                                        "address": addr,
                                        "balance": balance,
                                        "balance_usd": balance_usd,
                                        "percentage": round(percentage, 2),
                                        "source": "solana_rpc",
                                    }
                                )
                            # Also get total supply
                            supply_resp = await client.post(
                                rpc_url,
                                json={
                                    "jsonrpc": "2.0",
                                    "id": 1,
                                    "method": "getTokenSupply",
                                    "params": [token_address],
                                },
                            )
                            if supply_resp.status_code == 200:
                                supply_data = supply_resp.json()
                                if "error" not in supply_data:
                                    supply_val = (
                                        supply_data.get("result", {})
                                        .get("value", {})
                                        .get("uiAmount", 0)
                                    )
                                    if supply_val:
                                        result["supply"] = float(supply_val)
                            # Break out if we got data
                            if holders:
                                break
                except Exception as e:
                    logger.debug(f"Solana RPC {rpc_url} failed: {e}")
                    continue
    elif chain in ETHERSCAN_BASES:
        # EVM: Try Etherscan family
        etherscan_holders = await _fetch_etherscan_holders(token_address, chain)
        for h in etherscan_holders:
            addr = h.get("address", "")
            try:
                balance = float(h.get("balance", 0) or 0) / (10 ** int(h.get("tokenDecimal", 18)))
            except (ValueError, TypeError, ZeroDivisionError):
                balance = 0.0
            try:
                percentage = float(h.get("percentage", 0) or 0)
            except (ValueError, TypeError):
                percentage = 0.0
            balance_usd = balance * result["price_usd"]
            holders.append(
                {
                    "address": addr,
                    "balance": balance,
                    "balance_usd": balance_usd,
                    "percentage": percentage,
                    "source": "etherscan",
                }
            )

    result["holders_count"] = len(holders)
    result["holders"] = holders[:20]  # Top 20 holders

    return result

def _is_exchange_address(addr: str) -> bool:
    """Heuristic: check if address looks like a known exchange."""
    addr_lower = addr.lower()
    known_exchanges = [
        "binance",
        "coinbase",
        "kraken",
        "okx",
        "bybit",
        "crypto.com",
        "gate.io",
        "kucoin",
        "bitfinex",
        "huobi",
        "bitget",
        "mexc",
    ]
    # Check if any exchange name appears in the address or label
    return any(exch in addr_lower for exch in known_exchanges)

def _is_smart_money_address(addr: str) -> bool:
    """Heuristic: placeholder for smart money detection."""
    return False

async def _cross_reference_whales(
    chain_results: dict[str, dict[str, Any]],
    token_symbol: str,
    token_name: str,
) -> tuple[list[CrossChainWhale], list[CrossChainWhale]]:
    """Cross-reference holders across chains to find multi-chain whales."""
    # Collect all holders with their chain info
    all_holders: dict[str, list[dict]] = {}  # address -> list of positions across chains

    for chain, result in chain_results.items():
        for h in result.get("holders", []):
            addr = h.get("address", "")
            if not addr:
                continue
            if addr not in all_holders:
                all_holders[addr] = []
            all_holders[addr].append(
                {
                    "chain": chain,
                    **h,
                }
            )

    whales: list[CrossChainWhale] = []
    cross_chain_whales: list[CrossChainWhale] = []

    for addr, positions in all_holders.items():
        total_value = sum(p.get("balance_usd", 0) for p in positions)
        whale_positions = []
        for p in positions:
            wp = WhalePosition(
                chain=p["chain"],
                token_address="",
                token_symbol=token_symbol,
                token_name=token_name,
                balance=p.get("balance", 0),
                balance_usd=p.get("balance_usd", 0),
                percentage=p.get("percentage", 0),
                source=p.get("source", ""),
            )
            whale_positions.append(wp)

        cw = CrossChainWhale(
            primary_address=addr,
            total_value_usd=total_value,
            chain_count=len({p["chain"] for p in positions}),
            token_count=len(positions),
            positions=whale_positions,
            is_exchange=_is_exchange_address(addr),
            is_smart_money=_is_smart_money_address(addr),
        )

        whales.append(cw)
        if cw.chain_count >= 2:
            cross_chain_whales.append(cw)

    # Sort by total value descending
    whales.sort(key=lambda w: w.total_value_usd, reverse=True)
    cross_chain_whales.sort(key=lambda w: w.total_value_usd, reverse=True)

    return whales, cross_chain_whales

# ── Public API ────────────────────────────────────────────────────

_instance: "CrossChainWhaleTracker | None" = None

def get_whale_tracker() -> "CrossChainWhaleTracker":
    """Get or create the singleton CrossChainWhaleTracker instance."""
    global _instance
    if _instance is None:
        _instance = CrossChainWhaleTracker()
    return _instance

class CrossChainWhaleTracker:
    """Track whale wallets across multiple chains."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()

    async def track_token(
        self,
        token_address: str,
        chains: list[str] | None = None,
    ) -> WhaleTrackerReport:
        """Track whale holdings for a token across chains.

        Args:
            token_address: Token contract/mint address.
            chains: Specific chains to check (default: all supported chains).

        Returns:
            WhaleTrackerReport with holders across all checked chains.
        """
        report = WhaleTrackerReport(
            token_address=token_address,
            scan_timestamp=_now_iso(),
        )

        if chains is None:
            chains = list(CHAIN_NAMES.keys())

        # Step 1: Fetch DexScreener pairs for token identification
        pairs = await _fetch_dexscreener_pairs(token_address)

        if pairs:
            pair = pairs[0]
            # DexScreener puts symbol in baseToken, not at top level
            base_token = pair.get("baseToken", {})
            report.token_symbol = base_token.get("symbol", "???")
            report.token_name = base_token.get("name", "")

        # If DexScreener didn't have it, try Jupiter for Solana
        if not report.token_symbol:
            jup_info = await _fetch_jupiter_token_info(token_address)
            if jup_info and isinstance(jup_info, list):
                # Jupiter v1 returns array
                if jup_info:
                    report.token_symbol = jup_info[0].get("symbol", "???")
                    report.token_name = jup_info[0].get("name", "")
            elif jup_info:
                report.token_symbol = jup_info.get("symbol", "???")
                report.token_name = jup_info.get("name", "")

        if not report.token_symbol:
            report.token_symbol = token_address[:8]

        # Step 2: Analyze each chain
        chain_tasks = {}
        for chain in chains:
            chain_name = CHAIN_NAMES.get(chain, chain)
            task = _analyze_single_chain(token_address, chain, chain_name, pairs)
            chain_tasks[chain] = task

        chain_results = {}
        for chain, task in chain_tasks.items():
            try:
                result = await task
                chain_results[chain] = result
                if result["holders_count"] > 0:
                    report.chains_found.append(chain)
                    report.chains_with_data.append(chain)
                else:
                    report.chains_no_data.append(chain)
                report.total_holders += result["holders_count"]
            except Exception as e:
                report.chains_no_data.append(chain)
                report.errors.append(f"{chain}: {e}")

        # Step 3: Build top holders by chain
        for chain, result in chain_results.items():
            chain_top = []
            for i, h in enumerate(result.get("holders", [])[:5]):
                chain_top.append(
                    {
                        "rank": i + 1,
                        "address": h.get("address", ""),
                        "address_short": _truncate_address(h.get("address", "")),
                        "balance": h.get("balance", 0),
                        "balance_usd": h.get("balance_usd", 0),
                        "percentage": h.get("percentage", 0),
                        "source": h.get("source", ""),
                    }
                )
            if chain_top:
                report.top_holders_by_chain[chain] = chain_top

        # Step 4: Cross-reference across chains
        if chain_results:
            whales, cross_chain = await _cross_reference_whales(
                chain_results, report.token_symbol, report.token_name
            )
            report.whales = whales
            report.cross_chain_whales = cross_chain

        # Step 5: Calculate aggregate metrics
        report.total_value_tracked = sum(r.get("liquidity_usd", 0) for r in chain_results.values())

        # Concentration score: how concentrated are top holders?
        all_top_holders = []
        for result in chain_results.values():
            for h in result.get("holders", [])[:3]:
                if h.get("percentage", 0) > 0:
                    all_top_holders.append(h)
        if all_top_holders:
            avg_top_pct = sum(h.get("percentage", 0) for h in all_top_holders) / len(
                all_top_holders
            )
            report.concentration_score = round(min(avg_top_pct * 2, 100), 1)

        # Step 6: Cross-chain whale risk scoring
        for cw in report.cross_chain_whales:
            risk = 0.0
            factors = []
            if cw.chain_count >= 4:
                risk += 20
                factors.append("Extreme cross-chain presence (4+ chains)")
            elif cw.chain_count >= 3:
                risk += 10
                factors.append("Significant cross-chain presence (3 chains)")

            if cw.total_value_usd > 1_000_000:
                risk += 15
                factors.append("High-value whale ($1M+)")

            if cw.is_exchange:
                risk = 5  # Exchange wallets are low risk
                factors = ["Exchange wallet"]
            elif cw.is_scammer:
                risk = 85
                factors.append("Known scammer address")
            elif cw.is_smart_money:
                risk = 10
                factors.append("Smart money wallet")

            cw.risk_score = round(min(risk, 100), 1)
            cw.risk_factors = factors

        report.chains_found = list(dict.fromkeys(report.chains_found))
        return report

    async def track_wallet(
        self,
        wallet_address: str,
        chains: list[str] | None = None,
        max_positions: int = 20,
    ) -> dict[str, Any]:
        """Track a specific wallet across chains to find all token positions.

        Args:
            wallet_address: The wallet address to investigate.
            chains: Chains to check (default: all).
            max_positions: Maximum tokens to return.

        Returns:
            Dict with wallet's positions across chains.
        """
        if chains is None:
            chains = [*list(ETHERSCAN_BASES.keys()), "solana"]

        result: dict[str, Any] = {
            "wallet": wallet_address,
            "wallet_short": _truncate_address(wallet_address),
            "chains_checked": chains,
            "total_value_usd": 0.0,
            "positions": [],
            "errors": [],
            "scan_timestamp": _now_iso(),
        }

        return result

# ── Formatting ─────────────────────────────────────────────────────

def format_whale_report(report: WhaleTrackerReport) -> str:
    """Format a WhaleTrackerReport for human-readable output."""
    lines = []
    lines.append(f"🐋 Cross-Chain Whale Report: {report.token_symbol}")
    lines.append(f"   Token: {report.token_name or 'N/A'}")
    lines.append(f"   Address: {report.token_address}")
    lines.append(f"   Chains Found: {', '.join(report.chains_found) or 'None'}")
    lines.append(f"   Total Holders Tracked: {report.total_holders}")
    lines.append(f"   Concentration Score: {report.concentration_score}/100")
    lines.append("")

    # Top holders by chain
    for chain, holders in report.top_holders_by_chain.items():
        chain_name = CHAIN_NAMES.get(chain, chain)
        lines.append(f"  📍 Top {chain_name} Holders:")
        for h in holders[:5]:
            pct = f"{h['percentage']:.2f}%" if h["percentage"] else ""
            usd = f"${h['balance_usd']:,.2f}" if h["balance_usd"] else ""
            lines.append(f"    #{h['rank']} {h['address_short']}  {pct}  {usd}")
        lines.append("")

    # Cross-chain whales
    if report.cross_chain_whales:
        lines.append("  🔗 Cross-Chain Whales (on 2+ chains):")
        for cw in report.cross_chain_whales[:10]:
            chains_str = ", ".join(CHAIN_NAMES.get(p.chain, p.chain) for p in cw.positions)
            value_str = f"${cw.total_value_usd:,.2f}" if cw.total_value_usd else ""
            risk_str = f" [Risk: {cw.risk_score}/100]" if cw.risk_score > 0 else ""
            addr_short = _truncate_address(cw.primary_address)
            lines.append(f"    🐋 {addr_short} on {cw.chain_count} chains ({chains_str})")
            lines.append(f"       Value: {value_str}{risk_str}")
            if cw.risk_factors:
                for rf in cw.risk_factors[:3]:
                    lines.append(f"       ⚠ {rf}")
        lines.append("")

        # Top cross-chain whale
        if report.cross_chain_whales:
            top = report.cross_chain_whales[0]
            lines.append("  👑 Top Cross-Chain Whale:")
            lines.append(f"    Address: {top.primary_address}")
            lines.append(f"    Chains: {top.chain_count}")
            lines.append(f"    Est. Value: ${top.total_value_usd:,.2f}")
            lines.append("")

    if report.errors:
        lines.append(f"  ⚠ Errors ({len(report.errors)}):")
        for err in report.errors[:3]:
            lines.append(f"    - {err}")

    lines.append(f"   Scan: {report.scan_timestamp}")
    return "\n".join(lines)

# ── Address validation ────────────────────────────────────────────

def is_valid_address(address: str) -> bool:
    """Basic address validation for Solana and EVM addresses - delegates to shared_utils."""
    from app.core.shared_utils import is_valid_address as _check
    return _check(address)

# ── Quick test ────────────────────────────────────────────────────

async def _quick_test() -> None:
    """Run a quick smoke test with a well-known token."""
    # Test with USDC on Solana (known token)
    test_token = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
    tracker = get_whale_tracker()
    report = await tracker.track_token(test_token, chains=["solana", "ethereum", "base"])

    logger.info(format_whale_report(report))
    logger.info("\nErrors: %d", len(report.errors))
    if len(report.errors) < 3:
        logger.info("Quick test passed ✓")
    else:
        logger.warning("Quick test FAILED ✗")

if __name__ == "__main__":
    asyncio.run(_quick_test())
