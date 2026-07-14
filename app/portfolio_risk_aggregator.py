"""
Cross-Chain Portfolio Risk Aggregator
======================================
Aggregates a wallet's token holdings across ALL supported chains and produces
a unified risk profile - the free, comprehensive alternative to Nansen Portfolio.

How it works:
  1. Scans EVM chains (Ethereum, Base, BSC, Arbitrum, Polygon, Optimism, etc.)
     via web3.py for native balance + ERC20 holdings
  2. Scans Solana via solana-py for SPL token holdings
  3. Runs each token through the Unified Scanner risk engine
  4. Aggregates into a PortfolioRiskProfile with:
     - Chain-by-chain breakdown of total value, risk scores
     - Per-token risk scoring with scam/honeypot flags
     - Concentration risk (single token/chains exposure)
     - Overall portfolio health score (0-100)
  5. Generates human-readable risk report

Standalone usage:
    python3 portfolio_risk_aggregator.py <wallet_address>
    python3 portfolio_risk_aggregator.py <wallet_address> --chains ethereum,base,solana

API usage:
    from app.portfolio_risk_aggregator import PortfolioRiskAggregator
    agg = PortfolioRiskAggregator()
    profile = await agg.analyze("0x...")
    print(profile.report())
"""

import asyncio
import json
import logging
from app.core.logging import get_logger
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

logger = get_logger(__name__)

# ─── TRY IMPORTS (graceful fallback) ───────────────────────────────

try:
    from web3 import HTTPProvider
    from web3 import Web3 as _Web3

    WEB3_AVAILABLE = True
except ImportError:
    WEB3_AVAILABLE = False
    _Web3 = None
    HTTPProvider = None

try:
    from solana.rpc.api import Client as SolanaClient

    SOLANA_AVAILABLE = True
except ImportError:
    SOLANA_AVAILABLE = False
    SolanaClient = None

try:
    import httpx

    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False

# ─── CONSTANTS ─────────────────────────────────────────────────────

DEFAULT_TIMEOUT = 15  # seconds per chain scan
MAX_TOKENS_PER_WALLET = 100  # max tokens to analyze per wallet
RISK_WEIGHT_TOKENS = 0.6  # weight for token-level risk in portfolio score
RISK_WEIGHT_CONCENTRATION = 0.25
RISK_WEIGHT_CHAIN_DIVERSITY = 0.15

# ERC20 ABI (minimal - balanceOf + decimals + symbol)
ERC20_ABI = json.loads("""
[
    {"constant":true,"inputs":[{"name":"_owner","type":"address"}],"name":"balanceOf","outputs":[{"name":"balance","type":"uint256"}],"type":"function"},
    {"constant":true,"inputs":[],"name":"decimals","outputs":[{"name":"","type":"uint8"}],"type":"function"},
    {"constant":true,"inputs":[],"name":"symbol","outputs":[{"name":"","type":"string"}],"type":"function"},
    {"constant":true,"inputs":[],"name":"name","outputs":[{"name":"","type":"string"}],"type":"function"},
    {"constant":true,"inputs":[],"name":"totalSupply","outputs":[{"name":"","type":"uint256"}],"type":"function"}
]
""")

# ─── CHAIN CONFIGURATIONS ───────────────────────────────────────────

# ─── ADDRESS VALIDATION ─────────────────────────────────────────────

EVM_ADDRESS_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
SOLANA_ADDRESS_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")


def validate_wallet_address(address: str) -> tuple[bool, str]:
    """Validate a wallet address. Returns (is_valid, chain_hint)."""
    if EVM_ADDRESS_RE.match(address):
        return True, "evm"
    if SOLANA_ADDRESS_RE.match(address):
        return True, "solana"
    if not address.startswith("0x") and len(address) >= 32:
        return True, "solana"  # lenient solana check
    if address.startswith("0x") and len(address) == 42:
        return True, "evm"  # lenient EVM check (allow mixed case)
    return False, ""


