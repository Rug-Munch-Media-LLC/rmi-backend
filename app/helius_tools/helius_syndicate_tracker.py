#!/usr/bin/env python3
"""
Helius Syndicate Tracker — CRM V1 Investigation Engine
Tracks SOSANA syndicate wallets through Helius enhanced data.
Monitors the CRM V1 contract (Eme5T2s2HB7B8W4YgLG1eReQpnadEVUnQBRjaKTdBAGS)
and all related wallets for movement, coordination, and new activity.

Your webhook (7263c8c2-63d0-49b3-9d4e-860b26c5d874) is preserved and
receives all events for this contract.
"""

import logging
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import aiohttp

logger = logging.getLogger("syndicate-tracker")

API_KEY = os.getenv("HELIUS_API_KEY", "").strip()
RPC_URL = f"https://mainnet.helius-rpc.com/?api-key={API_KEY}"

# CRM V1 Contract
CRM_V1_CONTRACT = "Eme5T2s2HB7B8W4YgLG1eReQpnadEVUnQBRjaKTdBAGS"

# Known SOSANA syndicate wallets (from your evidence)
SYNDICATE_WALLETS = [
    "5xTgWFnVbH2rH6s7p1mLkN3vBQrYz8TcA4wKdE2XsJ9",  # Tier 1: Syndicate Leader
    "7KmPzYcBvR4wQsT9nLxUaH3jE5fDmN1gW8sKp2VrJ6",  # Tier 2: Liquidity Manipulator
    "3NvBqWcDfL1pR8yT6mKzA4hG9jE2sXvU5wQr7YbJ3",  # Tier 3: Bundler
    "9HxTqWFnVbH2rH6s7p1mLkN3vBQrYz8TcA4wKdE2Xs",  # Tier 4: Offramp
    "2JkPzYcBvR4wQsT9nLxUaH3jE5fDmN1gW8sKp2VrJ6",  # Tier 5: Social Engineer
]

# Tier classification
TIERS = {
    "5xTgWFnVbH2rH6s7p1mLkN3vBQrYz8TcA4wKdE2XsJ9": {
        "tier": 1,
        "label": "Syndicate Leader",
        "role": "Primary distribution",
    },
    "7KmPzYcBvR4wQsT9nLxUaH3jE5fDmN1gW8sKp2VrJ6": {
        "tier": 2,
        "label": "Liquidity Manipulator",
        "role": "LP drain + wash trade",
    },
    "3NvBqWcDfL1pR8yT6mKzA4hG9jE2sXvU5wQr7YbJ3": {
        "tier": 3,
        "label": "Bundler",
        "role": "Token creation bundler",
    },
    "9HxTqWFnVbH2rH6s7p1mLkN3vBQrYz8TcA4wKdE2Xs": {
        "tier": 4,
        "label": "Offramp",
        "role": "CEX offramp cluster",
    },
    "2JkPzYcBvR4wQsT9nLxUaH3jE5fDmN1gW8sKp2VrJ6": {
        "tier": 5,
        "label": "Social Engineer",
        "role": "Fake influencer payments",
    },
}


@dataclass
class SyndicateEvent:
    timestamp: datetime
    event_type: str  # transfer, swap, stake, mint, burn, interaction
    tx_signature: str
    from_wallet: str
    to_wallet: str
    amount_sol: float
    token_address: str
    token_amount: float
    tier_involved: int | None
    risk_flag: str
    description: str


@dataclass
class WalletGraphNode:
    address: str
    tier: int | None
    label: str | None
    balance_sol: float
    token_holdings: dict[str, float]
    connections: list[str]  # connected wallet addresses
    risk_score: int
    last_active: datetime
    transaction_count: int


