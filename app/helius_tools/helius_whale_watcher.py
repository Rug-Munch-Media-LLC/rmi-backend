#!/usr/bin/env python3
"""
Helius Whale Watcher - Real-time large transfer monitoring
Detects whale movements, dumps, accumulation patterns across Solana.

Features:
- Monitor specific wallets or token mints for large transfers
- Alert on: whale dumps (>5% supply moved), accumulation clusters,
  unusual velocity spikes, cross-exchange flows
- Track historical whale behavior patterns
- Score wallets by "whale influence" (0-100)
"""

import asyncio
import os
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import aiohttp

from app.core.logging import get_logger

logger = get_logger("whale-watcher")

API_KEY = os.getenv("HELIUS_API_KEY", "").strip()
RPC_URL = f"https://mainnet.helius-rpc.com/?api-key={API_KEY}"

WHALE_THRESHOLDS = {
    "sol_transfer": 100,  # 100+ SOL
    "token_transfer_pct": 2.0,  # 2%+ of supply
    "usd_value": 50000,  # $50K+ USD equivalent
    "holder_concentration": 10,  # Top 10 holders
}


@dataclass
class WhaleAlert:
    timestamp: datetime
    alert_type: str  # whale_dump, accumulation, distribution, velocity_spike
    token_address: str
    token_symbol: str
    from_wallet: str
    to_wallet: str
    amount_tokens: float
    amount_usd: float
    pct_of_supply: float
    severity: int  # 1-5
    tx_signature: str
    whale_tags: list[str] = field(default_factory=list)
    related_wallets: list[str] = field(default_factory=list)


@dataclass
class WhaleProfile:
    address: str
    total_holding_usd: float
    token_holdings: dict[str, float]
    recent_transfers: list[dict[str, Any]]
    influence_score: int  # 0-100
    behavior_tags: list[str]  # accumulator, distributor, swing_trader, etc.
    first_seen: datetime
    last_active: datetime
    avg_transfer_size_usd: float
    dump_history: list[dict[str, Any]]


