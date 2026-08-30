"""
Omega Forensic V5 - Cross-Token Affiliation Tracker
====================================================
Tracks wallet connections across multiple token projects.
Proves CRM ↔ SOSANA ↔ PBTC ↔ SHIFT AI connections.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.core.logging import get_logger

# Lazy imports - api_arsenal and wallet_database may not exist
ForensicAPIArsenal: Any = None
APIResponse: Any = None
WalletDatabase: Any = None
get_wallet_database: Any = None

with contextlib.suppress(ImportError):
    from .api_arsenal import ForensicAPIArsenal  # type: ignore[no-redef]

with contextlib.suppress(ImportError):
    from .wallet_database import get_wallet_database  # type: ignore[no-redef]

logging.basicConfig(level=logging.INFO)
logger = get_logger("CrossTokenTracker")

# === PROJECT TOKEN ADDRESSES ===
PROJECT_TOKENS = {
    "CRM": "Eme5T2s2HB7B8W4YgLG1eReQpnadEVUnQBRjaKTdBAGS",
    "SOSANA": "SoSaNaTokenAddressPlaceholder123456789",
    "PBTC": "pBTC Token Address Placeholder",
    "SHIFT_AI": "ShiftAITokenAddressPlaceholder123456",
}


@dataclass
class TokenAffiliation:
    """Represents a wallet's affiliation with a token project."""

    project: str
    token_address: str
    balance: float
    first_acquired: datetime | None = None
    last_activity: datetime | None = None
    acquisition_method: str = "unknown"  # prebond, purchase, airdrop, transfer
    evidence_strength: str = "unverified"  # confirmed, strong, circumstantial


@dataclass
class CrossProjectConnection:
    """Represents a connection between projects via shared wallets."""

    wallet: str
    projects: list[str]
    connection_type: str  # holder, deployer, funder, bridge
    evidence_tier: str
    notes: str = ""


