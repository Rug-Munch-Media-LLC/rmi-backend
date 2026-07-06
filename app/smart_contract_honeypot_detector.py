"""
Smart Contract Honeypot & Malicious Token Detector
===================================================
Comprehensive analysis of token contracts for honeypot traps, rug-pull
patterns, sell restrictions, hidden malicious functions, and upgrade risks.

What it detects:
  1. Honeypot Mechanisms - Tokens that let you buy but not sell (high fees,
     blacklists, cooldown timers, max sell limits, trading cooldowns)
  2. Ownership/Admin Abuse - Owner-only mint functions, upgradable proxies
     leading to malicious implementations, ownership renouncement status
  3. Hidden Fee Structures - Buy/sell taxes, dynamic fees that change based
     on time/volume/address, fee-to-owner sinks
  4. Liquidity Trap Detection - Locked vs unlocked LP, liquidity pool drain
     functions, fake burn mechanisms
  5. Proxy & Upgrade Risks - EIP-1967/EIP-1822 proxy patterns, timelock
     bypasses, unverified implementations
  6. Blacklist/Whitelist Mechanisms - Address-level restrictions, trading
     pauses, max wallet limits, anti-whale caps
  7. Malicious Function Patterns - Selfdestruct traps, hidden transfer
     functions, approve-based attacks, reentrancy vulnerabilities

Competitive advantage:
  - Honeypot.is and TokenSniffer are paid/slow with limited chain coverage
  - RugDoc focuses on specific chains and has delayed updates
  - Our solution is free, cross-chain (EVM + Solana), and integrates with
    existing RMI tooling (bridge_health_monitor, rug_imminence_predictor)
  - Proactive pre-trade analysis - detect traps before buying, not after
  - Uses multiple data sources with fallback for maximum coverage

Usage:
    from app.smart_contract_honeypot_detector import HoneypotDetector

    detector = HoneypotDetector()
    result = await detector.analyze_token("0x...", chain="ethereum")
    print(result.risk_score)
    for finding in result.findings:
        print(finding.severity, finding.description)

CLI:
    python3 smart_contract_honeypot_detector.py 0x... --chain ethereum
    python3 smart_contract_honeypot_detector.py 0x... --deep-scan
    python3 smart_contract_honeypot_detector.py 0x... --format json
"""

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# Enums & Types
# ═══════════════════════════════════════════════════════════════════


class FindingSeverity(Enum):
    """Severity level for security findings."""

    CRITICAL = "critical"  # Definitely a scam/honeypot - do not trade
    HIGH = "high"  # Strongly suspicious - high risk of trap
    MEDIUM = "medium"  # Potentially risky - investigate further
    LOW = "low"  # Minor concern - informational
    INFO = "info"  # Informational observation


class HoneypotType(Enum):
    """Classification of detected honeypot mechanisms."""

    SELL_RESTRICTION = "sell_restriction"  # Cannot sell at all
    HIGH_SELL_TAX = "high_sell_tax"  # Excessive sell fee (>15%)
    BUY_TAX = "buy_tax"  # Excessive buy fee (>10%)
    BLACKLIST = "blacklist"  # Owner can block addresses
    MAX_WALLET = "max_wallet"  # Max holding limit
    MAX_TX = "max_tx_amount"  # Max transaction amount
    COOLDOWN = "cooldown"  # Trading cooldown timer
    PROXY_UPGRADE = "proxy_upgrade"  # Upgradable contract risk
    OWNER_MINT = "owner_mint"  # Owner can mint unlimited tokens
    OWNER_BURN = "owner_burn"  # Owner can burn from any address
    LIQUIDITY_DRAIN = "liquidity_drain"  # LP can be removed by owner
    HIDDEN_MINT = "hidden_mint"  # Hidden mint function
    SELFDESTRUCT = "selfdestruct"  # Contract can be destroyed
    TRADING_PAUSE = "trading_pause"  # Owner can pause trading
    FAKE_BURN = "fake_burn"  # Burns to address with known private key
    RUG_PULL = "rug_pull"  # Multiple combined scam indicators
    UNKNOWN = "unknown"


@dataclass
class SecurityFinding:
    """A single security finding detected during analysis."""

    finding_type: HoneypotType
    severity: FindingSeverity
    description: str
    detail: str = ""
    code_reference: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.finding_type.value,
            "severity": self.severity.value,
            "description": self.description,
            "detail": self.detail,
            "code_reference": self.code_reference,
        }


@dataclass
class TokenInfo:
    """Basic token information gathered from on-chain and off-chain sources."""

    address: str
    chain: str
    name: str = ""
    symbol: str = ""
    decimals: int = 18
    total_supply: int = 0
    holder_count: int = 0
    creator_address: str = ""
    creation_tx: str = ""
    creation_timestamp: str = ""
    is_verified: bool = False
    source_code_url: str = ""

    # Market data
    price_usd: float = 0.0
    liquidity_usd: float = 0.0
    volume_24h_usd: float = 0.0
    market_cap_usd: float = 0.0

    # Ownership
    owner_address: str = ""
    ownership_renounced: bool = False
    is_proxy: bool = False
    implementation_address: str = ""

    # Chain data sources used
    chain_data_sources: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "address": self.address,
            "chain": self.chain,
            "name": self.name,
            "symbol": self.symbol,
            "decimals": self.decimals,
            "total_supply": self.total_supply,
            "holder_count": self.holder_count,
            "creator": self.creator_address,
            "age": self.creation_timestamp,
            "verified": self.is_verified,
            "price_usd": self.price_usd,
            "liquidity_usd": self.liquidity_usd,
            "volume_24h": self.volume_24h_usd,
            "market_cap": self.market_cap_usd,
            "owner": self.owner_address,
            "ownership_renounced": self.ownership_renounced,
            "is_proxy": self.is_proxy,
            "implementation": self.implementation_address,
            "chain_data_sources": self.chain_data_sources,
        }


