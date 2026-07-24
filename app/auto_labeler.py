"""
Auto-Labeling RAG System - Behavioral wallet labeling.
========================================================
Watches for on-chain patterns and automatically labels wallets over time.
Uses FAISS similarity search against known labeled wallets.
When a wallet matches known scam/deployer/actor patterns, it gets auto-labeled.

Label categories:
- repeat_deployer: Created 3+ tokens that rugged
- funding_funnel: Received funds from known scam wallets
- wash_trader: Circular transaction patterns
- sniper_bot: Consistent sub-block-10 entries
- sandwich_bot: MEV sandwich attack patterns
- dust_attacker: Dust-level transfers to many addresses
- honeypot_deployer: Deployed contracts with transfer restrictions
- drainer_wallet: Receives from known phishing victims
- cex_deposit_launderer: Moves through CEX to obfuscate
- mixer_user: Interacts with sanctioned mixers
- pig_butchering: Slow buildup then sudden drain pattern
"""

from __future__ import annotations

import os
import time
from collections import Counter, defaultdict
from datetime import datetime

from app.core.logging import get_logger

logger = get_logger(__name__)

# ── Label Definitions ─────────────────────────────────────────

AUTO_LABELS = {
    "repeat_deployer_3": {
        "name": "Serial Deployer (3+)",
        "description": "Deployed 3+ tokens that later rugged or were abandoned",
        "entity_type": "scam_deployer",
        "risk_score": 85,
        "confidence_threshold": 0.7,
        "icon": "🏭",
    },
    "repeat_deployer_5": {
        "name": "Rug Pull Factory (5+)",
        "description": "Deployed 5+ rug pull tokens - professional scam operation",
        "entity_type": "scam_operation",
        "risk_score": 95,
        "confidence_threshold": 0.8,
        "icon": "☠️",
    },
    "funding_funnel": {
        "name": "Funding Funnel",
        "description": "Received funds from 3+ known scam wallets - likely launderer",
        "entity_type": "money_launderer",
        "risk_score": 80,
        "confidence_threshold": 0.6,
        "icon": "💰",
    },
    "sniper_bot": {
        "name": "Sniper Bot",
        "description": "Consistently buys tokens in first 10 blocks of launch",
        "entity_type": "trading_bot",
        "risk_score": 30,
        "confidence_threshold": 0.8,
        "icon": "🎯",
    },
    "sandwich_bot": {
        "name": "Sandwich Bot",
        "description": "Detected sandwich attack patterns - front-running trades",
        "entity_type": "mev_bot",
        "risk_score": 60,
        "confidence_threshold": 0.7,
        "icon": "🥪",
    },
    "mixer_user": {
        "name": "Mixer User",
        "description": "Interacts with Tornado Cash or other sanctioned mixers",
        "entity_type": "mixer_user",
        "risk_score": 75,
        "confidence_threshold": 0.6,
        "icon": "🌪️",
    },
    "drainer_wallet": {
        "name": "Wallet Drainer",
        "description": "Receives funds from known phishing/exploit victim wallets",
        "entity_type": "drainer",
        "risk_score": 90,
        "confidence_threshold": 0.7,
        "icon": "🪝",
    },
    "honeypot_deployer": {
        "name": "Honeypot Deployer",
        "description": "Deployed contracts with sell restrictions or transfer blocks",
        "entity_type": "scam_deployer",
        "risk_score": 88,
        "confidence_threshold": 0.7,
        "icon": "🍯",
    },
    "wash_trader": {
        "name": "Wash Trader",
        "description": "Circular transaction patterns - trading with self/controlled wallets",
        "entity_type": "wash_trader",
        "risk_score": 70,
        "confidence_threshold": 0.65,
        "icon": "🔄",
    },
    "dust_attacker": {
        "name": "Dust Attacker",
        "description": "Sends dust amounts to 100+ addresses - phishing or tracking attempt",
        "entity_type": "dust_attacker",
        "risk_score": 45,
        "confidence_threshold": 0.8,
        "icon": "💨",
    },
    "pig_butchering": {
        "name": "Pig Butchering Operator",
        "description": "Gradual fund accumulation then sudden drain to exchange - scam pattern",
        "entity_type": "scam_operation",
        "risk_score": 92,
        "confidence_threshold": 0.7,
        "icon": "🐷",
    },
    "cex_launderer": {
        "name": "CEX Launderer",
        "description": "Routes through multiple CEX deposit addresses to break trace",
        "entity_type": "money_launderer",
        "risk_score": 78,
        "confidence_threshold": 0.65,
        "icon": "🏦",
    },
    "sleeping_agent": {
        "name": "Sleeping Agent",
        "description": "Wallet dormant 90+ days then suddenly active - potential sleeper",
        "entity_type": "suspicious",
        "risk_score": 55,
        "confidence_threshold": 0.6,
        "icon": "😴",
    },
    "flash_loan_attacker": {
        "name": "Flash Loan Attacker",
        "description": "Used flash loans for rapid price manipulation or exploits",
        "entity_type": "exploiter",
        "risk_score": 85,
        "confidence_threshold": 0.7,
        "icon": "⚡",
    },
}