class CrossTokenAffiliationTracker:
    """
    Tracks wallet affiliations across multiple token projects.
    Critical for proving the criminal enterprise spanned multiple tokens.
    """

    def __init__(self) -> None:
        self.wallet_db: Any = get_wallet_database() if get_wallet_database else None
        self.project_tokens = PROJECT_TOKENS
        self.affiliations: dict[str, list[TokenAffiliation]] = {}
        self.connections: list[CrossProjectConnection] = []

    async def track_wallet_affiliations(
        self, wallet: str, arsenal: ForensicAPIArsenal | None = None
    ) -> list[TokenAffiliation]:
        """
        Track all token project affiliations for a wallet.

        Args:
            wallet: Wallet address to track
            arsenal: Optional API arsenal instance

        Returns:
            List of token affiliations
        """
        logger.info(f"🔗 Tracking cross-token affiliations for {wallet}")

        affiliations = []

        if ForensicAPIArsenal is None:
            logger.warning("ForensicAPIArsenal not available - returning empty")
            return affiliations

        async with ForensicAPIArsenal() as api:
            # Get token holdings from Helius
            holdings_result = await api.helius_get_token_accounts(wallet)

            if holdings_result.success and holdings_result.data:
                tokens = holdings_result.data.get("tokens", [])

                for token in tokens:
                    mint = token.get("mint")
                    amount = token.get("amount", 0)
                    decimals = token.get("decimals", 0)
                    balance = amount / (10**decimals) if decimals > 0 else amount

                    # Check if this is one of our target projects
                    for project, token_addr in self.project_tokens.items():
                        if mint == token_addr and balance > 0:
                            affiliation = TokenAffiliation(
                                project=project,
                                token_address=mint,
                                balance=balance,
                                evidence_strength="confirmed",
                            )
                            affiliations.append(affiliation)
                            logger.info(f"  ✓ {project}: {balance:,.2f} tokens")

            # Get transaction history for deeper analysis
            tx_result = await api.helius_get_transactions(wallet, limit=200)

            if tx_result.success and tx_result.data:
                for tx in tx_result.data:
                    timestamp = tx.get("timestamp")
                    tx_time = datetime.fromtimestamp(timestamp) if timestamp else None

                    # Analyze token transfers
                    token_transfers = tx.get("tokenTransfers", [])

                    for transfer in token_transfers:
                        mint = transfer.get("mint")

                        for project, token_addr in self.project_tokens.items():
                            # Skip if already found as holder
                            if any(a.project == project for a in affiliations):
                                continue

                            if mint == token_addr:
                                # This wallet has transacted with the token
                                amount = transfer.get("tokenAmount", 0)

                                affiliation = TokenAffiliation(
                                    project=project,
                                    token_address=mint,
                                    balance=amount,
                                    first_acquired=tx_time,
                                    last_activity=tx_time,
                                    acquisition_method="transaction",
                                    evidence_strength="strong",
                                )
                                affiliations.append(affiliation)
                                logger.info(f"  ✓ {project}: Transaction activity detected")

        # Check database for known affiliations
        db_wallet = self.wallet_db.get_wallet(wallet)
        if db_wallet:
            for project in db_wallet.cross_project_affiliations:
                if not any(a.project == project for a in affiliations):
                    affiliation = TokenAffiliation(
                        project=project,
                        token_address=self.project_tokens.get(project, ""),
                        balance=0,
                        evidence_strength="circumstantial",
                    )
                    affiliations.append(affiliation)

        self.affiliations[wallet] = affiliations
        return affiliations

    async def find_cross_project_wallets(
        self, projects: list[str], min_projects: int = 2
    ) -> list[CrossProjectConnection]:
        """
        Find wallets affiliated with multiple projects.

        Args:
            projects: List of projects to check
            min_projects: Minimum number of projects for a wallet to qualify

        Returns:
            List of cross-project connections
        """
        logger.info(f"🔍 Finding wallets connected to {min_projects}+ projects")

        # Get all wallets from database that have cross-project affiliations
        all_wallets = list(self.wallet_db.wallets.values())

        cross_project_wallets = []

        for wallet in all_wallets:
            wallet_projects = set(wallet.cross_project_affiliations)

            # Check if wallet is in multiple target projects
            matching_projects = wallet_projects.intersection(set(projects))

            if len(matching_projects) >= min_projects:
                connection = CrossProjectConnection(
                    wallet=wallet.address,
                    projects=list(matching_projects),
                    connection_type=self._classify_connection(wallet),
                    evidence_tier=wallet.evidence_tier.value,
                    notes=wallet.notes,
                )
                cross_project_wallets.append(connection)

        self.connections = cross_project_wallets

        logger.info(f"  ✓ Found {len(cross_project_wallets)} cross-project wallets")
        return cross_project_wallets

    def _classify_connection(self, wallet) -> str:
        """Classify the type of cross-project connection."""
        category = wallet.category.value

        if "deployer" in category or "contract" in category:
            return "deployer"
        elif "funder" in category or "treasury" in category:
            return "funder"
        elif "bridge" in category:
            return "bridge"
        elif "commander" in category or "botnet" in category:
            return "operator"
        else:
            return "holder"

    async def map_project_network(self, center_project: str = "CRM", depth: int = 2) -> dict[str, Any]:
        """
        Map the entire network of projects connected to a center project.

        Args:
            center_project: The central project to map from
            depth: How many connection layers to traverse

        Returns:
            Network graph data
        """
        logger.info(f"🕸️ Mapping {center_project} network (depth={depth})")

        # Get all wallets affiliated with center project
        center_wallets = self.wallet_db.get_cross_project_wallets(center_project)

        # Build network
        network: dict[str, Any] = {
            "center": center_project,
            "nodes": [],
            "edges": [],
            "projects": {center_project},
            "wallet_count": len(center_wallets),
        }

        for wallet in center_wallets:
            # Add wallet node
            network["nodes"].append(
                {
                    "id": wallet.address,
                    "type": "wallet",
                    "category": wallet.category.value,
                    "tier": wallet.evidence_tier.value,
                }
            )

            # Add project connections
            for project in wallet.cross_project_affiliations:
                network["projects"].add(project)
                network["edges"].append({"source": wallet.address, "target": project, "type": "affiliation"})

        network["projects"] = list(network["projects"])

        logger.info(f"  ✓ Network mapped: {len(network['nodes'])} wallets, {len(network['projects'])} projects")

        return network

    def generate_evidence_report(self) -> dict[str, Any]:
        """Generate evidence report of cross-project connections."""
        report: dict[str, Any] = {
            "generated_at": datetime.now().isoformat(),
            "summary": {
                "total_wallets_analyzed": len(self.affiliations),
                "cross_project_wallets": len(self.connections),
                "projects_involved": list({proj for conn in self.connections for proj in conn.projects}),
            },
            "critical_connections": [],
            "evidence_by_tier": {
                "tier_1_direct": [],
                "tier_2_strong": [],
                "tier_3_circumstantial": [],
            },
        }

        for conn in self.connections:
            connection_data = {
                "wallet": conn.wallet,
                "projects": conn.projects,
                "type": conn.connection_type,
                "evidence_tier": conn.evidence_tier,
                "notes": conn.notes,
            }

            report["critical_connections"].append(connection_data)

            # Sort by tier
            if "tier_1" in conn.evidence_tier:
                report["evidence_by_tier"]["tier_1_direct"].append(connection_data)
            elif "tier_2" in conn.evidence_tier:
                report["evidence_by_tier"]["tier_2_strong"].append(connection_data)
            else:
                report["evidence_by_tier"]["tier_3_circumstantial"].append(connection_data)

        return report

    def get_smoking_guns(self) -> list[CrossProjectConnection]:
        """Get the strongest cross-project connections (smoking guns)."""
        return [
            conn
            for conn in self.connections
            if conn.evidence_tier == "tier_1_direct" or "SMOKING_GUN" in conn.notes.upper()
        ]


# === SYNC WRAPPERS ===
def track_wallet(wallet: str) -> list[TokenAffiliation]:
    """Synchronous wrapper for tracking wallet affiliations."""
    tracker = CrossTokenAffiliationTracker()
    return asyncio.run(tracker.track_wallet_affiliations(wallet))


def find_cross_project_wallets(projects: list[str], min_projects: int = 2) -> list[CrossProjectConnection]:
    """Synchronous wrapper for finding cross-project wallets."""
    tracker = CrossTokenAffiliationTracker()
    return asyncio.run(tracker.find_cross_project_wallets(projects, min_projects))