CHAIN_CONFIGS: dict[str, dict[str, Any]] = {
    "ethereum": {
        "rpc": "https://ethereum-rpc.publicnode.com",
        "chain_id": 1,
        "native_symbol": "ETH",
        "decimals": 18,
        "explorer": "https://etherscan.io",
        "type": "evm",
    },
    "base": {
        "rpc": "https://base-rpc.publicnode.com",
        "chain_id": 8453,
        "native_symbol": "ETH",
        "decimals": 18,
        "explorer": "https://basescan.org",
        "type": "evm",
    },
    "arbitrum": {
        "rpc": "https://arbitrum-rpc.publicnode.com",
        "chain_id": 42161,
        "native_symbol": "ETH",
        "decimals": 18,
        "explorer": "https://arbiscan.io",
        "type": "evm",
    },
    "optimism": {
        "rpc": "https://optimism-rpc.publicnode.com",
        "chain_id": 10,
        "native_symbol": "ETH",
        "decimals": 18,
        "explorer": "https://optimistic.etherscan.io",
        "type": "evm",
    },
    "polygon": {
        "rpc": "https://polygon-rpc.publicnode.com",
        "chain_id": 137,
        "native_symbol": "MATIC",
        "decimals": 18,
        "explorer": "https://polygonscan.com",
        "type": "evm",
        "poa": True,
    },
    "bsc": {
        "rpc": "https://bsc-rpc.publicnode.com",
        "chain_id": 56,
        "native_symbol": "BNB",
        "decimals": 18,
        "explorer": "https://bscscan.com",
        "type": "evm",
        "poa": True,
    },
    "avalanche": {
        "rpc": "https://avalanche-rpc.publicnode.com",
        "chain_id": 43114,
        "native_symbol": "AVAX",
        "decimals": 18,
        "explorer": "https://snowtrace.io",
        "type": "evm",
        "poa": True,
    },
    "linea": {
        "rpc": "https://linea-rpc.publicnode.com",
        "chain_id": 59144,
        "native_symbol": "ETH",
        "decimals": 18,
        "explorer": "https://lineascan.build",
        "type": "evm",
    },
    "zksync": {
        "rpc": "https://zksync-rpc.publicnode.com",
        "chain_id": 324,
        "native_symbol": "ETH",
        "decimals": 18,
        "explorer": "https://explorer.zksync.io",
        "type": "evm",
    },
    "scroll": {
        "rpc": "https://scroll-rpc.publicnode.com",
        "chain_id": 534352,
        "native_symbol": "ETH",
        "decimals": 18,
        "explorer": "https://scrollscan.com",
        "type": "evm",
    },
    "mantle": {
        "rpc": "https://mantle-rpc.publicnode.com",
        "chain_id": 5000,
        "native_symbol": "MNT",
        "decimals": 18,
        "explorer": "https://mantlescan.xyz",
        "type": "evm",
    },
    "fantom": {
        "rpc": "https://fantom-rpc.publicnode.com",
        "chain_id": 250,
        "native_symbol": "FTM",
        "decimals": 18,
        "explorer": "https://ftmscan.com",
        "type": "evm",
        "poa": True,
    },
    "solana": {
        "rpc": "https://solana-rpc.publicnode.com",
        "chain_id": "solana",
        "native_symbol": "SOL",
        "decimals": 9,
        "explorer": "https://solscan.io",
        "type": "solana",
    },
}

# Common high-value ERC20 token addresses for estimation
COMMON_TOKENS: dict[str, dict[str, str]] = {
    "ethereum": {
        "USDC": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        "USDT": "0xdAC17F958D2ee523a2206206994597C13D831ec7",
        "WBTC": "0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599",
        "LINK": "0x514910771AF9Ca656af840dff83E8264EcF986CA",
        "UNI": "0x1f9840a85d5aF5bf1D1762F925BDADdC4201F984",
        "DAI": "0x6B175474E89094C44Da98b954EedeAC495271d0F",
        "PEPE": "0x6982508145454Ce325dDbE47a25d4ec3d2311933",
        "SHIB": "0x95aD61b0a150d79219dCF64E1E6Cc01f0B64C4cE",
    },
    "base": {
        "USDC": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
        "AERO": "0x940181a94A35A4569E4529A3CDfB74e38FD98631",
    },
    "bsc": {
        "USDT": "0x55d398326f99059fF775485246999027B3197955",
        "CAKE": "0x0E09FaBB73Bd3Ade0a17ECC321fD13a19e81cE82",
        "BNB": "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c",
    },
}

# Common scam token patterns (address snippets to check)
KNOWN_SCAM_TOKENS: set[str] = set()


# ═══════════════════════════════════════════════════════════════════
# Data Models
# ═══════════════════════════════════════════════════════════════════


class RiskLevel(Enum):
    SAFE = "safe"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @classmethod
    def from_score(cls, score: float) -> "RiskLevel":
        if score >= 80:
            return cls.SAFE
        elif score >= 60:
            return cls.LOW
        elif score >= 40:
            return cls.MEDIUM
        elif score >= 20:
            return cls.HIGH
        return cls.CRITICAL