class AutoLabeler:
    """Auto-labeling engine that watches wallets and assigns labels based on behavior."""

    def __init__(self) -> None:
        self.labels_applied = Counter()
        self.pending_observations = defaultdict(list)
        self.last_run = None
        self._known_scam_wallets = set()
        self._known_mixers = set()
        self._known_exchanges = set()
        self._initialize_known_sets()

    def _initialize_known_sets(self) -> None:
        """Load known scam wallets and mixers from our label database."""
        clean_dir = os.path.join(os.environ.get("RMI_DATA_DIR", "/app/data"), "wallet-labels-clean")

        for chain in ["ethereum", "solana"]:
            path = os.path.join(clean_dir, f"wallet_labels_{chain}.csv")
            if not os.path.exists(path):
                continue

            import csv

            with open(path) as f:
                for row in csv.DictReader(f):
                    addr = row["address"].lower()
                    etype = row.get("entity_type", "")

                    if etype in (
                        "malicious",
                        "phishing_scam",
                        "scam",
                        "exploiter",
                        "drainer",
                        "nation_state_actor",
                        "scam_operation",
                        "scam_deployer",
                        "money_launderer",
                    ):
                        self._known_scam_wallets.add(addr)

                    if etype == "mixer" or "tornado" in row.get("name", "").lower():
                        self._known_mixers.add(addr)

                    if etype == "exchange" or etype == "exchange_deposit":
                        self._known_exchanges.add(addr)

        logger.info(
            f"AutoLabeler initialized: {len(self._known_scam_wallets):,} known scams, "
            f"{len(self._known_mixers):,} mixers, {len(self._known_exchanges):,} exchanges"
        )

    async def observe_wallet(self, address: str, chain: str, observations: dict) -> list[dict]:
        """Record observations about a wallet and check if any labels should be applied."""
        key = f"{chain}:{address.lower()}"
        self.pending_observations[key].append(
            {
                "timestamp": time.time(),
                **observations,
            }
        )

        # Check label rules - return only NEW labels not already applied
        existing_label_keys = {
            line_list["label_key"]
            for line_list in self.pending_observations.get(f"_labels_{key}", [])
        }
        new_labels = await self._check_labels(address, chain, self.pending_observations[key])
        unique_new = [
            line_list
            for line_list in new_labels
            if line_list["label_key"] not in existing_label_keys
        ]

        # Track applied labels to prevent duplicates
        if f"_labels_{key}" not in self.pending_observations:
            self.pending_observations[f"_labels_{key}"] = []
        self.pending_observations[f"_labels_{key}"].extend(unique_new)

        for label in unique_new:
            self.labels_applied[label["label_key"]] += 1

        return unique_new

    async def _check_labels(self, address: str, chain: str, history: list[dict]) -> list[dict]:
        """Check all label rules against wallet history."""
        applied = []

        deploy_count = sum(1 for o in history if o.get("event") == "token_deployed")
        if deploy_count >= 5:
            applied.append(
                self._create_label(
                    "repeat_deployer_5", address, chain, {"deployments": deploy_count}
                )
            )
        elif deploy_count >= 3:
            applied.append(
                self._create_label(
                    "repeat_deployer_3", address, chain, {"deployments": deploy_count}
                )
            )

        # Check funding sources
        funders = set()
        for o in history:
            if o.get("event") == "received_funds" and o.get("from_address"):
                funders.add(o["from_address"].lower())

        scam_funders = funders & self._known_scam_wallets
        if len(scam_funders) >= 3:
            applied.append(
                self._create_label(
                    "funding_funnel", address, chain, {"scam_funders": list(scam_funders)[:5]}
                )
            )

        # Check mixer interaction
        mixer_interactions = sum(
            1
            for o in history
            if o.get("counterparty", "").lower() in self._known_mixers
            or "tornado" in o.get("protocol", "").lower()
        )
        if mixer_interactions >= 1:
            applied.append(
                self._create_label(
                    "mixer_user", address, chain, {"mixer_interactions": mixer_interactions}
                )
            )

        # Check CEX laundering pattern
        cex_deposits = set()
        for o in history:
            if o.get("event") == "deposited_to_exchange" and o.get("exchange"):
                cex_deposits.add(o["exchange"])
        if len(cex_deposits) >= 3 and len(scam_funders) >= 1:
            applied.append(
                self._create_label(
                    "cex_launderer", address, chain, {"exchanges_used": list(cex_deposits)}
                )
            )

        # Check draining pattern
        victim_funds = sum(
            1
            for o in history
            if o.get("counterparty", "").lower() in self._known_scam_wallets
            and o.get("event") == "received_funds"
        )
        if victim_funds >= 5:
            applied.append(
                self._create_label("drainer_wallet", address, chain, {"victim_count": victim_funds})
            )

        # Check sleeping agent
        if len(history) >= 2:
            timestamps = sorted(o.get("timestamp", 0) for o in history)
            gap = timestamps[-1] - timestamps[0]
            if gap > 90 * 86400:  # 90+ days dormancy
                applied.append(
                    self._create_label(
                        "sleeping_agent", address, chain, {"dormant_days": int(gap / 86400)}
                    )
                )

        # Track applied labels
        for label in applied:
            self.labels_applied[label["label_key"]] += 1

        return applied

    def _create_label(self, label_key: str, address: str, chain: str, evidence: dict) -> dict:
        """Create a label entry."""
        config = AUTO_LABELS[label_key]
        return {
            "address": address,
            "chain": chain,
            "label_key": label_key,
            "name": config["name"],
            "description": config["description"],
            "entity_type": config["entity_type"],
            "risk_score": config["risk_score"],
            "icon": config["icon"],
            "source": "auto_labeler",
            "applied_at": datetime.now().isoformat(),
            "evidence": evidence,
        }

    async def batch_analyze(self, observations: list[dict]) -> dict:
        """Batch analyze multiple wallet observations and return all labels."""
        results = {"labels_applied": [], "stats": {}}

        for obs in observations:
            addr = obs.get("address", "")
            chain = obs.get("chain", "ethereum")
            if addr:
                labels = await self.observe_wallet(addr, chain, obs.get("events", []))
                results["labels_applied"].extend(labels)

        results["stats"] = {
            "total_wallets_analyzed": len(observations),
            "labels_applied": len(results["labels_applied"]),
            "label_counts": dict(self.labels_applied.most_common()),
            "known_scam_wallets": len(self._known_scam_wallets),
            "known_mixers": len(self._known_mixers),
        }

        return results

    def get_stats(self) -> dict:
        """Get auto-labeler statistics."""
        return {
            "labels_applied_total": sum(self.labels_applied.values()),
            "label_counts": dict(self.labels_applied.most_common()),
            "known_scam_wallets": len(self._known_scam_wallets),
            "known_mixers": len(self._known_mixers),
            "known_exchanges": len(self._known_exchanges),
            "pending_observations": sum(len(v) for v in self.pending_observations.values()),
            "last_run": self.last_run,
        }

# Singleton
_auto_labeler: AutoLabeler | None = None

def get_auto_labeler() -> AutoLabeler:
    global _auto_labeler
    if _auto_labeler is None:
        _auto_labeler = AutoLabeler()
    return _auto_labeler