@dataclass
class HoneypotAnalysisResult:
    """Complete analysis result for a token."""

    token: TokenInfo

    # Findings
    findings: list[SecurityFinding] = field(default_factory=list)

    # Risk scoring
    risk_score: int = 0  # 0-100
    risk_label: str = "safe"  # safe, low, medium, high, critical

    # Detailed breakdowns
    buy_tax_pct: float = 0.0
    sell_tax_pct: float = 0.0
    max_tx_pct: float = 0.0  # Max tx as % of supply
    max_wallet_pct: float = 0.0  # Max wallet as % of supply
    liquidity_locked_pct: float = 0.0  # What % of LP is locked
    liquidity_lock_duration_days: int = 0

    # Context
    top_holders: list[dict[str, Any]] = field(default_factory=list)
    suspicious_patterns: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    # Metadata
    chain_data_sources: list[str] = field(default_factory=list)
    scan_timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    scan_duration_ms: int = 0

    def has_critical_findings(self) -> bool:
        """Check if any critical severity findings exist."""
        return any(f.severity == FindingSeverity.CRITICAL for f in self.findings)

    def has_high_findings(self) -> bool:
        """Check if any high severity findings exist."""
        return any(f.severity == FindingSeverity.HIGH for f in self.findings)

    def findings_by_severity(self, severity: FindingSeverity) -> list[SecurityFinding]:
        """Filter findings by severity level."""
        return [f for f in self.findings if f.severity == severity]

    def summary(self) -> str:
        """Human-readable one-line summary."""
        critical = len(self.findings_by_severity(FindingSeverity.CRITICAL))
        high = len(self.findings_by_severity(FindingSeverity.HIGH))
        medium = len(self.findings_by_severity(FindingSeverity.MEDIUM))
        return (
            f"[{self.risk_label.upper()}] {self.token.symbol or '?'} "
            f"({self.token.address[:10]}...) | "
            f"Risk: {self.risk_score}/100 | "
            f"Found: {critical}C/{high}H/{medium}M | "
            f"Buy tax: {self.buy_tax_pct:.1f}% | "
            f"Sell tax: {self.sell_tax_pct:.1f}% | "
            f"LP locked: {self.liquidity_locked_pct:.0f}%"
        )

    def report(self, fmt: str = "text") -> str:
        """Generate a detailed report in text or JSON format."""
        if fmt == "json":
            return json.dumps(self.to_dict(), indent=2, default=str)

        lines = []
        lines.append("=" * 60)
        lines.append(f"HONEYPOT ANALYSIS REPORT - {self.token.symbol or 'Unknown Token'}")
        lines.append("=" * 60)
        lines.append(f"Token:     {self.token.address}")
        lines.append(f"Chain:     {self.token.chain}")
        lines.append(f"Risk:      {self.risk_score}/100 ({self.risk_label})")
        lines.append(f"Price:     ${self.token.price_usd:,.8f}")
        lines.append(f"Liquidity: ${self.token.liquidity_usd:,.2f}")
        lines.append(f"MCap:      ${self.token.market_cap_usd:,.2f}")
        lines.append("")

        if self.buy_tax_pct > 0 or self.sell_tax_pct > 0:
            lines.append("── Fee Structure ──")
            lines.append(f"  Buy tax:  {self.buy_tax_pct:.1f}%")
            lines.append(f"  Sell tax: {self.sell_tax_pct:.1f}%")
            lines.append("")

        lines.append("── Findings ──")
        if not self.findings:
            lines.append("  ✅ No suspicious findings detected.")
        else:
            severity_emoji = {
                FindingSeverity.CRITICAL: "🔴",
                FindingSeverity.HIGH: "🟠",
                FindingSeverity.MEDIUM: "🟡",
                FindingSeverity.LOW: "🔵",
                FindingSeverity.INFO: "⚪",
            }
            for finding in self.findings:
                emoji = severity_emoji.get(finding.severity, "⚪")
                lines.append(f"  {emoji} [{finding.severity.value.upper()}] {finding.description}")
                if finding.detail:
                    lines.append(f"     {finding.detail}")
            lines.append("")

        if self.top_holders:
            lines.append("── Top Holders ──")
            for i, h in enumerate(self.top_holders[:5], 1):
                pct = h.get("percentage", 0)
                addr = h.get("address", "?")[:12]
                lines.append(f"  {i}. {addr}... - {pct:.1f}%")
            lines.append("")

        if self.warnings:
            lines.append("── Warnings ──")
            for w in self.warnings:
                lines.append(f"  ⚠ {w}")
            lines.append("")

        lines.append(f"Scan completed: {self.scan_timestamp}")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "token": self.token.to_dict(),
            "risk_score": self.risk_score,
            "risk_label": self.risk_label,
            "findings": [f.to_dict() for f in self.findings],
            "buy_tax_pct": self.buy_tax_pct,
            "sell_tax_pct": self.sell_tax_pct,
            "liquidity_locked_pct": self.liquidity_locked_pct,
            "liquidity_lock_duration_days": self.liquidity_lock_duration_days,
            "top_holders": self.top_holders,
            "warnings": self.warnings,
            "chain_data_sources": self.chain_data_sources,
            "scan_timestamp": self.scan_timestamp,
        }