@dataclass
class TokenHolding:
    """A single token holding at a wallet, with risk data."""

    chain: str
    symbol: str
    name: str
    contract_address: str
    balance_raw: int
    balance_formatted: float
    decimals: int
    estimated_usd_value: float = 0.0
    risk_score: float = 50.0  # 0=critical risk, 100=safe
    risk_flags: list[str] = field(default_factory=list)
    is_native: bool = False
    pct_of_portfolio: float = 0.0

    def risk_level(self) -> RiskLevel:
        return RiskLevel.from_score(self.risk_score)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ChainPortfolio:
    """Aggregated holdings for a single chain."""

    chain: str
    total_value_usd: float = 0.0
    token_count: int = 0
    holdings: list[TokenHolding] = field(default_factory=list)
    avg_risk_score: float = 50.0
    high_risk_count: int = 0
    critical_risk_count: int = 0
    scan_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "chain": self.chain,
            "total_value_usd": round(self.total_value_usd, 2),
            "token_count": self.token_count,
            "avg_risk_score": round(self.avg_risk_score, 1),
            "high_risk_count": self.high_risk_count,
            "critical_risk_count": self.critical_risk_count,
            "holdings": [h.to_dict() for h in self.holdings],
            "scan_error": self.scan_error,
        }


@dataclass
class PortfolioRiskProfile:
    """Complete cross-chain portfolio risk assessment."""

    wallet_address: str
    chains_scanned: list[str]
    total_value_usd: float = 0.0
    total_tokens: int = 0
    chain_count: int = 0
    overall_health_score: float = 50.0
    concentration_risk_pct: float = 0.0  # % in top token
    chain_portfolios: dict[str, ChainPortfolio] = field(default_factory=dict)
    top_risk_tokens: list[TokenHolding] = field(default_factory=list)
    findings: list[str] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    scan_errors: list[str] = field(default_factory=list)

    def overall_risk_level(self) -> RiskLevel:
        return RiskLevel.from_score(self.overall_health_score)

    def report(self, format: str = "text") -> str:
        """Generate a human-readable risk report."""
        if format == "json":
            return json.dumps(self.to_dict(), indent=2, default=str)

        lines = []
        lines.append("=" * 64)
        total_val = f"${self.total_value_usd:,.2f}" if self.total_value_usd > 0 else "Unknown"
        lines.append(
            f"  PORTFOLIO RISK REPORT - {self.wallet_address[:12]}...{self.wallet_address[-6:]}"
        )
        lines.append(
            f"  Health: {self.overall_health_score:.0f}/100 ({self.overall_risk_level().value.upper()})"
        )
        lines.append(
            f"  Value:  {total_val} | Tokens: {self.total_tokens} | Chains: {self.chain_count}"
        )
        lines.append("=" * 64)

        if self.findings:
            lines.append("")
            lines.append("🔍 KEY FINDINGS:")
            for f in self.findings:
                lines.append(f"  • {f}")

        lines.append("")
        lines.append(f"{'CHAIN':<14} {'VALUE':<16} {'TOKENS':<8} {'RISK':<8} {'HIGH/CRIT':<10}")
        lines.append("-" * 56)
        for chain_name, cp in sorted(self.chain_portfolios.items()):
            val = f"${cp.total_value_usd:,.2f}" if cp.total_value_usd else "?"
            avg_risk = f"{cp.avg_risk_score:.0f}"
            high_crit = f"{cp.high_risk_count}/{cp.critical_risk_count}"
            error_tag = f" ⚠️ {cp.scan_error}" if cp.scan_error else ""
            lines.append(
                f"{chain_name:<14} {val:<16} {cp.token_count:<8} {avg_risk:<8} {high_crit:<10}{error_tag}"
            )

        if self.top_risk_tokens:
            lines.append("")
            lines.append("⚠️  HIGHEST RISK TOKENS:")
            for t in self.top_risk_tokens[:10]:
                val = f"${t.estimated_usd_value:,.2f}" if t.estimated_usd_value > 0 else "N/A"
                flags = ", ".join(t.risk_flags[:3])
                pct = f"{t.pct_of_portfolio:.1f}%"
                lines.append(
                    f"  • {t.symbol:>8} ({t.chain}) - Score: {t.risk_score:.0f}/100 - {val} - {pct}"
                )
                if flags:
                    lines.append(f"            ⚑ {flags}")

        lines.append("")
        lines.append("=" * 64)
        lines.append(f"  Report generated: {self.timestamp[:19]}")
        lines.append("=" * 64)

        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "wallet_address": self.wallet_address,
            "chains_scanned": self.chains_scanned,
            "total_value_usd": round(self.total_value_usd, 2),
            "total_tokens": self.total_tokens,
            "chain_count": self.chain_count,
            "overall_health_score": round(self.overall_health_score, 1),
            "concentration_risk_pct": round(self.concentration_risk_pct, 1),
            "overall_risk_level": self.overall_risk_level().value,
            "findings": self.findings,
            "chain_portfolios": {k: v.to_dict() for k, v in self.chain_portfolios.items()},
            "top_risk_tokens": [t.to_dict() for t in self.top_risk_tokens[:10]],
            "scan_errors": self.scan_errors,
            "timestamp": self.timestamp,
        }