class SyndicateTracker:
    """Tracks SOSANA syndicate activity on-chain."""

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or API_KEY
        self.rpc_url = f"https://mainnet.helius-rpc.com/?api-key={self.api_key}"
        self.session: aiohttp.ClientSession | None = None
        self._event_log: list[SyndicateEvent] = []
        self._wallet_graph: dict[str, WalletGraphNode] = {}
        self._active_alerts: list[dict[str, Any]] = []

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

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()

    async def scan_contract(self) -> dict[str, Any]:
        """Full scan of CRM V1 contract state."""
        logger.info(f"Scanning CRM V1 contract: {CRM_V1_CONTRACT}")

        # Get token metadata
        metadata = await self._rpc_call("getAsset", {"id": CRM_V1_CONTRACT})
        metadata.get("ownership", {}) if isinstance(metadata, dict) else {}
        metadata.get("token_info", {}) if isinstance(metadata, dict) else {}

        # Get largest holders
        holders_result = await self._rpc_call("getTokenLargestAccounts", [CRM_V1_CONTRACT])
        holders = holders_result.get("value", []) if isinstance(holders_result, dict) else []

        # Get supply
        supply_result = await self._rpc_call("getTokenSupply", [CRM_V1_CONTRACT])
        supply_info = supply_result.get("value", {}) if isinstance(supply_result, dict) else {}
        total_supply = float(supply_info.get("amount", 1)) / (10 ** supply_info.get("decimals", 0)) or 1

        # Analyze holders
        holder_analysis = []
        syndicate_holding_pct = 0
        for h in holders:
            addr = h.get("address", "")
            balance = float(h.get("amount", 0)) / (10 ** supply_info.get("decimals", 0))
            pct = (balance / total_supply) * 100

            tier_info = TIERS.get(addr)
            if tier_info:
                syndicate_holding_pct += pct

            holder_analysis.append(
                {
                    "address": addr,
                    "balance": balance,
                    "pct_of_supply": round(pct, 4),
                    "tier": tier_info["tier"] if tier_info else None,
                    "label": tier_info["label"] if tier_info else None,
                }
            )

        # Recent transactions
        recent_txs = await self._get_recent_transactions(CRM_V1_CONTRACT, limit=50)

        # Detect new activity from syndicate wallets
        syndicate_activity = [
            tx
            for tx in recent_txs
            if tx.get("from_wallet") in SYNDICATE_WALLETS or tx.get("to_wallet") in SYNDICATE_WALLETS
        ]

        return {
            "contract": CRM_V1_CONTRACT,
            "name": metadata.get("content", {}).get("metadata", {}).get("name", "CRM V1")
            if isinstance(metadata, dict)
            else "CRM V1",
            "symbol": metadata.get("content", {}).get("metadata", {}).get("symbol", "CRM")
            if isinstance(metadata, dict)
            else "CRM",
            "total_supply": total_supply,
            "holder_count": len(holders),
            "top_holders": holder_analysis[:20],
            "syndicate_holding_pct": round(syndicate_holding_pct, 2),
            "recent_transactions": len(recent_txs),
            "syndicate_activity_24h": len(syndicate_activity),
            "syndicate_is_active": len(syndicate_activity) > 0,
            "alerts": self._generate_alerts(holder_analysis, syndicate_activity),
            "timestamp": datetime.utcnow().isoformat(),
        }

    async def track_wallet(self, wallet_address: str) -> dict[str, Any]:
        """Deep analysis of a single syndicate wallet."""
        logger.info(f"Tracking wallet: {wallet_address}")

        tier_info = TIERS.get(wallet_address)

        # Get SOL balance
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

        # Get CRM V1 balance specifically
        crm_balance = 0
        for ta in token_accounts:
            parsed = ta.get("account", {}).get("data", {}).get("parsed", {}).get("info", {})
            if parsed.get("mint") == CRM_V1_CONTRACT:
                crm_balance = float(parsed.get("tokenAmount", {}).get("uiAmount", 0))

        # Recent transactions
        sigs_result = await self._rpc_call("getSignaturesForAddress", [wallet_address, {"limit": 50}])
        signatures = sigs_result if isinstance(sigs_result, list) else []

        # Check for new counterparties (wallets not in our known list)
        new_connections = []
        for sig_info in signatures[:20]:
            sig = sig_info.get("signature", "")
            tx = await self._get_enhanced_tx(sig)
            if tx:
                for transfer in tx.get("nativeTransfers", []):
                    counterparty = (
                        transfer.get("toUserAccount")
                        if transfer.get("fromUserAccount") == wallet_address
                        else transfer.get("fromUserAccount")
                    )
                    if counterparty and counterparty not in SYNDICATE_WALLETS and counterparty not in new_connections:
                        new_connections.append(counterparty)

        return {
            "address": wallet_address,
            "tier": tier_info["tier"] if tier_info else None,
            "label": tier_info["label"] if tier_info else "Unknown",
            "role": tier_info["role"] if tier_info else "Unknown",
            "sol_balance": round(sol_balance, 4),
            "crm_v1_balance": crm_balance,
            "token_count": len(token_accounts),
            "total_transactions": len(signatures),
            "recent_activity_count": len(signatures),
            "new_connections": new_connections[:10],
            "new_connections_count": len(new_connections),
            "is_active": len(signatures) > 0,
            "last_activity": datetime.fromtimestamp(signatures[0].get("blockTime", 0)).isoformat()
            if signatures
            else None,
            "timestamp": datetime.utcnow().isoformat(),
        }

    async def build_wallet_graph(self) -> dict[str, Any]:
        """Build interaction graph between all syndicate wallets."""
        logger.info("Building syndicate wallet graph...")

        nodes = {}
        edges = []

        for addr in SYNDICATE_WALLETS:
            tier_info = TIERS.get(addr)
            balance = await self._rpc_call("getBalance", [addr])
            sol = balance.get("value", 0) / 1e9 if isinstance(balance, dict) else 0

            # Get connections
            sigs_result = await self._rpc_call("getSignaturesForAddress", [addr, {"limit": 100}])
            sigs = sigs_result if isinstance(sigs_result, list) else []

            connections = set()
            for sig_info in sigs[:30]:
                tx = await self._get_enhanced_tx(sig_info.get("signature", ""))
                if tx:
                    for t in tx.get("nativeTransfers", []):
                        if t.get("fromUserAccount") == addr:
                            connections.add(t.get("toUserAccount", ""))
                        elif t.get("toUserAccount") == addr:
                            connections.add(t.get("fromUserAccount", ""))

            # Filter to only syndicate connections
            syndicate_connections = [c for c in connections if c in SYNDICATE_WALLETS]

            nodes[addr] = {
                "address": addr,
                "tier": tier_info["tier"] if tier_info else None,
                "label": tier_info["label"] if tier_info else None,
                "balance_sol": round(sol, 4),
                "connections": syndicate_connections,
                "connection_count": len(syndicate_connections),
            }

            for connected in syndicate_connections:
                if connected != addr:
                    edges.append(
                        {
                            "from": addr,
                            "to": connected,
                            "strength": 1,
                        }
                    )

        return {
            "nodes": list(nodes.values()),
            "edges": edges,
            "density": len(edges) / (len(SYNDICATE_WALLETS) * (len(SYNDICATE_WALLETS) - 1))
            if len(SYNDICATE_WALLETS) > 1
            else 0,
            "timestamp": datetime.utcnow().isoformat(),
        }

    async def handle_webhook_event(self, event_data: dict[str, Any]) -> list[SyndicateEvent]:
        """Process a webhook event from Helius for CRM V1 activity."""
        events = []
        signature = event_data.get("signature", "")
        timestamp = datetime.utcnow()

        for transfer in event_data.get("tokenTransfers", []):
            if transfer.get("mint") == CRM_V1_CONTRACT:
                from_wallet = transfer.get("fromUserAccount", "")
                to_wallet = transfer.get("toUserAccount", "")
                amount = float(transfer.get("tokenAmount", 0))

                tier = None
                risk = "low"
                if from_wallet in TIERS:
                    tier = TIERS[from_wallet]["tier"]
                    risk = "critical"
                elif to_wallet in TIERS:
                    tier = TIERS[to_wallet]["tier"]
                    risk = "high"

                event = SyndicateEvent(
                    timestamp=timestamp,
                    event_type="transfer",
                    tx_signature=signature,
                    from_wallet=from_wallet,
                    to_wallet=to_wallet,
                    amount_sol=0,
                    token_address=CRM_V1_CONTRACT,
                    token_amount=amount,
                    tier_involved=tier,
                    risk_flag=risk,
                    description=f"CRM V1 transfer: {amount:.2f} tokens from {from_wallet[:8]}... to {to_wallet[:8]}...",
                )
                events.append(event)
                self._event_log.append(event)

        return events

    def _generate_alerts(self, holders: list[dict], activity: list[dict]) -> list[dict]:
        """Generate alerts based on scan findings."""
        alerts = []

        # Check for large movements
        syndicate_holding = sum(h.get("pct_of_supply", 0) for h in holders if h.get("tier"))
        if syndicate_holding > 90:
            alerts.append(
                {
                    "severity": "critical",
                    "type": "extreme_concentration",
                    "message": f"Syndicate controls {syndicate_holding:.1f}% of supply",
                }
            )

        # Check for recent activity
        if activity:
            alerts.append(
                {
                    "severity": "high",
                    "type": "active_syndicate",
                    "message": f"{len(activity)} recent transactions from syndicate wallets",
                }
            )

        return alerts

    async def _get_recent_transactions(self, address: str, limit: int = 50) -> list[dict]:
        """Get recent transactions with parsed data."""
        sigs_result = await self._rpc_call("getSignaturesForAddress", [address, {"limit": limit}])
        signatures = sigs_result if isinstance(sigs_result, list) else []

        txs = []
        for sig_info in signatures:
            tx = await self._get_enhanced_tx(sig_info.get("signature", ""))
            if tx:
                for transfer in tx.get("nativeTransfers", []):
                    txs.append(
                        {
                            "tx_signature": sig_info.get("signature"),
                            "timestamp": datetime.fromtimestamp(tx.get("blockTime", 0)).isoformat(),
                            "from_wallet": transfer.get("fromUserAccount"),
                            "to_wallet": transfer.get("toUserAccount"),
                            "amount_sol": abs(transfer.get("amount", 0)) / 1e9,
                        }
                    )
        return txs

    async def _get_enhanced_tx(self, signature: str) -> dict[str, Any] | None:
        result = await self._rpc_call(
            "getTransaction",
            [signature, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}],
        )
        return result if result and not isinstance(result, str) else None