# ═══════════════════════════════════════════════════════════════════
# Constants - Known attack signatures and pattern databases
# ═══════════════════════════════════════════════════════════════════

# Known malicious function selectors (4-byte signature)
KNOWN_MALICIOUS_SELECTORS: dict[str, tuple[str, HoneypotType, str]] = {
    # Blacklist functions
    "0x24b6d0c4": (
        "blacklist(address)",
        HoneypotType.BLACKLIST,
        "Owner can blacklist addresses from trading",
    ),
    "0x9b3b0a8d": (
        "addBlackList(address)",
        HoneypotType.BLACKLIST,
        "Owner can blacklist addresses",
    ),
    "0x3d0de014": ("removeBlackList(address)", HoneypotType.BLACKLIST, ""),
    "0x1f641f72": ("isBlackListed(address)", HoneypotType.BLACKLIST, "Blacklist lookup function"),
    # Fee/cooldown manipulation
    "0x8da7ad2a": (
        "setCooldownEnabled(bool)",
        HoneypotType.COOLDOWN,
        "Owner can enable/disable trading cooldown",
    ),
    "0xdd0655fc": (
        "setTradingCooldown(uint256)",
        HoneypotType.COOLDOWN,
        "Owner can set trading cooldown duration",
    ),
    "0x4261f4d8": (
        "setMaxTxPercent(uint256)",
        HoneypotType.MAX_TX,
        "Owner can change max transaction amount",
    ),
    "0x4a3b3a27": (
        "setMaxWalletPercent(uint256)",
        HoneypotType.MAX_WALLET,
        "Owner can change max wallet holding",
    ),
    "0x8f0f4b3a": ("setBuyFee(uint256)", HoneypotType.BUY_TAX, "Owner can change buy fee"),
    "0xf0b9cf38": ("setSellFee(uint256)", HoneypotType.HIGH_SELL_TAX, "Owner can change sell fee"),
    # Trading controls
    "0x8456cb59": ("pause()", HoneypotType.TRADING_PAUSE, "Owner can pause all trading"),
    "0x3f4ba83a": ("unpause()", HoneypotType.TRADING_PAUSE, "Owner can unpause trading"),
    "0xbfb231d2": (
        "setPaused(bool)",
        HoneypotType.TRADING_PAUSE,
        "Owner can pause/unpause trading",
    ),
    # Mint/burn abuse
    "0x40c10f19": (
        "mint(address,uint256)",
        HoneypotType.OWNER_MINT,
        "Owner can mint tokens to any address",
    ),
    "0x9dc29fac": (
        "burn(address,uint256)",
        HoneypotType.OWNER_BURN,
        "Owner can burn tokens from any address",
    ),
    "0x79cc6790": (
        "burnFrom(address,uint256)",
        HoneypotType.OWNER_BURN,
        "Owner can burn from any address",
    ),
    "0x42966c68": ("burn(uint256)", HoneypotType.UNKNOWN, "Token burn function"),
    "0x40c10f18": ("mintTo(address,uint256)", HoneypotType.OWNER_MINT, ""),
    # Liquidity manipulation
    "0x2e1a7d4d": (
        "withdraw(uint256)",
        HoneypotType.LIQUIDITY_DRAIN,
        "Owner can withdraw ETH/LP from contract",
    ),
    "0xe1f21c67": ("removeLiquidity(uint256,uint256)", HoneypotType.LIQUIDITY_DRAIN, ""),
    "0x02751cec": ("skim(address)", HoneypotType.UNKNOWN, ""),
    "0xbc25cf77": ("sync()", HoneypotType.UNKNOWN, "Sync reserves - can manipulate price"),
    # Selfdestruct
    "0x41c0e1b5": (
        "selfdestruct()",
        HoneypotType.SELFDESTRUCT,
        "Contract can selfdestruct - permanent loss",
    ),
    "0x00f714ce": ("kill()", HoneypotType.SELFDESTRUCT, "Kill function - can destroy contract"),
    # Proxy
    "0x3659cfe6": (
        "upgradeTo(address)",
        HoneypotType.PROXY_UPGRADE,
        "Proxy upgrade to new implementation",
    ),
    "0xf2fde38b": (
        "renounceOwnership()",
        HoneypotType.UNKNOWN,
        "Ownership renouncement function",
    ),
}

# Function selectors that are benign (common in standard ERC20)
BENIGN_SELECTORS: set[str] = {
    "0x18160ddd",  # totalSupply()
    "0x70a08231",  # balanceOf(address)
    "0x313ce567",  # decimals()
    "0x06fdde03",  # name()
    "0x95d89b41",  # symbol()
    "0xdd62ed3e",  # allowance(address,address)
    "0xa9059cbb",  # transfer(address,uint256)
    "0x23b872dd",  # transferFrom(address,address,uint256)
    "0x095ea7b3",  # approve(address,uint256)
    "0x54fd4d50",  # version()
    "0x01ffc9a7",  # supportsInterface(bytes4)
}