# ═══════════════════════════════════════════════════════════════════
# Chain Connectors
# ═══════════════════════════════════════════════════════════════════


class ChainConnector:
    """Abstract chain connector base."""

    async def get_native_balance(self, address: str) -> float:
        raise NotImplementedError

    async def get_token_balances(self, address: str) -> list[dict[str, Any]]:
        raise NotImplementedError


class EVMConnector(ChainConnector):
    """EVM chain connector using web3.py."""

    def __init__(self, chain_name: str, config: dict[str, Any]):
        self.chain_name = chain_name
        self.config = config
        self._w3: Any = None

    @property
    def w3(self) -> Any:
        if self._w3 is None:
            if not WEB3_AVAILABLE:
                raise RuntimeError("web3.py not installed")
            provider = HTTPProvider(self.config["rpc"], request_kwargs={"timeout": DEFAULT_TIMEOUT})
            self._w3 = _Web3(provider)
            if self.config.get("poa"):
                try:
                    from web3.middleware import geth_poa_middleware

                    self._w3.middleware_onion.inject(geth_poa_middleware, layer=0)
                except ImportError:
                    try:
                        from web3.middleware import ExtraDataToPOAMiddleware

                        self._w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
                    except ImportError:
                        pass
        return self._w3

    async def get_native_balance(self, address: str) -> float:
        """Get native token balance in human-readable form."""
        try:
            bal_wei = await asyncio.to_thread(self.w3.eth.get_balance, address)
            decimals = self.config["decimals"]
            return float(bal_wei) / (10**decimals)
        except ConnectionError as e:
            logger.warning(f"{self.chain_name} RPC connection error (balance): {e}")
            return 0.0
        except TimeoutError as e:
            logger.warning(f"{self.chain_name} RPC timeout (balance): {e}")
            return 0.0
        except ValueError as e:
            logger.warning(f"{self.chain_name} value error (balance): {e}")
            return 0.0
        except Exception as e:
            logger.warning(
                f"{self.chain_name} unexpected native balance error: {type(e).__name__}: {e}"
            )
            return 0.0

    async def get_token_balances(self, address: str) -> list[dict[str, Any]]:
        """Get ERC20 token balances for an address.

        Uses a two-pass approach:
        1. Check common high-value tokens first
        2. If enabled, scan for all transfers to discover held tokens
        """
        results = []
        checked = set()

        # First pass: check common tokens
        common = COMMON_TOKENS.get(self.chain_name, {})
        for _symbol, token_addr in common.items():
            try:
                contract = self.w3.eth.contract(
                    address=_Web3.to_checksum_address(token_addr),
                    abi=ERC20_ABI,
                )
                bal = await asyncio.to_thread(contract.functions.balanceOf(address).call)
                if bal > 0:
                    dec = await asyncio.to_thread(contract.functions.decimals.call)
                    sym = await asyncio.to_thread(contract.functions.symbol.call)
                    results.append(
                        {
                            "address": token_addr,
                            "symbol": sym,
                            "balance_raw": bal,
                            "balance_formatted": float(bal) / (10**dec),
                            "decimals": dec,
                        }
                    )
                    checked.add(token_addr.lower())
            except Exception:
                pass

        # Second pass: check recent transfer events to discover tokens
        try:
            # Look at ERC20 Transfer events to the wallet (last 5000 blocks)
            latest = await asyncio.to_thread(self.w3.eth.block_number)
            from_block = max(latest - 5000, 0)
            transfer_topic = self.w3.keccak(text="Transfer(address,address,uint256)").hex()

            logs = await asyncio.to_thread(
                self.w3.eth.get_logs,
                {
                    "address": None,  # across all contracts
                    "fromBlock": from_block,
                    "toBlock": latest,
                    "topics": [
                        transfer_topic,
                        None,
                        _Web3.to_checksum_address(address).hex()
                        if address.startswith("0x")
                        else "0x" + address.zfill(64),
                    ],
                },
            )
            # Limit to avoid huge sets
            for log in logs[:200]:
                token_addr = log["address"].lower()
                if token_addr in checked:
                    continue
                checked.add(token_addr)
                try:
                    contract = self.w3.eth.contract(address=token_addr, abi=ERC20_ABI)
                    bal = await asyncio.to_thread(contract.functions.balanceOf(address).call)
                    if bal > 0:
                        dec = await asyncio.to_thread(contract.functions.decimals.call)
                        sym = await asyncio.to_thread(contract.functions.symbol.call)
                        results.append(
                            {
                                "address": log["address"],
                                "symbol": sym,
                                "balance_raw": bal,
                                "balance_formatted": float(bal) / (10**dec),
                                "decimals": dec,
                            }
                        )
                except Exception:
                    pass
                if len(results) >= MAX_TOKENS_PER_WALLET:
                    break
        except Exception as e:
            logger.debug(f"{self.chain_name} event scan error: {e}")

        return results


