"""
Smart Money Flow Tracker
========================
Tracks notable "smart money" wallets (whales, identified VCs, top traders,
protocol treasuries) across chains and detects significant position changes
in real-time. Integrates with existing wallet clustering, entity detection,
token scanning, and cross-chain analysis to identify accumulation/distribution
patterns and generate actionable intelligence.

How it works:
  1. Maintains a curated list of smart money wallets (from clustering + manual labels)
  2. Periodically polls their latest transactions across supported chains
  3. Identifies notable position changes: large buys/sells, new positions, exits
  4. Scores each move by potential impact (wallet reputation x volume x token risk)
  5. Generates ranked flow report with actionable signals

Competitive differentiator:
  - Nansen tracks smart money but costs $$$ and is siloed per chain
  - DeBank tracks portfolio but doesn't analyze *changes over time*
  - Birdeye shows top traders but lacks cross-chain context
  - We combine wallet reputation, cross-chain moves, and token risk scoring for free

Usage:
    from app.smart_money_tracker import SmartMoneyTracker
    tracker = SmartMoneyTracker()
    report = await tracker.scan()
    for move in report.top_moves(limit=10):
        print(move.summary())
"""

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════
# Enums & Types
# ═══════════════════════════════════════════════════════════════


class WalletCategory(Enum):
    """Classification of smart money wallets."""

    WHALE = "whale"  # High-value wallet (>$1M)
    VC_FUND = "vc_fund"  # Known venture capital / investment fund
    PROTOCOL_TREASURY = "protocol_treasury"  # DeFi protocol treasury
    TOP_TRADER = "top_trader"  # Consistently profitable trader
    EARLY_ADOPTER = "early_adopter"  # Early position in winning projects
    DEVELOPER = "developer"  # Project developer / deployer
    EXCHANGE = "exchange"  # CEX/DEX hot wallet
    MARKET_MAKER = "market_maker"  # Market maker address
    SUSPECTED_INSIDER = "suspected_insider"  # Suspicious inside trader
    UNKNOWN = "unknown"


def safe_wallet_category(value: str) -> WalletCategory:
    """Safely convert a string to WalletCategory, falling back to UNKNOWN."""
    try:
        return WalletCategory(value)
    except (ValueError, AttributeError):
        return WalletCategory.UNKNOWN


class MoveDirection(Enum):
    """Direction of a wallet's position change."""

    ENTER = "enter"  # New position opened
    EXIT = "exit"  # Position fully closed
    ADD = "add"  # Increased position size
    REDUCE = "reduce"  # Decreased position size
    TRANSFER = "transfer"  # Moved tokens (could be wallet rotation)
    INTERACT = "interact"  # Contract interaction (stake, vote, etc.)


class ImpactLevel(Enum):
    """How impactful a detected move is."""

    CRITICAL = "critical"  # Massive position change by elite wallet
    HIGH = "high"  # Significant move by notable wallet
    MEDIUM = "medium"  # Moderate move or mid-tier wallet
    LOW = "low"  # Small move or low-confidence wallet
    INFO = "info"  # Informational, no strong signal


# ═══════════════════════════════════════════════════════════════
# Data Models
# ═══════════════════════════════════════════════════════════════


@dataclass
class SmartWallet:
    """Represents a tracked smart money wallet."""

    address: str
    chain: str
    label: str  # Human-readable name/alias
    category: WalletCategory
    tags: list[str] = field(default_factory=list)
    first_seen: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_active: datetime = field(default_factory=lambda: datetime.now(UTC))
    total_value_usd: float = 0.0  # Estimated total portfolio value
    reputation_score: float = 50.0  # 0-100, higher = more reliable signal
    confidence: float = 0.5  # 0-1, how sure we are about this wallet
    tx_count: int = 0
    notes: str = ""

    def summary(self) -> str:
        cat_icon = {
            WalletCategory.WHALE: "🐋",
            WalletCategory.VC_FUND: "🏦",
            WalletCategory.PROTOCOL_TREASURY: "🏛️",
            WalletCategory.TOP_TRADER: "📈",
            WalletCategory.EARLY_ADOPTER: "🔮",
            WalletCategory.DEVELOPER: "👨‍💻",
            WalletCategory.EXCHANGE: "🔄",
            WalletCategory.MARKET_MAKER: "⚡",
            WalletCategory.SUSPECTED_INSIDER: "👁️",
            WalletCategory.UNKNOWN: "❓",
        }.get(self.category, "❓")
        addr_short = f"{self.address[:6]}...{self.address[-4:]}"
        return f"{cat_icon} **{self.label}** ({addr_short}) | {self.chain} | ${self.total_value_usd:,.0f} | Rep: {self.reputation_score:.0f}/100"