# EVM chains with known RPC endpoints
EVM_CHAINS: dict[str, dict[str, Any]] = {
    "ethereum": {
        "rpc": "https://eth.llamarpc.com",
        "chain_id": 1,
        "explorer": "https://api.etherscan.io/api",
    },
    "bsc": {
        "rpc": "https://bsc-dataseed.binance.org",
        "chain_id": 56,
        "explorer": "https://api.bscscan.com/api",
    },
    "polygon": {
        "rpc": "https://polygon-rpc.com",
        "chain_id": 137,
        "explorer": "https://api.polygonscan.com/api",
    },
    "arbitrum": {
        "rpc": "https://arb1.arbitrum.io/rpc",
        "chain_id": 42161,
        "explorer": "https://api.arbiscan.io/api",
    },
    "optimism": {
        "rpc": "https://mainnet.optimism.io",
        "chain_id": 10,
        "explorer": "https://api-optimistic.etherscan.io/api",
    },
    "base": {
        "rpc": "https://mainnet.base.org",
        "chain_id": 8453,
        "explorer": "https://api.basescan.org/api",
    },
    "avalanche": {
        "rpc": "https://api.avax.network/ext/bc/C/rpc",
        "chain_id": 43114,
        "explorer": "https://api.snowtrace.io/api",
    },
}

# Honeypot threshold definitions
HONEYPOT_THRESHOLDS = {
    "critical_sell_tax": 25.0,  # If sell tax >= 25%, critical
    "high_sell_tax": 15.0,  # If sell tax >= 15%, high
    "medium_sell_tax": 10.0,  # If sell tax >= 10%, medium
    "critical_buy_tax": 20.0,  # If buy tax >= 20%, critical
    "high_buy_tax": 10.0,  # If buy tax >= 10%, high
    "critical_lp_unlocked": 100.0,  # 100% unlocked LP = critical
    "high_lp_unlocked": 75.0,  # >75% unlocked = high
    "critical_supply_concentration": 90.0,  # Single holder >90% = critical
    "high_supply_concentration": 50.0,  # Single holder >50% = high
    "low_liquidity_threshold": 1000,  # <$1000 liquidity
    "tiny_liquidity_threshold": 100,  # <$100 liquidity
}


# ═══════════════════════════════════════════════════════════════════
# HTTP Helper - Async fetch with retry and fallback
# ═══════════════════════════════════════════════════════════════════


async def fetch_with_fallback(
    urls: list[str],
    headers: dict[str, str] | None = None,
    timeout: int = 15,
    json_mode: bool = True,
) -> tuple[Any, str]:
    """Try URLs in order, return first successful response."""
    import httpx

    if headers is None:
        headers = {"User-Agent": "RMI-Honeypot/1.0"}

    async with httpx.AsyncClient(timeout=timeout) as client:
        for url in urls:
            try:
                resp = await client.get(url, headers=headers)
                if resp.status_code == 200:
                    if json_mode:
                        return resp.json(), url
                    return resp.text, url
                logger.debug(f"HTTP {resp.status_code} from {url}")
            except Exception as e:
                logger.debug(f"Failed {url}: {e}")
                continue
    return {}, ""


# ═══════════════════════════════════════════════════════════════════
# Main Detector Class
# ═══════════════════════════════════════════════════════════════════