class SolanaConnector(ChainConnector):
    """Solana chain connector using solana-py."""

    def __init__(self):
        self._client: Any = None

    @property
    def client(self) -> Any:
        if self._client is None:
            if not SOLANA_AVAILABLE:
                raise RuntimeError("solana-py not installed")
            self._client = SolanaClient(CHAIN_CONFIGS["solana"]["rpc"])
        return self._client

    async def get_native_balance(self, address: str) -> float:
        """Get SOL balance."""
        try:
            from solana.rpc.api import Pubkey as SolPubkey

            pubkey = SolPubkey.from_string(address)
            resp = await asyncio.to_thread(self.client.get_balance, pubkey)
            if resp and resp.get("result"):
                lamports = resp["result"]["value"]
                return lamports / 1e9
            return 0.0
        except ConnectionError as e:
            logger.warning(f"solana RPC connection error (balance): {e}")
            return 0.0
        except TimeoutError as e:
            logger.warning(f"solana RPC timeout (balance): {e}")
            return 0.0
        except ValueError as e:
            logger.warning(
                f"solana address validation error: {e} - invalid pubkey: {address[:12]}..."
            )
            return 0.0
        except Exception as e:
            logger.warning(f"solana unexpected native balance error: {type(e).__name__}: {e}")
            return 0.0

    async def get_token_balances(self, address: str) -> list[dict[str, Any]]:
        """Get SPL token balances."""
        results = []
        try:
            from solana.rpc.api import Pubkey as SolPubkey

            pubkey = SolPubkey.from_string(address)
            resp = await asyncio.to_thread(
                self.client.get_token_accounts_by_owner_json_parsed, pubkey
            )
            if not resp or not resp.get("result"):
                return results
            for item in resp["result"]["value"]:
                try:
                    parsed = item["account"]["data"]["parsed"]["info"]
                    mint = parsed.get("mint", "")
                    token_amount = parsed.get("tokenAmount", {})
                    ui_amount = float(token_amount.get("uiAmount", 0) or 0)
                    if ui_amount <= 0:
                        continue
                    results.append(
                        {
                            "address": mint,
                            "symbol": token_amount.get("symbol", "SPL"),
                            "balance_raw": int(token_amount.get("amount", "0")),
                            "balance_formatted": ui_amount,
                            "decimals": token_amount.get("decimals", 0),
                        }
                    )
                except Exception:
                    pass
        except Exception as e:
            logger.warning(f"solana token balances error: {e}")
        return results


# ═══════════════════════════════════════════════════════════════════
# Token Risk Scanner
# ═══════════════════════════════════════════════════════════════════


class TokenRiskScorer:
    """Scores a token's risk using heuristics + on-chain data.

    In production, this calls the unified scanner. For standalone use,
    it applies heuristic checks based on token metadata and chain data.
    """

    def __init__(self):
        self._scanner_available = False
        self._import_error: str | None = None
        self._try_import_scanner()

    def _try_import_scanner(self):
        """Try to import the unified scanner for real risk scoring."""
        try:
            import importlib

            self._scanner_available = importlib.util.find_spec("app.unified_scanner") is not None
        except ImportError:
            pass

    async def score_token(self, chain: str, address: str, symbol: str) -> dict[str, Any]:
        """Score a token. Returns risk_score (0-100, higher = safer) and flags."""
        if not address or address == "0x0000000000000000000000000000000000000000":
            return {"risk_score": 100, "risk_flags": [], "reason": "Native token (zero address)"}

        if self._scanner_available:
            try:
                from app.unified_scanner import scan_token as _scan_token

                result = await _scan_token(chain, address)
                if result:
                    score = result.get("safety_score", 50)
                    flags = result.get("risk_flags", [])
                    return {"risk_score": score, "risk_flags": flags, "reason": "Scanner"}
            except Exception:
                pass

        # Heuristic fallback
        return self._heuristic_score(chain, address, symbol)

    def _heuristic_score(self, chain: str, address: str, symbol: str) -> dict[str, Any]:
        """Heuristic risk assessment when scanner unavailable."""
        score = 50  # default: neutral
        flags = []
        addr_lower = address.lower()

        # Check known scam tokens
        if addr_lower in KNOWN_SCAM_TOKENS:
            score -= 40
            flags.append("KNOWN_SCAM_TOKEN")

        # Very new token heuristic (check if address appears in known lists)
        if not addr_lower.startswith("0x") or len(address) < 20:
            score -= 20
            flags.append("NON_STANDARD_ADDRESS")

        # Check name/symbol heuristics
        upper_sym = symbol.upper() if symbol else ""
        scam_indicators = ["ELON", "MUSK", "SHIBA", "FLOCKI", "SQUID", "SAFEMOON"]
        for ind in scam_indicators:
            if ind in upper_sym:
                score -= 15
                flags.append(f"SCAM_INDICATOR_{ind}")
                break

        # Check if it's a well-known token
        for chain_tokens in COMMON_TOKENS.values():
            for _known_sym, known_addr in chain_tokens.items():
                if known_addr.lower() == addr_lower:
                    score = 90
                    flags = []
                    return {"risk_score": score, "risk_flags": flags, "reason": "Known token"}

        return {"risk_score": max(0, min(100, score)), "risk_flags": flags, "reason": "Heuristic"}