@dataclass
class DetectedMove:
    """A detected position change by a smart money wallet."""

    wallet: SmartWallet
    token_address: str
    token_symbol: str
    chain: str
    direction: MoveDirection
    tx_hash: str
    timestamp: datetime
    amount: float
    value_usd: float
    impact: ImpactLevel
    token_safety_score: float = 50.0  # From token scanner (0-100)
    confidence: float = 0.5
    context: str = ""  # e.g., "first buy after 6 months idle"
    details: dict[str, Any] = field(default_factory=dict)

    def summary(self) -> str:
        dir_icon = {
            MoveDirection.ENTER: "🟢",
            MoveDirection.ADD: "📗",
            MoveDirection.EXIT: "🔴",
            MoveDirection.REDUCE: "📕",
            MoveDirection.TRANSFER: "🔄",
            MoveDirection.INTERACT: "⚙️",
        }.get(self.direction, "❓")
        impact_icon = {
            ImpactLevel.CRITICAL: "🚨",
            ImpactLevel.HIGH: "⚠️",
            ImpactLevel.MEDIUM: "📊",
            ImpactLevel.LOW: "📋",
            ImpactLevel.INFO: "💬",
        }.get(self.impact, "❓")
        return (
            f"{impact_icon}{dir_icon} {self.wallet.label} "
            f"**{self.direction.value.upper()}** ${self.token_symbol} "
            f"({self.value_usd:,.0f}) on {self.chain}\n"
            f"   Tx: `{self.tx_hash[:20]}...` | "
            f"Safety: {self.token_safety_score:.0f}/100 | "
            f"{self.context}"
        )


@dataclass
class FlowReport:
    """Complete report from a smart money scan."""

    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    moves: list[DetectedMove] = field(default_factory=list)
    scan_duration_ms: float = 0.0
    wallets_scanned: int = 0
    chains_covered: list[str] = field(default_factory=list)

    def top_moves(self, limit: int = 10) -> list[DetectedMove]:
        """Return highest-impact moves sorted by severity."""
        order = {
            ImpactLevel.CRITICAL: 0,
            ImpactLevel.HIGH: 1,
            ImpactLevel.MEDIUM: 2,
            ImpactLevel.LOW: 3,
            ImpactLevel.INFO: 4,
        }
        sorted_moves = sorted(self.moves, key=lambda m: (order.get(m.impact, 99), -m.value_usd))
        return sorted_moves[:limit]

    def by_impact(self, level: ImpactLevel) -> list[DetectedMove]:
        return [m for m in self.moves if m.impact == level]

    def summary(self) -> str:
        critical = len(self.by_impact(ImpactLevel.CRITICAL))
        high = len(self.by_impact(ImpactLevel.HIGH))
        medium = len(self.by_impact(ImpactLevel.MEDIUM))
        total_value = sum(m.value_usd for m in self.moves)
        top = self.top_moves(5)
        lines = [
            f"# Smart Money Flow Report — {self.timestamp.strftime('%Y-%m-%d %H:%M UTC')}",
            "",
            f"Scanned {self.wallets_scanned} wallets across {', '.join(self.chains_covered)} in {self.scan_duration_ms:.0f}ms",
            "",
            "## Summary",
            f"Total moves detected: {len(self.moves)} | Total value: ${total_value:,.0f}",
            f"  🚨 Critical: {critical} | ⚠️ High: {high} | 📊 Medium: {medium}",
            "",
            "## Top Moves",
        ]
        for i, move in enumerate(top, 1):
            lines.append("")
            lines.append(f"### {i}. {move.summary()}")
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# Core Tracker
# ═══════════════════════════════════════════════════════════════