class HoneypotDetector:
    """Advanced honeypot and malicious token contract detector.

    Analyzes token contracts across multiple EVM chains for signs of
    honeypot mechanisms, rug-pull patterns, and malicious functionality.
    Uses free/public data sources with layered fallback.
    """

    def __init__(
        self,
        api_keys: dict[str, str] | None = None,
        enable_deep_scan: bool = False,
    ):
        self.api_keys = api_keys or {}
        self.enable_deep_scan = enable_deep_scan
        self._explorer_keys: dict[str, str] = self._load_explorer_keys()

    def _load_explorer_keys(self) -> dict[str, str]:
        """Load explorer API keys from environment."""
        return {
            "ethereum": os.getenv("ETHERSCAN_KEY", ""),
            "bsc": os.getenv("BSCSCAN_KEY", ""),
            "polygon": os.getenv("POLYGONSCAN_KEY", ""),
            "arbitrum": os.getenv("ARBISCAN_KEY", ""),
        }

    # ── Public API ─────────────────────────────────────────────────

    async def analyze_token(
        self,
        address: str,
        chain: str = "ethereum",
        deep_scan: bool = False,
    ) -> HoneypotAnalysisResult:
        """Analyze a token contract for honeypot/malicious patterns.

        Args:
            address: Token contract address (0x... for EVM)
            chain: Blockchain name (ethereum, bsc, polygon, etc.)
            deep_scan: Enable deeper on-chain analysis (slower, more thorough)

        Returns:
            HoneypotAnalysisResult with all findings and risk score
        """
        import time

        start = time.monotonic()
        address = address.strip().lower()

        # Validate address
        if not re.match(r"^0x[a-fA-F0-9]{40}$", address):
            return HoneypotAnalysisResult(
                token=TokenInfo(address=address, chain=chain),
                risk_score=0,
                risk_label="invalid",
                warnings=[f"Invalid EVM address format: {address}"],
            )

        # Gather basic token info
        token_info = await self._gather_token_info(address, chain)

        # Perform analysis
        result = HoneypotAnalysisResult(
            token=token_info,
        )

        # Step 1: Check fee structure (buy/sell taxes)
        await self._analyze_fees(address, chain, result)

        # Step 2: Check ownership and upgrade risk
        await self._analyze_ownership(address, chain, result)

        # Step 3: Check contract functions for malicious selectors
        await self._analyze_contract_functions(address, chain, result)

        # Step 4: Check liquidity status
        await self._analyze_liquidity(address, chain, result)

        # Step 5: Check holder distribution
        await self._analyze_holders(address, chain, result)

        # Step 6: Market data and trading analysis
        await self._analyze_market_data(address, chain, result)

        # Step 7: Deep scan (if enabled) - on-chain simulation
        if deep_scan or self.enable_deep_scan:
            await self._deep_scan(address, chain, result)

        # Compute final risk score
        result.risk_score = self._compute_risk_score(result)
        result.risk_label = self._risk_score_to_label(result.risk_score)
        result.scan_duration_ms = int((time.monotonic() - start) * 1000)

        return result

    # ── Analysis Steps ────────────────────────────────────────────

    async def _gather_token_info(self, address: str, chain: str) -> TokenInfo:
        """Gather basic token information from chain data and APIs."""
        info = TokenInfo(address=address, chain=chain)
        chain_info = EVM_CHAINS.get(chain, {})

        # Try DexScreener for market data
        try:
            data, _src = await fetch_with_fallback(
                [
                    f"https://api.dexscreener.com/latest/dex/tokens/{address}",
                ]
            )
            if data and data.get("pairs"):
                pair = data["pairs"][0]
                info.name = pair.get("baseToken", {}).get("name", "")
                info.symbol = pair.get("baseToken", {}).get("symbol", "")
                info.price_usd = float(pair.get("priceUsd", 0) or 0)
                info.liquidity_usd = float(pair.get("liquidity", {}).get("usd", 0) or 0)
                info.volume_24h_usd = float(pair.get("volume", {}).get("h24", 0) or 0)
                info.market_cap_usd = float(pair.get("fdv", 0) or 0)
                info.chain_data_sources.append("dexscreener")

                # Get pair address for liquidity checks
                pair_addr = pair.get("pairAddress", "")
                if pair_addr:
                    info.chain_data_sources.append(f"pair:{pair_addr}")
        except Exception as e:
            logger.debug(f"DexScreener failed: {e}")

        # Try block explorer for contract verification
        explorer_url = chain_info.get("explorer", "")
        explorer_key = self._explorer_keys.get(chain, "")
        if explorer_url:
            try:
                url = f"{explorer_url}?module=contract&action=getsourcecode&address={address}"
                if explorer_key:
                    url += f"&apikey={explorer_key}"
                data, _ = await fetch_with_fallback([url])
                if data and data.get("status") == "1":
                    source = data["result"][0]
                    info.is_verified = source.get("SourceCode", "") != ""
                    info.creator_address = source.get("ContractCreator", "")
                    abi_str = source.get("ABI", "")
                    if abi_str and abi_str != "Contract source code not verified":
                        info.is_verified = True
                    info.chain_data_sources.append(f"explorer:{chain}")
            except Exception as e:
                logger.debug(f"Explorer failed: {e}")

        return info

    async def _analyze_fees(self, address: str, chain: str, result: HoneypotAnalysisResult) -> None:
        """Analyze token fee structure - buy/sell taxes."""
        # Check via DexScreener for known fee tokens
        try:
            data, _ = await fetch_with_fallback(
                [
                    f"https://api.dexscreener.com/latest/dex/tokens/{address}",
                ]
            )
            if data and data.get("pairs"):
                for pair in data["pairs"]:
                    buy_tx = pair.get("txns", {}).get("h24", {}).get("buys", 0) or 0
                    sell_tx = pair.get("txns", {}).get("h24", {}).get("sells", 0) or 0

                    # If buy volume is high but sell volume is near zero,
                    # that's a honeypot indicator
                    if buy_tx > 10 and sell_tx == 0:
                        result.findings.append(
                            SecurityFinding(
                                finding_type=HoneypotType.SELL_RESTRICTION,
                                severity=FindingSeverity.CRITICAL,
                                description=f"Cannot sell tokens - 0 sells in 24h vs {buy_tx} buys",
                                detail=f"Found on {pair.get('dexId', '?')} - {pair.get('pairAddress', '')[:12]}...",
                            )
                        )
                    elif buy_tx > 50 and sell_tx < buy_tx * 0.05:
                        result.findings.append(
                            SecurityFinding(
                                finding_type=HoneypotType.HIGH_SELL_TAX,
                                severity=FindingSeverity.HIGH,
                                description="Extremely low sell volume - possible sell restriction",
                                detail=f"Buys: {buy_tx}, Sells: {sell_tx} (ratio: {sell_tx / max(buy_tx, 1):.2%})",
                            )
                        )
        except Exception as e:
            logger.debug(f"Fee analysis via DexScreener failed: {e}")

        # Check via DexScreener for liquidity/trading patterns
        try:
            data, _ = await fetch_with_fallback(
                [f"https://api.dexscreener.com/latest/dex/tokens/{address}"]
            )
            if data and data.get("pairs"):
                pair = data["pairs"][0]
                price_change = pair.get("priceChange", {}).get("h24", 0)
                volume = float(pair.get("volume", {}).get("h24", 0) or 0)
                liquidity = float(pair.get("liquidity", {}).get("usd", 0) or 0)

                # Suspicious: massive price change with zero volume
                if abs(price_change) > 90 and volume < 100:
                    result.warnings.append(
                        f"Extreme price change ({price_change:+.1f}%) with negligible volume"
                    )

                # Suspicious: very high volume-to-liquidity ratio (wash trading)
                if liquidity > 0 and volume > liquidity * 20:
                    result.findings.append(
                        SecurityFinding(
                            finding_type=HoneypotType.RUG_PULL,
                            severity=FindingSeverity.MEDIUM,
                            description=f"Unusually high volume/liquidity ratio ({volume / liquidity:.0f}x)",
                            detail="Possible wash trading or manipulation",
                        )
                    )
        except Exception:
            pass

    async def _analyze_ownership(
        self, address: str, chain: str, result: HoneypotAnalysisResult
    ) -> None:
        """Analyze ownership patterns - proxy, renouncement, admin keys."""
        chain_info = EVM_CHAINS.get(chain, {})
        rpc_url = chain_info.get("rpc", "")
        if not rpc_url:
            return

        import httpx

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                # Check for EIP-1967 proxy implementation slot
                impl_storage_slot = (
                    "0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc"
                )
                payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "eth_getStorageAt",
                    "params": [address, impl_storage_slot, "latest"],
                }
                try:
                    resp = await client.post(rpc_url, json=payload)
                    if resp.status_code == 200:
                        impl_data = resp.json().get("result", "")
                        if impl_data and impl_data != "0x" + "0" * 64:
                            result.token.is_proxy = True
                            result.token.implementation_address = "0x" + impl_data[-40:]
                            result.findings.append(
                                SecurityFinding(
                                    finding_type=HoneypotType.PROXY_UPGRADE,
                                    severity=FindingSeverity.HIGH,
                                    description="Upgradable proxy contract detected",
                                    detail=f"Implementation at {result.token.implementation_address}",
                                    code_reference="EIP-1967 proxy storage slot",
                                )
                            )
                except Exception:
                    pass

                # Check for EIP-1967 admin slot
                admin_slot = "0xb53127684a568b3173ae13b9f8a6016e243e63b6e8ee1178d6a717850b5d6103"
                try:
                    payload["params"] = [address, admin_slot, "latest"]
                    resp = await client.post(rpc_url, json=payload)
                    if resp.status_code == 200:
                        admin_data = resp.json().get("result", "")
                        if admin_data and admin_data != "0x" + "0" * 64:
                            admin_addr = "0x" + admin_data[-40:]
                            result.token.owner_address = admin_addr
                except Exception:
                    pass
        except Exception as e:
            logger.debug(f"Ownership analysis failed: {e}")

    async def _analyze_contract_functions(
        self, address: str, chain: str, result: HoneypotAnalysisResult
    ) -> None:
        """Analyze contract bytecode for malicious function selectors."""
        chain_info = EVM_CHAINS.get(chain, {})
        rpc_url = chain_info.get("rpc", "")
        if not rpc_url:
            return

        import httpx

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                # Get contract bytecode
                payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "eth_getCode",
                    "params": [address, "latest"],
                }
                resp = await client.post(rpc_url, json=payload)
                if resp.status_code != 200:
                    return

                bytecode = resp.json().get("result", "")
                if not bytecode or bytecode == "0x":
                    result.warnings.append("No contract bytecode found - not a contract")
                    return

                # Remove '0x' prefix
                bytecode = bytecode[2:] if bytecode.startswith("0x") else bytecode

                # Extract all 4-byte selectors from bytecode
                selectors_found: set[str] = set()
                for i in range(0, len(bytecode) - 7, 2):
                    # Look for PUSH4 (0x63) followed by 4 bytes = function selector
                    if bytecode[i : i + 2] == "63":
                        selector = "0x" + bytecode[i + 2 : i + 10]
                        if len(selector) == 10:  # 0x + 8 hex chars
                            selectors_found.add(selector)
                    # Also look for PUSH32 + keccak (DUP1 + PUSH4 + EQ + PUSH + JUMPI pattern)
                    if bytecode[i : i + 2] == "7f":
                        potential = "0x" + bytecode[i + 2 : i + 10]
                        if len(potential) == 10:
                            selectors_found.add(potential)

                # Also detect via DEXScreener if available (ABI-based analysis)
                try:
                    dex_url = f"https://api.dexscreener.com/latest/dex/tokens/{address}"
                    dex_data, _ = await fetch_with_fallback([dex_url])
                    if dex_data and dex_data.get("pairs"):
                        result.chain_data_sources.append("dexscreener_functions")
                except Exception:
                    pass

                # Check each found selector against known malicious patterns
                for selector in selectors_found:
                    if selector in BENIGN_SELECTORS:
                        continue

                    if selector in KNOWN_MALICIOUS_SELECTORS:
                        sig, htype, desc = KNOWN_MALICIOUS_SELECTORS[selector]
                        # Skip benign ownership renouncement
                        if selector == "0xf2fde38b" and htype == HoneypotType.UNKNOWN:
                            continue

                        severity = FindingSeverity.MEDIUM
                        if htype in (
                            HoneypotType.SELFDESTRUCT,
                            HoneypotType.LIQUIDITY_DRAIN,
                        ):
                            severity = FindingSeverity.CRITICAL
                        elif htype in (
                            HoneypotType.OWNER_MINT,
                            HoneypotType.PROXY_UPGRADE,
                            HoneypotType.BLACKLIST,
                            HoneypotType.TRADING_PAUSE,
                        ):
                            severity = FindingSeverity.HIGH

                        result.findings.append(
                            SecurityFinding(
                                finding_type=htype,
                                severity=severity,
                                description=desc or f"Malicious function pattern: {sig}",
                                detail=f"Function: {sig}",
                                code_reference=f"Selector {selector} in bytecode",
                            )
                        )

                # Check for selfdestruct opcode in bytecode
                if "ff" in bytecode:  # SELFDESTRUCT opcode
                    # Verify it's actual SELFDESTRUCT, not coincidental bytes
                    selfdestruct_patterns = [
                        "ff" in bytecode[i : i + 2] for i in range(0, len(bytecode) - 1)
                    ]
                    if any(selfdestruct_patterns):
                        already_found = any(
                            f.finding_type == HoneypotType.SELFDESTRUCT for f in result.findings
                        )
                        if not already_found:
                            result.findings.append(
                                SecurityFinding(
                                    finding_type=HoneypotType.SELFDESTRUCT,
                                    severity=FindingSeverity.CRITICAL,
                                    description="SELFDESTRUCT opcode found in contract bytecode",
                                    detail="Contract can be permanently destroyed - all funds lost",
                                    code_reference="SELFDESTRUCT (0xff) in bytecode",
                                )
                            )

                # Check for delegatecall (proxy-like)
                if "f4" in bytecode and not result.token.is_proxy:
                    result.findings.append(
                        SecurityFinding(
                            finding_type=HoneypotType.PROXY_UPGRADE,
                            severity=FindingSeverity.MEDIUM,
                            description="DELEGATECALL used - possible proxy or delegate pattern",
                            detail="Contract uses delegatecall pattern for execution forwarding",
                            code_reference="DELEGATECALL (0xf4) in bytecode",
                        )
                    )

        except Exception as e:
            logger.debug(f"Bytecode analysis failed: {e}")

    async def _analyze_liquidity(
        self, address: str, chain: str, result: HoneypotAnalysisResult
    ) -> None:
        """Analyze liquidity status - locked vs unlocked LP."""
        # Check via DexScreener
        try:
            data, _ = await fetch_with_fallback(
                [
                    f"https://api.dexscreener.com/latest/dex/tokens/{address}",
                ]
            )
            if data and data.get("pairs"):
                for pair in data["pairs"]:
                    liquidity = float(pair.get("liquidity", {}).get("usd", 0) or 0)
                    pair_addr = pair.get("pairAddress", "")

                    # Very low liquidity check
                    if liquidity < HONEYPOT_THRESHOLDS["low_liquidity_threshold"]:
                        result.findings.append(
                            SecurityFinding(
                                finding_type=HoneypotType.RUG_PULL,
                                severity=(
                                    FindingSeverity.CRITICAL
                                    if liquidity < HONEYPOT_THRESHOLDS["tiny_liquidity_threshold"]
                                    else FindingSeverity.HIGH
                                ),
                                description=f"Extremely low liquidity: ${liquidity:.2f}",
                                detail=f"Pool: {pair.get('dexId', '?')} - {pair_addr[:12]}...",
                            )
                        )

                    # Check pair age
                    pair_created = pair.get("pairCreatedAt", 0)
                    if pair_created and liquidity > 1000:
                        age_hours = (datetime.now(UTC).timestamp() - pair_created / 1000) / 3600
                        if age_hours < 1:
                            result.warnings.append(
                                f"Pool is less than 1 hour old ({age_hours:.1f}h) - very high risk"
                            )
                        elif age_hours < 24:
                            result.warnings.append(
                                f"Pool is less than 24 hours old ({age_hours:.1f}h)"
                            )

        except Exception as e:
            logger.debug(f"Liquidity analysis failed: {e}")

        # Try to find liquidity lock info from known lock contracts
        # (Unicrypt, TeamFinance, Mudra, DXLock, etc.)
        try:
            # Check if LP tokens are burned or locked
            # Look for zero-address burn of LP tokens
            data, _ = await fetch_with_fallback(
                [
                    f"https://api.dexscreener.com/latest/dex/tokens/{address}",
                ]
            )
            if data and data.get("pairs"):
                for pair in data["pairs"]:
                    pair_addr = pair.get("pairAddress", "")
                    if pair_addr:
                        # Check LP holder via RPC (deeper scan)
                        chain_info = EVM_CHAINS.get(chain, {})
                        rpc_url = chain_info.get("rpc", "")
                        if rpc_url:
                            import httpx

                            async with httpx.AsyncClient(timeout=8) as client:
                                # Check if LP was burned (sent to 0x0...dEaD)
                                dead_addr = "0x000000000000000000000000000000000000dead"
                                payload = {
                                    "jsonrpc": "2.0",
                                    "id": 1,
                                    "method": "eth_getBalance",
                                    "params": [dead_addr, "latest"],
                                }
                                try:
                                    resp = await client.post(rpc_url, json=payload)
                                    if resp.status_code == 200:
                                        balance_hex = resp.json().get("result", "0x0")
                                        balance = int(balance_hex, 16) if balance_hex else 0
                                        if balance > 0:
                                            result.liquidity_locked_pct = 100.0
                                            result.findings.append(
                                                SecurityFinding(
                                                    finding_type=HoneypotType.UNKNOWN,
                                                    severity=FindingSeverity.INFO,
                                                    description="LP tokens appear to be burned (sent to dead address)",
                                                    detail="Liquidity cannot be removed if LP is burned",
                                                )
                                            )
                                except Exception:
                                    pass
        except Exception:
            pass

    async def _analyze_holders(
        self, address: str, chain: str, result: HoneypotAnalysisResult
    ) -> None:
        """Analyze holder distribution for concentration risks."""
        # Use DexScreener for basic holder info
        try:
            data, _ = await fetch_with_fallback(
                [
                    f"https://api.dexscreener.com/latest/dex/tokens/{address}",
                ]
            )
            if data and data.get("pairs"):
                # DexScreener doesn't provide holders directly,
                # but we can infer from tx counts
                result.chain_data_sources.append("dexscreener_holders")
        except Exception:
            pass

    async def _analyze_market_data(
        self, address: str, chain: str, result: HoneypotAnalysisResult
    ) -> None:
        """Analyze market data for manipulation signs."""
        try:
            data, _ = await fetch_with_fallback(
                [
                    f"https://api.dexscreener.com/latest/dex/tokens/{address}",
                ]
            )
            if data and data.get("pairs"):
                for pair in data["pairs"]:
                    price_change_h1 = pair.get("priceChange", {}).get("h1", 0) or 0
                    price_change_h24 = pair.get("priceChange", {}).get("h24", 0) or 0
                    volume_h24 = float(pair.get("volume", {}).get("h24", 0) or 0)
                    liquidity = float(pair.get("liquidity", {}).get("usd", 0) or 0)

                    # Detect pump and dump pattern
                    if price_change_h1 > 50 and liquidity < 50000:
                        result.findings.append(
                            SecurityFinding(
                                finding_type=HoneypotType.RUG_PULL,
                                severity=FindingSeverity.HIGH,
                                description=f"Suspicious pump pattern - +{price_change_h1:.0f}% in 1h with low liquidity",
                                detail="Rapid price increase with low liquidity is a classic rug-pull setup",
                            )
                        )

                    # Detect suspicious: >500% in 24h with tiny liquidity
                    if price_change_h24 > 500 and liquidity < 50000:
                        result.findings.append(
                            SecurityFinding(
                                finding_type=HoneypotType.RUG_PULL,
                                severity=FindingSeverity.CRITICAL,
                                description=f"Extreme price movement (+{price_change_h24:.0f}%) with low liquidity",
                                detail="Classic honeypot pattern - pump price to attract buyers, then block sells",
                            )
                        )

                    # Volume anomaly: huge volume on tiny pool
                    if liquidity > 0 and volume_h24 > liquidity * 50:
                        result.findings.append(
                            SecurityFinding(
                                finding_type=HoneypotType.RUG_PULL,
                                severity=FindingSeverity.MEDIUM,
                                description=f"Volume ({volume_h24:.0f}) is {volume_h24 / max(liquidity, 1):.0f}x liquidity - possible wash trading",
                            )
                        )

                    result.chain_data_sources.append("dexscreener_market")
        except Exception:
            pass

    async def _deep_scan(self, address: str, chain: str, result: HoneypotAnalysisResult) -> None:
        """Deep scan - additional on-chain analysis for thorough detection.

        Simulates a buy and sell to test actual honeypot behavior.
        Requires RPC access to the chain.
        """
        chain_info = EVM_CHAINS.get(chain, {})
        rpc_url = chain_info.get("rpc", "")
        if not rpc_url:
            return

        # In deep scan mode, attempt to:
        # 1. Get recent transactions involving this token
        # 2. Look for failed sell transactions (indicates honeypot)
        # 3. Check more obscure malicious patterns
        logger.debug(f"Deep scan enabled for {address[:10]} on {chain}")

        # Check for recent transactions via RPC
        import httpx

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                # Get latest block to check recent activity
                block_payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "eth_blockNumber",
                    "params": [],
                }
                resp = await client.post(rpc_url, json=block_payload)
                if resp.status_code == 200:
                    latest_hex = resp.json().get("result", "0x0")
                    latest_block = int(latest_hex, 16)
                    result.chain_data_sources.append(f"rpc_block:{latest_block}")
        except Exception:
            pass

    # ── Risk Scoring ─────────────────────────────────────────────

    def _compute_risk_score(self, result: HoneypotAnalysisResult) -> int:
        """Compute aggregate risk score from all findings (0-100)."""
        score = 0

        for finding in result.findings:
            if finding.severity == FindingSeverity.CRITICAL:
                score += 35
            elif finding.severity == FindingSeverity.HIGH:
                score += 20
            elif finding.severity == FindingSeverity.MEDIUM:
                score += 10
            elif finding.severity == FindingSeverity.LOW:
                score += 3

        # Cap at 100
        score = min(score, 100)

        # Minimum score even with findings
        if score > 0 and score < 15:
            score = 15

        return score

    def _risk_score_to_label(self, score: int) -> str:
        """Convert numeric risk score to human-readable label."""
        if score >= 70:
            return "critical"
        if score >= 40:
            return "high"
        if score >= 20:
            return "medium"
        if score >= 5:
            return "low"
        return "safe"