# ═══════════════════════════════════════════════════════════════════
# Price Oracle (Coingecko-based estimation)
# ═══════════════════════════════════════════════════════════════════


class PriceOracle:
    """Simple price oracle using Coingecko public API.

    Caches prices in-memory to avoid rate limits.
    """

    def __init__(self):
        self._cache: dict[str, float] = {
            "ETH": 2800.0,
            "BTC": 68000.0,
            "SOL": 145.0,
            "BNB": 580.0,
            "MATIC": 0.55,
            "AVAX": 32.0,
            "MNT": 0.85,
            "FTM": 0.68,
            "USDC": 1.0,
            "USDT": 1.0,
            "DAI": 1.0,
            "WBTC": 68000.0,
            "LINK": 14.0,
            "UNI": 7.5,
            "PEPE": 0.000012,
            "SHIB": 0.000025,
            "AERO": 0.85,
            "CAKE": 2.10,
        }
        self._cache_time: float = 0
        self._cache_ttl = 300  # 5 minutes

    async def get_price(self, symbol: str) -> float:
        """Get USD price for a token symbol. Uses fallbacks."""
        upper = symbol.upper()
        cached = self._cache.get(upper)
        if cached is not None:
            return cached

        # Try async lookup via Coingecko
        try:
            if HTTPX_AVAILABLE:
                async with httpx.AsyncClient(timeout=5) as client:
                    url = f"https://api.coingecko.com/api/v3/simple/price?ids={upper.lower()}&vs_currencies=usd"
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        data = resp.json()
                        price = data.get(upper.lower(), {}).get("usd")
                        if price:
                            self._cache[upper] = float(price)
                            return float(price)
        except Exception:
            pass

        return 0.0  # Unknown price

    def estimate_native_price(self, chain: str) -> float:
        """Get estimated native token price for a chain."""
        symbol = CHAIN_CONFIGS[chain]["native_symbol"]
        return self._cache.get(symbol.upper(), 0.0)


# ═══════════════════════════════════════════════════════════════════
# Portfolio Risk Aggregator
# ═══════════════════════════════════════════════════════════════════