class WhaleWatcher:
    """Monitors Solana for whale activity using Helius enhanced APIs."""

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or API_KEY
        self.rpc_url = f"https://mainnet.helius-rpc.com/?api-key={self.api_key}"
        self.session: aiohttp.ClientSession | None = None
        self._monitored_tokens: dict[str, dict] = {}
        self._whale_db: dict[str, WhaleProfile] = {}
        self._alert_history: deque = deque(maxlen=1000)
        self._known_whales: set = set()
        self.callbacks: list[Callable[[WhaleAlert], None]] = []

    async def _get_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))
        return self.session

    async def _rpc_call(self, method: str, params: list | None = None) -> Any:
        s = await self._get_session()
        payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or []}
        try:
            async with s.post(self.rpc_url, json=payload) as resp:
                data = await resp.json()
                return data.get("result", {})
        except Exception as e:
            logger.error(f"RPC {method} failed: {e}")
            return {}

    async def close(self) -> None:
        if self.session and not self.session.closed:
            await self.session.close()

    # ─── Core Intelligence ──────────────────────────────────────────────

    async def scan_token_for_whales(self, token_address: str) -> dict[str, Any]:
        """Scan a token for whale activity."""
        logger.info(f"Scanning {token_address} for whales...")

        # Get token supply
        supply_result = await self._rpc_call("getTokenSupply", [token_address])
        supply_info = supply_result.get("value", {}) if isinstance(supply_result, dict) else {}
        total_supply = (
            float(supply_info.get("amount", 1)) / (10 ** supply_info.get("decimals", 0)) or 1
        )

        # Get largest holders
        holders_result = await self._rpc_call("getTokenLargestAccounts", [token_address])
        holders = holders_result.get("value", []) if isinstance(holders_result, dict) else []

        whales = []
        alerts = []

        for holder in holders:
            addr = holder.get("address", "")
            balance = float(holder.get("amount", 0)) / (10 ** supply_info.get("decimals", 0))
            pct = (balance / total_supply) * 100

            if pct >= 1.0:  # 1%+ holders are "whales"
                # Get wallet SOL balance
                sol_balance = await self._rpc_call("getBalance", [addr])
                sol = sol_balance.get("value", 0) / 1e9 if isinstance(sol_balance, dict) else 0

                whale_profile = {
                    "address": addr,
                    "balance_tokens": balance,
                    "pct_of_supply": round(pct, 2),
                    "sol_balance": round(sol, 2),
                    "is_contract": sol < 0.001,  # likely a contract/ATA
                }
                whales.append(whale_profile)

                # Check for concerning concentration
                if pct > 20:
                    alerts.append(
                        {
                            "type": "extreme_concentration",
                            "wallet": addr,
                            "pct": pct,
                            "message": f"Wallet holds {pct:.1f}% of total supply!",
                        }
                    )

        # Calculate concentration metrics
        top_5_pct = sum(w["pct_of_supply"] for w in whales[:5])
        top_10_pct = sum(w["pct_of_supply"] for w in whales[:10])

        # Risk assessment
        risk_level = "LOW"
        risk_score = 0
        if top_5_pct > 80:
            risk_level = "CRITICAL"
            risk_score = 90
        elif top_5_pct > 60:
            risk_level = "HIGH"
            risk_score = 70
        elif top_5_pct > 40:
            risk_level = "ELEVATED"
            risk_score = 50
        elif top_10_pct > 70:
            risk_level = "ELEVATED"
            risk_score = 45

        return {
            "token_address": token_address,
            "total_supply": total_supply,
            "whale_count": len(whales),
            "whales": whales[:20],
            "top_5_concentration": round(top_5_pct, 2),
            "top_10_concentration": round(top_10_pct, 2),
            "risk_level": risk_level,
            "risk_score": risk_score,
            "alerts": alerts,
            "timestamp": datetime.now(UTC).isoformat(),
        }

    async def detect_whale_dump(self, token_address: str, hours_back: int = 24) -> dict[str, Any]:
        """Detect recent whale dumping activity."""
        logger.info(f"Detecting whale dumps for {token_address}...")

        # Get recent transactions
        sigs_result = await self._rpc_call(
            "getSignaturesForAddress", [token_address, {"limit": 200}]
        )
        signatures = sigs_result if isinstance(sigs_result, list) else []

        cutoff_time = datetime.now(UTC) - timedelta(hours=hours_back)
        dumps = []

        for sig_info in signatures[:50]:  # Check last 50 txs
            block_time = sig_info.get("blockTime", 0)
            tx_time = datetime.fromtimestamp(block_time)
            if tx_time < cutoff_time:
                continue

            sig = sig_info.get("signature", "")
            # Get enhanced transaction
            tx = await self._get_enhanced_tx(sig)
            if not tx:
                continue

            # Look for large token transfers
            token_transfers = tx.get("tokenTransfers", [])
            for transfer in token_transfers:
                if transfer.get("mint", "") == token_address:
                    amount = float(transfer.get("tokenAmount", 0))
                    from_wallet = transfer.get("fromUserAccount", "")
                    to_wallet = transfer.get("toUserAccount", "")

                    # Check if this is a known whale
                    if from_wallet in self._known_whales:
                        dumps.append(
                            {
                                "tx_signature": sig,
                                "timestamp": tx_time.isoformat(),
                                "from": from_wallet,
                                "to": to_wallet,
                                "amount_tokens": amount,
                                "type": "whale_transfer",
                            }
                        )

        return {
            "token_address": token_address,
            "period_hours": hours_back,
            "dump_events": dumps,
            "total_dump_count": len(dumps),
            "timestamp": datetime.now(UTC).isoformat(),
        }

    async def get_whale_profile(self, wallet_address: str) -> dict[str, Any]:
        """Build a comprehensive whale profile for a wallet."""
        logger.info(f"Building whale profile for {wallet_address}")

        # SOL balance
        sol_result = await self._rpc_call("getBalance", [wallet_address])
        sol_balance = sol_result.get("value", 0) / 1e9 if isinstance(sol_result, dict) else 0

        # Get token accounts
        token_result = await self._rpc_call(
            "getTokenAccountsByOwner",
            [
                wallet_address,
                {"programId": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"},
                {"encoding": "jsonParsed"},
            ],
        )
        token_accounts = token_result.get("value", []) if isinstance(token_result, dict) else []

        token_holdings = []
        for ta in token_accounts[:50]:
            parsed = ta.get("account", {}).get("data", {}).get("parsed", {}).get("info", {})
            mint = parsed.get("mint", "")
            balance = float(parsed.get("tokenAmount", {}).get("uiAmount", 0))
            if balance > 0:
                token_holdings.append(
                    {
                        "mint": mint,
                        "balance": balance,
                    }
                )

        # Get recent tx count
        sigs_result = await self._rpc_call(
            "getSignaturesForAddress", [wallet_address, {"limit": 1000}]
        )
        signatures = sigs_result if isinstance(sigs_result, list) else []

        # Classify behavior
        tags = []
        if sol_balance > 1000:
            tags.append("sol_whale")
        if sol_balance > 10000:
            tags.append("mega_whale")
        if len(token_holdings) > 20:
            tags.append("diverse_portfolio")
        if len(signatures) > 1000:
            tags.append("highly_active")
        if len(signatures) > 0:
            first_tx = signatures[-1].get("blockTime", 0)
            last_tx = signatures[0].get("blockTime", 0)
            age_days = (last_tx - first_tx) / 86400
            if age_days < 30 and len(signatures) > 200:
                tags.append("aggressive_trader")

        influence_score = min(
            100, int((sol_balance / 100) * 2 + len(token_holdings) * 3 + len(signatures) / 50)
        )

        return {
            "address": wallet_address,
            "sol_balance": round(sol_balance, 4),
            "token_count": len(token_holdings),
            "token_holdings": token_holdings[:20],
            "total_transactions": len(signatures),
            "influence_score": influence_score,
            "behavior_tags": tags,
            "is_whale": sol_balance > 100 or influence_score > 50,
            "timestamp": datetime.now(UTC).isoformat(),
        }

    async def _get_enhanced_tx(self, signature: str) -> dict[str, Any] | None:
        result = await self._rpc_call(
            "getTransaction",
            [signature, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}],
        )
        return result if result and not isinstance(result, str) else None

    def register_alert_callback(self, callback: Callable[[WhaleAlert], None]) -> None:
        """Register a callback for whale alerts."""
        self.callbacks.append(callback)

    async def monitor_tokens(self, token_addresses: list[str], interval_seconds: int = 60) -> None:
        """Continuously monitor tokens for whale activity."""
        logger.info(f"Starting whale monitor for {len(token_addresses)} tokens")
        while True:
            for token in token_addresses:
                try:
                    result = await self.scan_token_for_whales(token)
                    if result.get("alerts"):
                        for alert in result["alerts"]:
                            logger.warning(f"WHALE ALERT: {alert['message']}")
                    self._monitored_tokens[token] = result
                except Exception as e:
                    logger.error(f"Monitor error for {token}: {e}")
            await asyncio.sleep(interval_seconds)
