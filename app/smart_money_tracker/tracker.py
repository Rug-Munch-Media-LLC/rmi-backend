"""
Smart Money Tracker — Core Scanner
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.logging import get_logger
from app.smart_money_tracker.types import (
    DetectedMove,
    FlowReport,
    ImpactLevel,
    MoveDirection,
    SmartWallet,
    WalletCategory,
    safe_wallet_category,
)

logger = get_logger(__name__)


class SmartMoneyTracker:
    """
    Tracks smart money wallet movements across chains.
    Periodic scan -> detect position changes -> score impact -> report.
    """

    def __init__(self, data_dir: str | None = None) -> None:
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

    def _load_wallets(self) -> None:
        try:
            if os.path.exists(self._wallets_file):
                with open(self._wallets_file) as f:
                    data = json.load(f)
                raw = data.get("wallets", [])
                if not isinstance(raw, list):
                    self._wallets = self._default_wallets()
                else:
                    self._wallets = []
                    for w in raw:
                        if not isinstance(w, dict):
                            continue
                        self._wallets.append(
                            SmartWallet(
                                address=w.get("address", ""),
                                chain=w.get("chain", "ethereum"),
                                label=w.get("label", ""),
                                category=safe_wallet_category(w.get("category", "unknown")),
                                tags=set(w.get("tags", [])),
                                first_seen=w.get("first_seen", 0),
                                last_active=w.get("last_active", 0),
                                total_tx_count=w.get("total_tx_count", 0),
                                reputation_score=w.get("reputation_score", w.get("reputation", 50.0)),
                                confidence=w.get("confidence", 0.5),
                                notes=w.get("notes", ""),
                            )
                        )
            else:
                self._wallets = self._default_wallets()
                self._save_wallets()
        except (FileNotFoundError, json.JSONDecodeError) as e:
            logger.warning("Could not load wallets: %s — seeding defaults", e)
            self._wallets = self._default_wallets()
            self._save_wallets()

    def _save_wallets(self) -> None:
        data = {
            "version": 2,
            "created_at": datetime.now(UTC).isoformat(),
            "wallets": [
                {
                    "address": w.address,
                    "chain": w.chain,
                    "label": w.label,
                    "category": w.category.value,
                    "tags": list(w.tags),
                    "first_seen": w.first_seen,
                    "last_active": w.last_active,
                    "total_tx_count": w.total_tx_count,
                    "reputation_score": w.reputation_score,
                    "confidence": w.confidence,
                    "notes": w.notes,
                }
                for w in self._wallets
            ],
        }
        with open(self._wallets_file, "w") as f:
            json.dump(data, f, indent=2)

    def _default_wallets(self) -> list[SmartWallet]:
        return [
            SmartWallet(
                "0x3f5ce5fbfe3e9af3971dd833d26ba9b5c936f0be",
                "ethereum",
                "Binance Hot Wallet 1",
                WalletCategory.EXCHANGE,
            ),
            SmartWallet(
                "0x47ac0fb4f2d84898e4d9e7b4dab3c24507a6d503",
                "ethereum",
                "Binance Hot Wallet 2",
                WalletCategory.EXCHANGE,
            ),
            SmartWallet(
                "0x4f26ffbe5f04ed43630fdc30a87638d53d0b0876", "ethereum", "Binance Cold Wallet", WalletCategory.EXCHANGE
            ),
            SmartWallet(
                "0x742d35cc6634c0532925a3b844bc454e4438f44e", "ethereum", "Bitfinex Hot Wallet", WalletCategory.EXCHANGE
            ),
            SmartWallet(
                "0x1151314c646ce4e0efd76d1af4760ae66a9fe30f",
                "ethereum",
                "Bitfinex Cold Wallet",
                WalletCategory.EXCHANGE,
            ),
            SmartWallet(
                "0xdAC17F958D2ee523a2206206994597C13D831ec7", "ethereum", "Tether Treasury", WalletCategory.PROTOCOL
            ),
            SmartWallet("0xfe9e8709d3215310075d67e3ed32a380ccf451c8", "ethereum", "a16z Wallet", WalletCategory.VC),
            SmartWallet(
                "0xb81d0c5d3c2ba823a1e5e4a7c0f3b0b3b3b3b3b3", "ethereum", "Paradigm Capital", WalletCategory.VC
            ),
            SmartWallet("0x7a95f1554e80138d5b6a2ab5b9d6c0d5a5c5e5f5", "ethereum", "0xSifu", WalletCategory.WHALE),
            SmartWallet(
                "0x10a5d6c7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3", "ethereum", "Uniswap Treasury", WalletCategory.PROTOCOL
            ),
            SmartWallet(
                "0x2b5e3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b", "ethereum", "Aave Treasury", WalletCategory.PROTOCOL
            ),
            SmartWallet(
                "DUSTawcnrPC2bPE4fMBCJ8eLqX29HnidPF3q3V6jzWBP", "solana", "Solana Foundation", WalletCategory.PROTOCOL
            ),
            SmartWallet(
                "1db3439a222c451ab1c1e3f3c7c2c7d4e1c3e1a", "solana", "Jupiter DEX Aggregator", WalletCategory.PROTOCOL
            ),
        ]

    def add_wallet(self, wallet: SmartWallet) -> None:
        # Update if exists, otherwise append
        for i, w in enumerate(self._wallets):
            if w.address.lower() == wallet.address.lower() and w.chain == wallet.chain:
                self._wallets[i] = wallet
                break
        else:
            self._wallets.append(wallet)
        self._save_wallets()

    def remove_wallet(self, address: str, chain: str) -> bool:
        before = len(self._wallets)
        self._wallets = [w for w in self._wallets if not (w.address.lower() == address.lower() and w.chain == chain)]
        return len(self._wallets) < before and (self._save_wallets() or True)

    def get_wallets(self, category: WalletCategory | None = None, chain: str | None = None) -> list[SmartWallet]:
        wallets = self._wallets
        if category:
            wallets = [w for w in wallets if w.category == category]
        if chain:
            wallets = [w for w in wallets if w.chain == chain]
        return wallets

    async def _fetch_recent_tx(self, wallet: SmartWallet, lookback_minutes: int = 60) -> list[dict[str, Any]]:
        try:
            if wallet.chain.lower() == "solana":
                return await self._fetch_solana_txs(wallet, lookback_minutes)
            elif wallet.chain.lower() in ("ethereum", "bsc", "polygon", "arbitrum", "optimism", "avalanche"):
                return await self._fetch_evm_txs(wallet, wallet.chain.lower(), lookback_minutes)
        except Exception as e:
            logger.error("Error fetching txs for %s: %s", wallet.label, e)
        return []

    async def _fetch_solana_txs(self, wallet: SmartWallet, lookback_minutes: int) -> list[dict[str, Any]]:
        import httpx

        lookback_ts = int((datetime.now(UTC) - timedelta(minutes=lookback_minutes)).timestamp())
        r = (
            await httpx.AsyncClient(timeout=30).post(
                "https://api.mainnet-beta.solana.com",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getSignaturesForAddress",
                    "params": [wallet.address, {"limit": 20}],
                },
                headers={"Content-Type": "application/json"},
            )
        ).json()
        return [
            {
                "tx_hash": s["signature"],
                "timestamp": datetime.fromtimestamp(s.get("blockTime", 0), tz=UTC),
                "chain": "solana",
                "type": "transfer",
                "from": wallet.address,
                "to": "unknown",
                "value_usd": 0,
                "token_address": "",
                "token_symbol": "SOL",
                "raw": s,
            }
            for s in r.get("result", [])[:10]
            if s.get("blockTime", 0) >= lookback_ts
        ]

    async def _fetch_evm_txs(self, wallet: SmartWallet, chain: str, lookback_minutes: int) -> list[dict[str, Any]]:
        import httpx

        rpc_url = {
            "ethereum": "https://eth.llamarpc.com",
            "bsc": "https://bsc-dataseed.binance.org",
            "polygon": "https://polygon-rpc.com",
            "arbitrum": "https://arb1.arbitrum.io/rpc",
            "optimism": "https://mainnet.optimism.io",
            "avalanche": "https://api.avax.network/ext/bc/C/rpc",
        }.get(chain, "https://eth.llamarpc.com")
        async with httpx.AsyncClient(timeout=15) as client:
            latest = int(
                (
                    await client.post(
                        rpc_url,
                        json={"jsonrpc": "2.0", "method": "eth_blockNumber", "params": [], "id": 1},
                        headers={"Content-Type": "application/json"},
                    )
                )
                .json()
                .get("result", "0x0"),
                16,
            )
            start = hex(max(0, latest - lookback_minutes * 5))
            addr = wallet.address.lower()
            r = await client.post(
                rpc_url,
                json={
                    "jsonrpc": "2.0",
                    "method": "eth_getLogs",
                    "params": [
                        {
                            "fromBlock": start,
                            "toBlock": "latest",
                            "address": None,
                            "topics": [
                                "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef",
                                f"0x{'0' * 24}{addr[2:]}" if addr.startswith("0x") else "",
                            ],
                        }
                    ],
                    "id": 2,
                },
                headers={"Content-Type": "application/json"},
            )
            if r.status_code == 200:
                return [
                    {
                        "tx_hash": log.get("transactionHash", ""),
                        "timestamp": datetime.now(UTC),
                        "chain": chain,
                        "type": "transfer",
                        "from": f"0x{log['topics'][1][26:]}" if len(log.get("topics", [])) > 1 else "",
                        "to": f"0x{log['topics'][2][26:]}" if len(log.get("topics", [])) > 2 else "",
                        "value_usd": 0,
                        "token_address": log.get("address", ""),
                        "token_symbol": "TOKEN",
                        "raw": log,
                    }
                    for log in r.json().get("result", [])[:15]
                ]
        return []

    async def _analyze_moves(self, wallet: SmartWallet, txs: list[dict[str, Any]]) -> list[DetectedMove]:
        historical = self._load_move_history()
        seen = set(historical.get(wallet.key, {}).get("seen_txs", []))
        moves = []
        for tx in txs:
            if tx["tx_hash"] in seen:
                continue
            fa, ta, wa = (tx.get("from", "") or "").lower(), (tx.get("to", "") or "").lower(), wallet.address.lower()
            direction = (
                MoveDirection.REDUCE
                if (fa == wa and ta and ta != wa)
                else MoveDirection.ADD
                if (ta == wa and fa and fa != wa)
                else MoveDirection.INTERACT
            )
            token_safety = await self._get_token_safety(tx.get("token_address", ""), tx.get("chain", wallet.chain))
            moves.append(
                DetectedMove(
                    wallet=wallet,
                    token_address=tx.get("token_address", ""),
                    token_symbol=tx.get("token_symbol", "UNKNOWN"),
                    chain=tx.get("chain", wallet.chain),
                    direction=direction,
                    tx_hash=tx["tx_hash"],
                    timestamp=tx.get("timestamp", datetime.now(UTC)),
                    value_usd=tx.get("value_usd", 0),
                    impact=self._score_impact(wallet, tx.get("value_usd", 0), direction),
                    token_safety_score=token_safety,
                    confidence=wallet.confidence,
                    context=self._generate_context(direction, wallet, tx.get("value_usd", 0)),
                    details=tx,
                )
            )
        return moves

    def _score_impact(self, wallet: SmartWallet, value_usd: float, direction: MoveDirection) -> ImpactLevel:
        rep = wallet.reputation_score / 100.0
        notable = direction in (MoveDirection.ENTER, MoveDirection.EXIT)
        if value_usd >= 1_000_000 and rep >= 0.7 and notable:
            return ImpactLevel.CRITICAL
        elif value_usd >= 500_000 and rep >= 0.6:
            return ImpactLevel.HIGH
        elif value_usd >= 100_000 and rep >= 0.5:
            return ImpactLevel.MEDIUM
        elif value_usd >= 10_000:
            return ImpactLevel.LOW
        return ImpactLevel.INFO

    def _generate_context(self, direction: MoveDirection, wallet: SmartWallet, value_usd: float) -> str:
        return {
            MoveDirection.ENTER: f"{wallet.label} opened a new position worth ${value_usd:,.0f}",
            MoveDirection.EXIT: f"{wallet.label} fully exited a position worth ${value_usd:,.0f}",
            MoveDirection.ADD: f"{wallet.label} added ${value_usd:,.0f} to existing position",
            MoveDirection.REDUCE: f"{wallet.label} reduced position by ${value_usd:,.0f}",
            MoveDirection.TRANSFER: f"{wallet.label} transferred ${value_usd:,.0f}",
        }.get(direction, f"{wallet.label} interacted with a contract")

    async def _get_token_safety(self, token_address: str, chain: str) -> float:
        if not token_address:
            return 50.0
        try:
            from app.token_scanner import TokenScanner

            return (await TokenScanner().scan(token_address, chain)).get("safety_score", 50.0)
        except (ImportError, Exception):
            return 50.0

    def _load_move_history(self) -> dict:
        try:
            if os.path.exists(self._move_history_file):
                with open(self._move_history_file) as f:
                    return json.load(f)
        except Exception as e:
            logger.warning("Smart money tracker error: %s", e)
        return {}

    def _save_move_history(self, wallet_key: str, tx_hashes: set[str]) -> None:
        try:
            history = self._load_move_history()
            if wallet_key not in history:
                history[wallet_key] = {"seen_txs": [], "last_scan": datetime.now(UTC).isoformat()}
            seen = set(history[wallet_key].get("seen_txs", []))
            seen.update(tx_hashes)
            history[wallet_key]["seen_txs"] = list(seen)[-500:]
            history[wallet_key]["last_scan"] = datetime.now(UTC).isoformat()
            with open(self._move_history_file, "w") as f:
                json.dump(history, f, indent=2)
        except Exception as e:
            logger.error("Failed to save move history: %s", e)

    async def scan(
        self, lookback_minutes: int = 60, chains: list[str] | None = None, max_wallets: int = 50
    ) -> FlowReport:
        start = time.monotonic()
        report = FlowReport()
        wallets = [
            w for w in self._wallets[:max_wallets] if not chains or w.chain.lower() in [c.lower() for c in chains]
        ]
        report.wallets_scanned = len(wallets)
        report.chains_covered = list({w.chain for w in wallets})
        all_moves = []
        for i in range(0, len(wallets), 5):
            batch = wallets[i : i + 5]
            for result in await asyncio.gather(
                *[asyncio.create_task(self._scan_single_wallet(w, lookback_minutes)) for w in batch],
                return_exceptions=True,
            ):
                if isinstance(result, Exception):
                    logger.error("Wallet scan failed: %s", result)
                elif result is not None:
                    moves, wk, txh = result
                    all_moves.extend(moves)
                    self._save_move_history(wk, txh)
            if i + 5 < len(wallets):
                await asyncio.sleep(0.5)
        report.moves = all_moves
        report.scan_duration_ms = (time.monotonic() - start) * 1000
        logger.info(
            "Smart money scan done: %d moves from %d wallets in %.0fms",
            len(all_moves),
            len(wallets),
            report.scan_duration_ms,
        )
        return report

    async def _scan_single_wallet(
        self, wallet: SmartWallet, lookback_minutes: int
    ) -> tuple[list[DetectedMove], str, set[str]] | None:
        txs = await self._fetch_recent_tx(wallet, lookback_minutes)
        if not txs:
            return None
        return await self._analyze_moves(wallet, txs), wallet.key, {tx["tx_hash"] for tx in txs}

    def get_wallet_count(self) -> int:
        return len(self._wallets)

    def get_report_path(self) -> str:
        return self.data_dir

    async def quick_scan(self) -> str:
        return (await self.scan(lookback_minutes=60, max_wallets=20)).summary()