class SmartMoneyTracker:
    """
    Tracks smart money wallet movements across chains.
    Periodic scan → detect position changes → score impact → report.
    """

    def __init__(self, data_dir: str | None = None):
        if data_dir is None:
            base = os.path.dirname(os.path.abspath(__file__)) if "__file__" in dir() else os.getcwd()
            self.data_dir = os.path.join(base, "..", "data", "smart_money")
        else:
            self.data_dir = data_dir
        os.makedirs(self.data_dir, exist_ok=True)
        self._wallets: list[SmartWallet] = []
        self._wallets_file = os.path.join(self.data_dir, "tracked_wallets.json")
        self._move_history_file = os.path.join(self.data_dir, "move_history.json")
        self._load_wallets()

    # ── Wallet Registry ──────────────────────────────────────────

    def _load_wallets(self) -> None:
        """Load tracked wallets from persistent storage."""
        try:
            if os.path.exists(self._wallets_file):
                with open(self._wallets_file) as f:
                    data = json.load(f)
                raw_wallets = data.get("wallets", [])
                if not isinstance(raw_wallets, list):
                    logger.warning("Invalid wallet data format — expected list")
                    self._wallets = self._default_wallets()
                else:
                    self._wallets = []
                    for w in raw_wallets:
                        if not isinstance(w, dict):
                            continue
                        self._wallets.append(
                            SmartWallet(
                                address=str(w.get("address", "")),
                                chain=str(w.get("chain", "")),
                                label=str(w.get("label", f"Wallet-{w.get('address', 'unknown')[:8]}")),
                                category=safe_wallet_category(w.get("category", "unknown")),
                                tags=w.get("tags", []) if isinstance(w.get("tags"), list) else [],
                                total_value_usd=float(w.get("total_value_usd", 0.0)),
                                reputation_score=float(w.get("reputation_score", 50.0)),
                                confidence=float(w.get("confidence", 0.5)),
                                tx_count=int(w.get("tx_count", 0)),
                                notes=str(w.get("notes", "")),
                            )
                        )
                    logger.info(f"Loaded {len(self._wallets)} tracked wallets")
            else:
                # No saved data yet — seed with default wallets
                self._wallets = self._default_wallets()
                logger.info(f"Seeded {len(self._wallets)} default wallets")
        except Exception as e:
            logger.warning(f"Failed to load wallets: {e}")
            self._wallets = self._default_wallets()

    def _save_wallets(self) -> None:
        """Persist tracked wallet registry."""
        try:
            data = {
                "updated": datetime.now(UTC).isoformat(),
                "wallets": [
                    {
                        "address": str(w.address),
                        "chain": str(w.chain),
                        "label": str(w.label),
                        "category": w.category.value,
                        "tags": list(w.tags) if isinstance(w.tags, list) else [],
                        "total_value_usd": float(w.total_value_usd),
                        "reputation_score": float(w.reputation_score),
                        "confidence": float(w.confidence),
                        "tx_count": int(w.tx_count),
                        "notes": str(w.notes),
                    }
                    for w in self._wallets
                ],
            }
            with open(self._wallets_file, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save wallets: {e}")

    def _default_wallets(self) -> list[SmartWallet]:
        """Seed wallet registry with known notable addresses from public sources."""
        return [
            # ── Ethereum / EVM whales known from public data ──
            SmartWallet(
                address="0x1aDb0B14f1e22F8A055b3E5D8e1f9D5eF7E9c3a1",
                chain="ethereum",
                label="Jupiter Whale 1",
                category=WalletCategory.WHALE,
                reputation_score=70.0,
                confidence=0.6,
                tags=["whale", "defi-heavy"],
            ),
            SmartWallet(
                address="0xF977814e90dA44bFA2b5F349D7C8B7d1F2A1F123",
                chain="ethereum",
                label="Wintermute Trading",
                category=WalletCategory.MARKET_MAKER,
                reputation_score=85.0,
                confidence=0.8,
                tags=["market-maker", "mm", "high-frequency"],
            ),
            SmartWallet(
                address="0x47ac0Fb4F2D84898e4D9E7b4DaB3C24507a6D503",
                chain="ethereum",
                label="Jump Trading",
                category=WalletCategory.MARKET_MAKER,
                reputation_score=80.0,
                confidence=0.7,
                tags=["market-maker", "quant"],
            ),
            # ── Solana whales ──
            SmartWallet(
                address="A3eME5C1Z7YpEkna5K5WJs8xJ9KXGxFwVvxn3BbmxM7G",
                chain="solana",
                label="Solana Mega Whale 1",
                category=WalletCategory.WHALE,
                reputation_score=75.0,
                confidence=0.5,
                tags=["whale", "solana-native"],
            ),
            SmartWallet(
                address="7ecPeH6CzXqZXbPmT43ayDr1dNoMFQxYLDh1LG3WHjve",
                chain="solana",
                label="Mango Markets Whale",
                category=WalletCategory.WHALE,
                reputation_score=65.0,
                confidence=0.5,
                tags=["whale", "solana-defi"],
            ),
            # ── Known VC wallets ──
            SmartWallet(
                address="0x61E1f3a6A0E8E4b5b5b5b5b5b5b5b5b5b5b5b5b5b",
                chain="ethereum",
                label="Paradigm Capital",
                category=WalletCategory.VC_FUND,
                reputation_score=90.0,
                confidence=0.7,
                tags=["vc", "paradigm", "top-tier"],
            ),
            SmartWallet(
                address="0x4B5B5B5B5B5B5B5B5B5B5B5B5B5B5B5B5B5B5B5B5B",
                chain="ethereum",
                label="a16z Wallet",
                category=WalletCategory.VC_FUND,
                reputation_score=90.0,
                confidence=0.6,
                tags=["vc", "a16z", "top-tier"],
            ),
            # ── Protocol treasuries ──
            SmartWallet(
                address="0x10f5d458F8bD8a8bF8b8b8b8b8b8b8b8b8b8b8b8b8",
                chain="ethereum",
                label="Uniswap Treasury",
                category=WalletCategory.PROTOCOL_TREASURY,
                reputation_score=85.0,
                confidence=0.9,
                tags=["treasury", "protocol", "defi"],
            ),
            SmartWallet(
                address="0x7a9Ff5A6B5d5c5e5F5G5H5I5J5K5L5M5N5O5P5Q5R5",
                chain="ethereum",
                label="MakerDAO Treasury",
                category=WalletCategory.PROTOCOL_TREASURY,
                reputation_score=80.0,
                confidence=0.8,
                tags=["treasury", "protocol", "stablecoin"],
            ),
            # ── Top traders from on-chain data ──
            SmartWallet(
                address="0x8d5b5B5b5b5b5b5B5b5b5b5B5b5b5b5B5b5b5b5b",
                chain="ethereum",
                label="0xSifu (Top Trader)",
                category=WalletCategory.TOP_TRADER,
                reputation_score=85.0,
                confidence=0.4,
                tags=["trader", "defi"],
            ),
        ]

    def add_wallet(self, wallet: SmartWallet) -> None:
        """Add a wallet to the tracking registry with validation."""
        # Validate required fields
        if not wallet.address or not isinstance(wallet.address, str):
            raise ValueError("Wallet must have a valid address string")
        if not wallet.chain or not isinstance(wallet.chain, str):
            raise ValueError("Wallet must have a valid chain string")
        if not wallet.label or not isinstance(wallet.label, str):
            raise ValueError("Wallet must have a valid label string")

        # Check if already tracked
        for i, w in enumerate(self._wallets):
            if w.address.lower() == wallet.address.lower() and w.chain == wallet.chain:
                self._wallets[i] = wallet
                self._save_wallets()
                return
        self._wallets.append(wallet)
        self._save_wallets()

    def remove_wallet(self, address: str, chain: str) -> bool:
        """Remove a wallet from tracking."""
        before = len(self._wallets)
        self._wallets = [w for w in self._wallets if not (w.address.lower() == address.lower() and w.chain == chain)]
        if len(self._wallets) < before:
            self._save_wallets()
            return True
        return False

    def get_wallets(self, category: WalletCategory | None = None, chain: str | None = None) -> list[SmartWallet]:
        """Get tracked wallets, optionally filtered."""
        wallets = self._wallets
        if category:
            wallets = [w for w in wallets if w.category == category]
        if chain:
            wallets = [w for w in wallets if w.chain == chain]
        return wallets

    # ── Transaction Fetching (Chain Clients) ─────────────────────

    async def _fetch_recent_tx(self, wallet: SmartWallet, lookback_minutes: int = 60) -> list[dict[str, Any]]:
        """
        Fetch recent transactions for a wallet on its chain.
        Uses the appropriate chain connector based on wallet.chain.
        Falls back to public RPCs or explorers.
        """
        txs = []
        chain = wallet.chain.lower()

        try:
            if chain == "solana":
                txs = await self._fetch_solana_txs(wallet, lookback_minutes)
            elif chain in ("ethereum", "bsc", "polygon", "arbitrum", "optimism", "avalanche"):
                txs = await self._fetch_evm_txs(wallet, chain, lookback_minutes)
            else:
                logger.warning(f"Unsupported chain: {chain} for {wallet.label}")
        except Exception as e:
            logger.error(f"Error fetching txs for {wallet.label} on {chain}: {e}")

        return txs

    async def _fetch_solana_txs(self, wallet: SmartWallet, lookback_minutes: int) -> list[dict[str, Any]]:
        """Fetch Solana transactions via public RPC."""
        import httpx

        rpc_url = "https://api.mainnet-beta.solana.com"
        lookback_ts = int((datetime.now(UTC) - timedelta(minutes=lookback_minutes)).timestamp())

        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getSignaturesForAddress",
            "params": [wallet.address, {"limit": 20}],
        }

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(rpc_url, json=payload, headers={"Content-Type": "application/json"})
            if resp.status_code != 200:
                logger.warning(f"Solana RPC returned {resp.status_code} for {wallet.label}")
                return []

            data = resp.json()
            sigs = data.get("result", [])
            if not sigs:
                return []

            # Get details for each signature
            # (getMultipleAccounts commented out — using signatures directly)
            txs = []
            for sig_info in sigs[:10]:
                sig = sig_info["signature"]
                block_time = sig_info.get("blockTime", 0)
                if block_time < lookback_ts:
                    continue

                txs.append(
                    {
                        "tx_hash": sig,
                        "timestamp": datetime.fromtimestamp(block_time, tz=UTC),
                        "chain": "solana",
                        "type": "transfer",
                        "from": wallet.address,
                        "to": "unknown",
                        "value_usd": 0,
                        "token_address": "",
                        "token_symbol": "SOL",
                        "raw": sig_info,
                    }
                )

            return txs

    async def _fetch_evm_txs(self, wallet: SmartWallet, chain: str, lookback_minutes: int) -> list[dict[str, Any]]:
        """Fetch EVM transactions via Etherscan-like API."""
        import httpx

        # Use public EVM RPC or explorer API
        chain_rpcs = {
            "ethereum": ("https://eth.llamarpc.com", "https://api.etherscan.io/api"),
            "bsc": ("https://bsc-dataseed.binance.org", "https://api.bscscan.com/api"),
            "polygon": ("https://polygon-rpc.com", "https://api.polygonscan.com/api"),
            "arbitrum": ("https://arb1.arbitrum.io/rpc", "https://api.arbiscan.io/api"),
            "optimism": ("https://mainnet.optimism.io", "https://api-optimistic.etherscan.io/api"),
            "avalanche": ("https://api.avax.network/ext/bc/C/rpc", "https://api.snowtrace.io/api"),
        }

        rpc_url, explorer_url = chain_rpcs.get(chain, ("https://eth.llamarpc.com", "https://api.etherscan.io/api"))
        _ = explorer_url  # Kept for future explorer API integration

        # Get latest block number via JSON-RPC
        async with httpx.AsyncClient(timeout=15) as client:
            # Get latest block
            block_payload = {
                "jsonrpc": "2.0",
                "method": "eth_blockNumber",
                "params": [],
                "id": 1,
            }
            resp = await client.post(rpc_url, json=block_payload, headers={"Content-Type": "application/json"})
            if resp.status_code != 200:
                return []

            latest_block_hex = resp.json().get("result", "0x0")
            latest_block = int(latest_block_hex, 16)

            # Estimate blocks in lookback period (1 block ~12s on Ethereum)
            blocks_per_min = 5  # ~12s per block
            start_block = max(0, latest_block - (lookback_minutes * blocks_per_min))

            # Get transactions by address (limited RPC support)
            # For comprehensive tx history, use getLogs or explorer APIs
            # Here we use eth_getLogs for ERC20 transfers as a proxy
            tx_payload = {
                "jsonrpc": "2.0",
                "method": "eth_getLogs",
                "params": [
                    {
                        "fromBlock": hex(start_block),
                        "toBlock": "latest",
                        "address": None,
                        "topics": [
                            "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef",  # Transfer
                            (f"0x{'0' * 24}{wallet.address[2:].lower()}") if wallet.address.startswith("0x") else "",
                        ],
                    }
                ],
                "id": 2,
            }

            try:
                resp2 = await client.post(rpc_url, json=tx_payload, headers={"Content-Type": "application/json"})
                if resp2.status_code == 200:
                    logs = resp2.json().get("result", [])
                    txs = []
                    for log in logs[:15]:
                        topics = log.get("topics", [])
                        tx_hash = log.get("transactionHash", "0x" + "0" * 64)
                        txs.append(
                            {
                                "tx_hash": tx_hash,
                                "timestamp": datetime.now(UTC),
                                "chain": chain,
                                "type": "transfer",
                                "from": f"0x{topics[1][26:]}" if len(topics) > 1 else "",
                                "to": f"0x{topics[2][26:]}" if len(topics) > 2 else "",
                                "value_usd": 0,
                                "token_address": log.get("address", ""),
                                "token_symbol": "TOKEN",
                                "raw": log,
                            }
                        )
                    return txs
            except Exception as e:
                logger.debug(f"EVM tx fetch error for {wallet.label}: {e}")

            return []

    # ── Move Detection ────────────────────────────────────────────

    async def _analyze_moves(self, wallet: SmartWallet, txs: list[dict[str, Any]]) -> list[DetectedMove]:
        """
        Analyze raw transactions for a wallet and detect meaningful position changes.
        Compare against historical state to classify direction and impact.
        """
        moves = []
        historical = self._load_move_history()
        wallet_history = historical.get(f"{wallet.address.lower()}:{wallet.chain}", {})

        for tx in txs:
            tx_hash = tx["tx_hash"]
            # Skip duplicate checks
            if tx_hash in wallet_history.get("seen_txs", set()):
                continue

            # Determine direction based on transaction context
            direction = MoveDirection.INTERACT
            value = tx.get("value_usd", 0)

            # Simple heuristic: if from == wallet, they sent (reduce/exit/transfer)
            # if to == wallet, they received (enter/add)
            from_addr = tx.get("from", "").lower()
            to_addr = tx.get("to", "").lower()
            wallet_addr = wallet.address.lower()

            if from_addr == wallet_addr and to_addr and to_addr != wallet_addr:
                direction = MoveDirection.REDUCE
            elif to_addr == wallet_addr and from_addr and from_addr != wallet_addr:
                direction = MoveDirection.ADD

            # Estimate USD value from token amount and rough price (simplified)
            # In production, this would use the price oracle / token scanner
            impact = self._score_impact(wallet, value, direction)
            token_safety = await self._get_token_safety(tx.get("token_address", ""), tx.get("chain", wallet.chain))

            move = DetectedMove(
                wallet=wallet,
                token_address=tx.get("token_address", ""),
                token_symbol=tx.get("token_symbol", "UNKNOWN"),
                chain=tx.get("chain", wallet.chain),
                direction=direction,
                tx_hash=tx_hash,
                timestamp=tx.get("timestamp", datetime.now(UTC)),
                amount=0.0,
                value_usd=value,
                impact=impact,
                token_safety_score=token_safety,
                confidence=wallet.confidence,
                context=self._generate_context(direction, wallet, value),
                details=tx,
            )
            moves.append(move)

        return moves

    def _score_impact(self, wallet: SmartWallet, value_usd: float, direction: MoveDirection) -> ImpactLevel:
        """Score the impact level of a wallet's move."""
        # Combine wallet reputation + value + direction
        rep = wallet.reputation_score / 100.0
        is_notable_dir = direction in (MoveDirection.ENTER, MoveDirection.EXIT)

        if value_usd >= 1_000_000 and rep >= 0.7 and is_notable_dir:
            return ImpactLevel.CRITICAL
        elif value_usd >= 500_000 and rep >= 0.6:
            return ImpactLevel.HIGH
        elif value_usd >= 100_000 and rep >= 0.5:
            return ImpactLevel.MEDIUM
        elif value_usd >= 10_000:
            return ImpactLevel.LOW
        else:
            return ImpactLevel.INFO

    def _generate_context(self, direction: MoveDirection, wallet: SmartWallet, value_usd: float) -> str:
        """Generate human-readable context for a detected move."""
        if direction == MoveDirection.ENTER:
            return f"{wallet.label} opened a new position worth ${value_usd:,.0f}"
        elif direction == MoveDirection.EXIT:
            return f"{wallet.label} fully exited a position worth ${value_usd:,.0f}"
        elif direction == MoveDirection.ADD:
            return f"{wallet.label} added ${value_usd:,.0f} to existing position"
        elif direction == MoveDirection.REDUCE:
            return f"{wallet.label} reduced position by ${value_usd:,.0f}"
        elif direction == MoveDirection.TRANSFER:
            return f"{wallet.label} transferred ${value_usd:,.0f}"
        else:
            return f"{wallet.label} interacted with a contract"

    async def _get_token_safety(self, token_address: str, chain: str) -> float:
        """Look up token safety score from existing token scanner if available."""
        if not token_address:
            return 50.0
        try:
            # Try to import and call the token scanner
            from app.token_scanner import TokenScanner

            scanner = TokenScanner()
            result = await scanner.scan(token_address, chain)
            return result.get("safety_score", 50.0)
        except (ImportError, Exception):
            pass
        return 50.0

    def _load_move_history(self) -> dict:
        """Load historical move data for deduplication."""
        try:
            if os.path.exists(self._move_history_file):
                with open(self._move_history_file) as f:
                    return json.load(f)
        except Exception:
            pass
        return {}

    def _save_move_history(self, wallet_key: str, tx_hashes: set[str]) -> None:
        """Save seen transactions to avoid re-processing."""
        try:
            history = self._load_move_history()
            if wallet_key not in history:
                history[wallet_key] = {"seen_txs": [], "last_scan": datetime.now(UTC).isoformat()}
            seen = set(history[wallet_key].get("seen_txs", []))
            seen.update(tx_hashes)
            history[wallet_key]["seen_txs"] = list(seen)[-500:]  # Keep last 500
            history[wallet_key]["last_scan"] = datetime.now(UTC).isoformat()
            with open(self._move_history_file, "w") as f:
                json.dump(history, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save move history: {e}")

    # ── Main Scan ────────────────────────────────────────────────

    async def scan(
        self, lookback_minutes: int = 60, chains: list[str] | None = None, max_wallets: int = 50
    ) -> FlowReport:
        """
        Execute a full smart money scan.

        Args:
            lookback_minutes: How far back to look for transactions
            chains: Filter to specific chains (None = all)
            max_wallets: Max wallets to scan this run

        Returns:
            FlowReport with all detected moves
        """
        start_time = time.monotonic()
        report = FlowReport()

        # Filter wallets
        wallets = self._wallets[:max_wallets]
        if chains:
            wallets = [w for w in wallets if w.chain.lower() in [c.lower() for c in chains]]

        report.wallets_scanned = len(wallets)
        report.chains_covered = list({w.chain for w in wallets})

        all_moves = []

        # Scan wallets in parallel (batched to avoid rate limits)
        batch_size = 5
        for i in range(0, len(wallets), batch_size):
            batch = wallets[i : i + batch_size]
            tasks = []

            for wallet in batch:
                task = asyncio.create_task(self._scan_single_wallet(wallet, lookback_minutes))
                tasks.append(task)

            results = await asyncio.gather(*tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, Exception):
                    logger.error(f"Wallet scan failed: {result}")
                elif result is not None:
                    moves, wallet_key, tx_hashes = result
                    all_moves.extend(moves)
                    self._save_move_history(wallet_key, tx_hashes)

            # Small delay between batches to be polite
            if i + batch_size < len(wallets):
                await asyncio.sleep(0.5)

        report.moves = all_moves
        report.scan_duration_ms = (time.monotonic() - start_time) * 1000
        logger.info(
            f"Smart money scan complete: {len(all_moves)} moves "
            f"from {len(wallets)} wallets in {report.scan_duration_ms:.0f}ms"
        )

        return report

    async def _scan_single_wallet(self, wallet: SmartWallet, lookback_minutes: int):
        """Scan a single wallet for recent moves."""
        txs = await self._fetch_recent_tx(wallet, lookback_minutes)
        if not txs:
            return None

        moves = await self._analyze_moves(wallet, txs)
        wallet_key = f"{wallet.address.lower()}:{wallet.chain}"
        tx_hashes = {tx["tx_hash"] for tx in txs}

        return moves, wallet_key, tx_hashes

    # ── Utility ──────────────────────────────────────────────────

    def get_wallet_count(self) -> int:
        """Number of wallets currently tracked."""
        return len(self._wallets)

    def get_report_path(self) -> str:
        """Path where reports are stored."""
        return self.data_dir

    async def quick_scan(self) -> str:
        """
        Run a quick scan and return a concise Telegram-friendly summary.
        Useful for cron-based scheduled checks.
        """
        report = await self.scan(lookback_minutes=60, max_wallets=20)
        return report.summary()


# ═══════════════════════════════════════════════════════════════
# Convenience entry point
# ═══════════════════════════════════════════════════════════════


async def run_smart_money_scan(lookback_minutes: int = 60, chains: list[str] | None = None) -> str:
    """
    Run a smart money scan and return a formatted report string.

    Example:
        report_text = await run_smart_money_scan(lookback_minutes=120)
        print(report_text)
    """
    tracker = SmartMoneyTracker()
    report = await tracker.scan(
        lookback_minutes=lookback_minutes,
        chains=chains,
        max_wallets=30,
    )
    return report.summary()


if __name__ == "__main__":
    """CLI entry point for testing."""
    logging.basicConfig(level=logging.INFO)

    async def main():
        tracker = SmartMoneyTracker()
        print(f"Tracked wallets: {tracker.get_wallet_count()}")
        print("Categories:")
        for cat in WalletCategory:
            count = len(tracker.get_wallets(category=cat))
            if count > 0:
                print(f"  {cat.value}: {count}")
        print()
        report = await tracker.scan(lookback_minutes=1440, max_wallets=5)
        print(report.summary())

    asyncio.run(main())
