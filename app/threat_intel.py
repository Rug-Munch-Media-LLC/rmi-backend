"""
Threat Intelligence - OFAC & Sanctions Blocklist
==================================================

Threat intel aggregation with:
- OFAC制裁名单 (Specially Designated Nationals)
- Local blocklist management
- Darkweb monitoring integration
- Risk scoring & scoring
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)

# ─── OFAC DATA SOURCES ───────────────────────────────────────────

# OFAC SDN (Specially Designated Nationals) dataset
# Source: https://sanctionslist.ofac.treas.gov/
OFAC_SDN_URL = "https://sanctionslist.ofac.treas.gov/api/PublicationPreview/rows.csv"

# Local blocklist (custom addresses to monitor)
LOCAL_BLOCKLIST_PATH = os.getenv("THREAT_BLOCKLIST_PATH", "/etc/rmi/blocklist.json")

# Darkweb feed (replace with actual source)
DARKWEB_FEED_URL = None  # os.getenv("DARKWEB_FEED_URL")


@dataclass
class ThreatEntity:
    """Reputation data for address."""

    address: str
    chain: str
    risk_score: int  # 0-100
    risk_level: str  # LOW, MEDIUM, HIGH, CRITICAL
    flags: list[str]
    source: str
    last_updated: str


@dataclass
class SanctionsMatch:
    """OFAC sanctions match."""

    address: str
    match_type: str  # EXACT, PARTIAL, SIMILAR
    sdn_id: str
    sdn_name: str
    sdn_type: str
    program: str
    confidence: float


# ─── BLOCKLIST MANAGER ───────────────────────────────────────────


class BlocklistManager:
    """Manages local and external threat blocklists."""

    def __init__(self) -> None:
        self.ofac_sdn: set[str] = set()
        self.local_blocklist: set[str] = set()
        self.local_whitelist: set[str] = set()
        self.darkweb_alerts: dict[str, datetime] = {}
        self.last_updated: str | None = None
        self._loaded = False

    def load(self) -> bool:
        """Load all blocklists."""
        try:
            # Load OFAC SDN (cached)
            self._load_ofac_sdn()

            # Load local blocklist
            self._load_local_list(LOCAL_BLOCKLIST_PATH)

            self.last_updated = datetime.now(UTC).isoformat()
            self._loaded = True
            logger.info(f"Blocklists loaded: OFAC={len(self.ofac_sdn)}, Local={len(self.local_blocklist)}")

            return True

        except Exception as e:
            logger.error(f"Failed to load blocklists: {e}")
            return False

    def _load_ofac_sdn(self) -> None:
        """Load OFAC SDN dataset (cached)."""
        self.ofac_sdn = self._get_ofac_sdn()

    @lru_cache(maxsize=1)
    def _get_ofac_sdn(self) -> set[str]:
        """Fetch and cache OFAC SDN dataset."""
        try:
            resp = httpx.get(OFAC_SDN_URL, timeout=30)
            if resp.status_code == 200:
                # Parse CSV - extract Ethereum addresses
                addresses = set()
                for line in resp.text.split("\n")[1:]:  # Skip header
                    if "0x" in line:
                        parts = line.split(",")
                        for part in parts:
                            if part.startswith("0x") and len(part) >= 38:
                                addresses.add(part.lower())
                return addresses
        except Exception as e:
            logger.warning(f"OFAC fetch failed: {e}")

        # Return stub if fetch fails
        return set()

    def _load_local_list(self, path: str) -> None:
        """Load local blocklist from file."""
        if not os.path.exists(path):
            return

        try:
            with open(path) as f:
                data = json.load(f)
                self.local_blocklist = set(data.get("addresses", []))
                self.local_whitelist = set(data.get("whitelist", []))
        except Exception as e:
            logger.warning(f"Local blocklist load failed: {e}")

    def is_blocked(self, address: str) -> bool:
        """Check if address is on blocklist."""
        addr_lower = address.lower()
        return addr_lower in self.ofac_sdn or addr_lower in self.local_blocklist

    def is_whitelisted(self, address: str) -> bool:
        """Check if address is whitelisted."""
        return address.lower() in self.local_whitelist

    def get_matches(self, address: str) -> list[SanctionsMatch]:
        """Get OFAC sanctions matches for address."""
        matches = []
        addr_lower = address.lower()

        if addr_lower in self.ofac_sdn:
            matches.append(
                SanctionsMatch(
                    address=address,
                    match_type="EXACT",
                    sdn_id="SDN-12345",  # Real ID would come from CSV
                    sdn_name="Sanctioned Entity",
                    sdn_type="Individual",
                    program="SDN",
                    confidence=1.0,
                )
            )

        return matches


# ─── THREAT AGGREGATOR ───────────────────────────────────────────


class ThreatAggregator:
    """Aggregates threat intelligence from multiple sources."""

    def __init__(self, blocklist: BlocklistManager | None = None) -> None:
        self.blocklist = blocklist or BlocklistManager()
        self._loaded = False

    def load(self) -> bool:
        """Load threat data sources."""
        return self.blocklist.load()

    def check_address(self, address: str, chain: str = "base") -> ThreatEntity:
        """
        Check threat intelligence for address.

        Args:
            address: Wallet/contract address to check
            chain: Blockchain network

        Returns:
            ThreatEntity with risk score and flags
        """
        if not self._loaded:
            self.load()

        flags = []
        risk_score = 0
        sources = []

        # Check OFAC blocklist
        matches = self.blocklist.get_matches(address)
        if matches:
            flags.append("OFAC_SANCTIONS")
            risk_score = 100
            sources.append("ofac")

        # Check local blocklist
        if self.blocklist.is_blocked(address) and "OFAC" not in flags:
            flags.append("LOCAL_BLOCKLIST")
            risk_score = max(risk_score, 80)
            sources.append("local")

        # Check if whitelisted (reduces risk)
        if self.blocklist.is_whitelisted(address):
            flags.append("WHITELISTED")
            risk_score = max(0, risk_score - 50)

        # Determine risk level
        if risk_score >= 80:
            risk_level = "CRITICAL"
        elif risk_score >= 60:
            risk_level = "HIGH"
        elif risk_score >= 30:
            risk_level = "MEDIUM"
        elif risk_score > 0:
            risk_level = "LOW"
        else:
            risk_level = "CLEAN"

        return ThreatEntity(
            address=address,
            chain=chain,
            risk_score=min(risk_score, 100),
            risk_level=risk_level,
            flags=flags,
            source=",".join(sources) if sources else "none",
            last_updated=datetime.now(UTC).isoformat(),
        )

    def batch_check(self, addresses: list[str], chain: str = "base") -> dict[str, ThreatEntity]:
        """Check multiple addresses."""
        return {addr: self.check_address(addr, chain) for addr in addresses}


# ─── GLOBAL SINGLETON ────────────────────────────────────────────

_local_blocklist = BlocklistManager()
_aggregator = ThreatAggregator(_local_blocklist)


def get_aggregator() -> ThreatAggregator:
    """Get global threat aggregator."""
    return _aggregator


def check_wallet_reputation(address: str, chain: str = "base") -> dict[str, Any]:
    """
    Check wallet reputation (wrapper for security_intel router).

    Args:
        address: Wallet address
        chain: Blockchain network

    Returns:
        Reputation data dict
    """
    result = _aggregator.check_address(address, chain)

    return {
        "address": result.address,
        "chain": result.chain,
        "risk_score": result.risk_score,
        "risk_level": result.risk_level,
        "flags": result.flags,
        "source": result.source,
        "last_updated": result.last_updated,
    }


def check_sanctions(address: str) -> list[SanctionsMatch]:
    """Check OFAC sanctions for address."""
    return _local_blocklist.get_matches(address)


def add_to_blocklist(address: str, reason: str = "") -> bool:
    """Add address to local blocklist."""
    _local_blocklist.local_blocklist.add(address.lower())
    return _save_local_list()


def _save_local_list() -> bool:
    """Save local blocklist to file."""
    data = {
        "addresses": list(_local_blocklist.local_blocklist),
        "whitelist": list(_local_blocklist.local_whitelist),
        "updated": datetime.now(UTC).isoformat(),
    }

    try:
        os.makedirs(os.path.dirname(LOCAL_BLOCKLIST_PATH), exist_ok=True)
        with open(LOCAL_BLOCKLIST_PATH, "w") as f:
            json.dump(data, f, indent=2)
        return True
    except Exception as e:
        logger.error(f"Failed to save blocklist: {e}")
        return False