class PortfolioRiskAggregator:
    """Main portfolio risk analysis engine."""

    def __init__(self, chains: list[str] | None = None):
        self.chains = chains or [
            "ethereum",
            "base",
            "arbitrum",
            "optimism",
            "polygon",
            "bsc",
            "avalanche",
            "fantom",
            "solana",
        ]
        self.price_oracle = PriceOracle()
        self.token_scorer = TokenRiskScorer()
        self._connectors: dict[str, ChainConnector] = {}

    def _get_connector(self, chain: str) -> ChainConnector:
        """Get or create a chain connector."""
        if chain not in self._connectors:
            config = CHAIN_CONFIGS.get(chain)
            if not config:
                raise ValueError(f"Unsupported chain: {chain}")
            if config["type"] == "evm":
                self._connectors[chain] = EVMConnector(chain, config)
            elif config["type"] == "solana":
                self._connectors[chain] = SolanaConnector()
            else:
                raise ValueError(f"Unknown chain type for {chain}: {config['type']}")
        return self._connectors[chain]

    async def analyze(self, wallet_address: str) -> PortfolioRiskProfile:
        """Full cross-chain portfolio risk analysis."""
        profile = PortfolioRiskProfile(
            wallet_address=wallet_address,
            chains_scanned=list(self.chains),
        )

        # Scan each chain in parallel
        tasks = []
        for chain in self.chains:
            tasks.append(self._scan_chain(chain, wallet_address))
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Process results
        all_tokens: list[TokenHolding] = []
        total_value = 0.0

        for chain, result in zip(self.chains, results, strict=False):
            if isinstance(result, Exception):
                profile.scan_errors.append(f"{chain}: {str(result)[:100]}")
                profile.chain_portfolios[chain] = ChainPortfolio(
                    chain=chain, scan_error=str(result)[:100]
                )
                continue

            cp = result  # ChainPortfolio
            profile.chain_portfolios[chain] = cp
            total_value += cp.total_value_usd
            all_tokens.extend(cp.holdings)

        # Calculate percentages
        for token in all_tokens:
            if total_value > 0:
                token.pct_of_portfolio = (token.estimated_usd_value / total_value) * 100

        # Calculate concentration risk
        if all_tokens:
            sorted_by_value = sorted(all_tokens, key=lambda t: t.estimated_usd_value, reverse=True)
            top_token_pct = sorted_by_value[0].pct_of_portfolio if sorted_by_value else 0
            profile.concentration_risk_pct = top_token_pct

            # Top risk tokens (sorted by risk score ascending = most dangerous first)
            risk_sorted = sorted(
                [t for t in all_tokens if t.risk_score < 60],
                key=lambda t: t.risk_score,
            )
            profile.top_risk_tokens = risk_sorted

        profile.total_value_usd = total_value
        profile.total_tokens = len(all_tokens)
        profile.chain_count = len(
            [
                c
                for c in self.chains
                if c in profile.chain_portfolios and profile.chain_portfolios[c].scan_error is None
            ]
        )

        # Calculate overall health score
        profile.overall_health_score = self._calculate_health_score(profile)

        # Generate findings
        profile.findings = self._generate_findings(profile)

        return profile

    async def _scan_chain(self, chain: str, wallet: str) -> ChainPortfolio:
        """Scan a single chain for wallet holdings."""
        cp = ChainPortfolio(chain=chain)

        try:
            connector = self._get_connector(chain)
        except ValueError as e:
            cp.scan_error = str(e)
            return cp

        # Get native balance
        try:
            native_balance = await connector.get_native_balance(wallet)
        except Exception as e:
            native_balance = 0.0
            logger.debug(f"{chain} native balance error: {e}")

        # Get token balances
        try:
            token_data = await connector.get_token_balances(wallet)
        except Exception as e:
            token_data = []
            logger.debug(f"{chain} token balances error: {e}")

        # Process native token
        native_price = self.price_oracle.estimate_native_price(chain)
        native_value = native_balance * native_price
        if native_balance > 0:
            native_addr = "0x0000000000000000000000000000000000000000"
            native_sym = CHAIN_CONFIGS[chain]["native_symbol"]
            native_token = TokenHolding(
                chain=chain,
                symbol=native_sym,
                name=f"Native {native_sym}",
                contract_address=native_addr,
                balance_raw=0,
                balance_formatted=native_balance,
                decimals=CHAIN_CONFIGS[chain]["decimals"],
                estimated_usd_value=native_value,
                risk_score=90,  # Native tokens are generally low risk
                risk_flags=[],
                is_native=True,
            )
            cp.holdings.append(native_token)

        # Process ERC20/SPL tokens
        for td in token_data[:MAX_TOKENS_PER_WALLET]:
            try:
                addr = td.get("address", "")
                symbol = td.get("symbol", "UNK")
                formatted = td.get("balance_formatted", 0)

                if formatted <= 0:
                    continue

                # Get price estimate
                price = await self.price_oracle.get_price(symbol)
                usd_value = formatted * price

                # Get risk score
                risk_result = await self.token_scorer.score_token(chain, addr, symbol)

                token = TokenHolding(
                    chain=chain,
                    symbol=symbol,
                    name=td.get("symbol", symbol),
                    contract_address=addr,
                    balance_raw=td.get("balance_raw", 0),
                    balance_formatted=formatted,
                    decimals=td.get("decimals", 18),
                    estimated_usd_value=usd_value,
                    risk_score=risk_result["risk_score"],
                    risk_flags=risk_result.get("risk_flags", []),
                    is_native=False,
                )
                cp.holdings.append(token)
            except Exception as e:
                logger.debug(f"Error processing token on {chain}: {e}")

        # Calculate chain-level stats
        cp.token_count = len(cp.holdings)
        if cp.holdings:
            cp.total_value_usd = sum(h.estimated_usd_value for h in cp.holdings)
            cp.avg_risk_score = sum(h.risk_score for h in cp.holdings) / len(cp.holdings)
            cp.high_risk_count = len([h for h in cp.holdings if h.risk_score < 40])
            cp.critical_risk_count = len([h for h in cp.holdings if h.risk_score < 20])

        return cp

    def _calculate_health_score(self, profile: PortfolioRiskProfile) -> float:
        """Calculate overall portfolio health score (0-100)."""
        if not profile.chain_portfolios:
            return 50.0

        # Component 1: Average token risk (0-100) weighted 60%
        token_scores = []
        for cp in profile.chain_portfolios.values():
            for h in cp.holdings:
                token_scores.append(h.risk_score)
        avg_token_score = sum(token_scores) / len(token_scores) if token_scores else 50

        # Component 2: Concentration risk (100 - % in single token), weighted 25%
        conc_score = 100 - profile.concentration_risk_pct

        # Component 3: Chain diversity, weighted 15%
        chains_with_tokens = sum(
            1 for cp in profile.chain_portfolios.values() if cp.token_count > 0
        )
        diversity_score = min(100, (chains_with_tokens / max(1, len(self.chains))) * 100)

        health = (
            avg_token_score * RISK_WEIGHT_TOKENS
            + conc_score * RISK_WEIGHT_CONCENTRATION
            + diversity_score * RISK_WEIGHT_CHAIN_DIVERSITY
        )

        return max(0, min(100, health))

    def _generate_findings(self, profile: PortfolioRiskProfile) -> list[str]:
        """Generate actionable findings from the risk profile."""
        findings = []

        # Critical risk tokens
        critical = []
        for cp in profile.chain_portfolios.values():
            for h in cp.holdings:
                if h.risk_score < 20:
                    critical.append(h)

        if critical:
            syms = ", ".join(f"{h.symbol} ({h.chain})" for h in critical[:5])
            findings.append(f"⚠️  {len(critical)} CRITICAL risk token(s) detected: {syms}")

        # High risk tokens
        high = []
        for cp in profile.chain_portfolios.values():
            for h in cp.holdings:
                if 20 <= h.risk_score < 40:
                    high.append(h)
        if high:
            findings.append(f"⚠️  {len(high)} HIGH risk token(s) - investigate withdrawals")

        # Concentration risk
        if profile.concentration_risk_pct > 50:
            findings.append(
                f"🔴 High concentration risk: {profile.concentration_risk_pct:.0f}% in single token"
            )
        elif profile.concentration_risk_pct > 30:
            findings.append(
                f"🟡 Moderate concentration: {profile.concentration_risk_pct:.0f}% in top holding"
            )

        # Low chain diversity
        chains_used = sum(1 for cp in profile.chain_portfolios.values() if cp.token_count > 0)
        if chains_used <= 1:
            findings.append("🟡 Single-chain portfolio - consider diversifying across chains")
        elif chains_used >= 5:
            findings.append(f"✅ Good cross-chain diversity ({chains_used} chains)")

        # Scam flags
        scam_tokens = []
        for cp in profile.chain_portfolios.values():
            for h in cp.holdings:
                if any("SCAM" in f or "HONEYPOT" in f for f in h.risk_flags):
                    scam_tokens.append(h)
        if scam_tokens:
            findings.append(f"🚨 {len(scam_tokens)} token(s) with scam indicators found!")

        # High value in risky tokens
        risky_value = 0.0
        for cp in profile.chain_portfolios.values():
            for h in cp.holdings:
                if h.risk_score < 40:
                    risky_value += h.estimated_usd_value
        if risky_value > 1000:
            findings.append(f"💰 ${risky_value:,.0f} total value in high/critical risk tokens")

        # Clean portfolio
        if not critical and not high and profile.overall_health_score >= 80:
            findings.append("✅ Portfolio looks clean - no high-risk tokens detected")

        return findings