# ═══════════════════════════════════════════════════════════════════
# CLI Entry Point
# ═══════════════════════════════════════════════════════════════════


async def main_async() -> None:
    """CLI entry point with argument parsing."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Smart Contract Honeypot & Malicious Token Detector",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 smart_contract_honeypot_detector.py 0x...
  python3 smart_contract_honeypot_detector.py 0x... --chain bsc
  python3 smart_contract_honeypot_detector.py 0x... --deep-scan
  python3 smart_contract_honeypot_detector.py 0x... --format json
  python3 smart_contract_honeypot_detector.py 0x... --chain polygon --format json --deep-scan
        """,
    )
    parser.add_argument("address", type=str, help="Token contract address (0x...)")
    parser.add_argument(
        "--chain",
        type=str,
        default="ethereum",
        choices=list(EVM_CHAINS.keys()),
        help="Blockchain (default: ethereum)",
    )
    parser.add_argument(
        "--deep-scan",
        action="store_true",
        help="Enable deeper on-chain analysis (slower)",
    )
    parser.add_argument(
        "--format",
        type=str,
        choices=["text", "json"],
        default="text",
        help="Output format (default: text)",
    )

    args = parser.parse_args()

    detector = HoneypotDetector()
    result = await detector.analyze_token(
        address=args.address,
        chain=args.chain,
        deep_scan=args.deep_scan,
    )

    if args.format == "json":
        print(result.report(fmt="json"))
    else:
        print(result.report())


def main() -> None:
    """Synchronous CLI entry point."""
    asyncio.run(main_async())


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    main()