# ═══════════════════════════════════════════════════════════════════
# CLI Entry Point
# ═══════════════════════════════════════════════════════════════════


async def main():
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Cross-Chain Portfolio Risk Aggregator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 portfolio_risk_aggregator.py 0x742d35Cc6634C0532925a3b844Bc454e4438f44e
  python3 portfolio_risk_aggregator.py 0x... --chains ethereum,base
  python3 portfolio_risk_aggregator.py 0x... --format json
  python3 portfolio_risk_aggregator.py <solana_wallet>
        """,
    )
    parser.add_argument("wallet", help="Wallet address to analyze (EVM or Solana)")
    parser.add_argument("--chains", help="Comma-separated chain list (default: all)")
    parser.add_argument(
        "--format", choices=["text", "json"], default="text", help="Output format (default: text)"
    )
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")

    args = parser.parse_args()

    if args.debug:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    chains = args.chains.split(",") if args.chains else None

    # Validate wallet address
    is_valid, chain_hint = validate_wallet_address(args.wallet)
    if not is_valid:
        print(f"❌ Invalid wallet address: {args.wallet}")
        print("   Expected: 42-char hex (0x...) for EVM, or 32-44 base58 for Solana")
        sys.exit(1)

    aggregator = PortfolioRiskAggregator(chains=chains)
    print(f"🔍 Analyzing wallet: {args.wallet[:16]}...{args.wallet[-6:]}")
    print(f"   Chains: {', '.join(aggregator.chains)}")
    if chain_hint == "solana":
        print("   [i] Solana address detected -- scanning SPL tokens")
    else:
        print("   [i] EVM address detected -- scanning ERC20 tokens")
    print()

    profile = await aggregator.analyze(args.wallet)
    print(profile.report(format=args.format))


if __name__ == "__main__":
    asyncio.run(main())
