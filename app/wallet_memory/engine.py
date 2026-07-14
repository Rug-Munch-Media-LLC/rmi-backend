"""
Wallet Memory Engine - Orchestrator for the Wallet Memory Bank.
================================================================
Single entry point for all wallet intelligence queries. Combines:
  - Storage (ClickHouse + Redis)
  - Clustering (6 heuristics, Union-Find)
  - Labels (6 live sources + static databases)
  - Risk scoring (composite: label + cluster + deployer history)
  - Entity resolution (cross-chain linking)

Primary API:
  get_deployer_intelligence(address, chain) - what SENTINEL calls
  get_wallet_profile(address, chain) - what WalletSafe frontend calls
  flag_deployer(address, chain, reason) - the data flywheel from scans
"""

from app.core.logging import get_logger
from datetime import UTC, datetime
from typing import Any

from .clustering import (
    WalletClusteringEngine,
)
from .entity_resolver import EntityResolver
from .ingestion import get_ingestion_pipeline
from .labels import get_label_service
from .risk_scoring import RiskScorer
from .storage import WalletStorage, get_storage

logger = get_logger("wallet_memory.engine")


class WalletMemoryEngine:
    """
    The brain of the Wallet Memory Bank.
    Coordinates storage, clustering, labels, risk scoring, and entity resolution.
    """

    def __init__(self, storage: WalletStorage | None = None):
        self.storage = storage or get_storage()
        self.clustering = WalletClusteringEngine()
        self.labels = get_label_service()
        self.risk_scorer = RiskScorer(self.storage, self.labels, self.clustering)
        self.entity_resolver = EntityResolver(self.storage)
        self.ingestion = get_ingestion_pipeline(engine=self, storage=self.storage)

    # ── Primary API: Deployer Intelligence (SENTINEL calls this) ───

    async def get_deployer_intelligence(self, address: str, chain: str) -> dict[str, Any]:
        """
        One-shot call returning everything the token scanner needs about a deployer.

        Returns:
            {
                "address": str,
                "chain": str,
                "entity_id": str | None,
                "entity_label": str,
                "entity_category": str,
                "linked_wallets": [...],
                "risk_score": float (0-100),
                "risk_level": str,
                "scam_associations": [...],
                "deployer_history": [...],
                "cross_chain_presence": [...],
                "labels": [...],
                "confidence": float,
            }
        """
        addr = address.lower().strip()
        result: dict[str, Any] = {
            "address": addr,
            "chain": chain,
            "entity_id": None,
            "entity_label": "",
            "entity_category": "unknown",
            "linked_wallets": [],
            "risk_score": 0.0,
            "risk_level": "unknown",
            "scam_associations": [],
            "deployer_history": [],
            "cross_chain_presence": [],
            "labels": [],
            "confidence": 0.0,
        }

        # 1. Labels (from 6 live sources + static databases)
        try:
            label_report = await self.labels.resolve(addr, chain)
            if label_report:
                result["labels"] = [
                    {
                        "source": line.get("source", ""),
                        "label": line.get("label", ""),
                        "category": line.get("category", ""),
                        "confidence": line.get("confidence", 1.0),
                    }
                    for line in label_report.get("labels", [])
                ]
                result["entity_label"] = label_report.get("primary_label", "")
                result["entity_category"] = label_report.get("primary_category", "")
                if label_report.get("is_known_scam"):
                    result["scam_associations"].append(
                        {
                            "source": "address_labeler",
                            "type": "known_scam",
                            "label": label_report.get("primary_label", ""),
                        }
                    )
                if label_report.get("is_sanctioned"):
                    result["scam_associations"].append(
                        {
                            "source": "ofac",
                            "type": "sanctioned",
                            "label": "OFAC sanctioned entity",
                        }
                    )
        except Exception as e:
            logger.debug(f"Label resolution failed for {addr}: {e}")

        # 2. Entity from storage (ClickHouse / Redis)
        try:
            entity = await self.storage.get_entity_for_wallet(addr, chain)
            if entity:
                result["entity_id"] = entity.get("entity_id")
                if not result["entity_label"] and entity.get("entity_label"):
                    result["entity_label"] = entity["entity_label"]
                if entity.get("entity_category") and result["entity_category"] == "unknown":
                    result["entity_category"] = entity["entity_category"]
                result["confidence"] = entity.get("confidence_score", 0.0)

                # Get linked wallets in this entity
                if entity.get("entity_id"):
                    linked = await self.storage.get_entity_wallets(entity["entity_id"])
                    result["linked_wallets"] = [
                        {
                            "address": w["wallet_address"],
                            "chain": w["chain_id"],
                            "confidence": w["confidence_score"],
                        }
                        for w in linked
                        if w["wallet_address"].lower() != addr
                    ]
        except Exception as e:
            logger.debug(f"Entity lookup failed for {addr}: {e}")

        # 3. Scam database check
        try:
            scam = await self.storage.check_scam_address(addr, chain)
            if scam:
                result["scam_associations"].append(
                    {
                        "source": scam.get("source", "unknown"),
                        "type": scam.get("threat_type", "unknown"),
                        "confidence": scam.get("confidence", 1.0),
                    }
                )
        except Exception as e:
            logger.debug(f"Scam check failed for {addr}: {e}")

        # 4. Deployer history
        try:
            history = await self.storage.get_deployer_history(addr, chain)
            result["deployer_history"] = history
        except Exception as e:
            logger.debug(f"Deployer history failed for {addr}: {e}")

        # 5. Cross-chain presence (bridge mappings)
        try:
            bridges = await self.storage.get_bridge_links(addr, chain)
            result["cross_chain_presence"] = bridges
        except Exception as e:
            logger.debug(f"Bridge lookup failed for {addr}: {e}")

        # 6. Composite risk score
        try:
            risk = await self.risk_scorer.score(addr, chain, result)
            result["risk_score"] = risk.get("score", 0.0)
            result["risk_level"] = risk.get("level", "unknown")
        except Exception as e:
            logger.debug(f"Risk scoring failed for {addr}: {e}")

        # 7. Cluster from in-memory engine (if data was fed)
        try:
            cluster = self.clustering.get_cluster_for_wallet(addr)
            if cluster and len(cluster.wallets) > 1:
                # Merge cluster members into linked_wallets if not already present
                existing_addrs = {w["address"].lower() for w in result["linked_wallets"]}
                for w in cluster.wallets:
                    if w.lower() != addr and w.lower() not in existing_addrs:
                        result["linked_wallets"].append(
                            {
                                "address": w,
                                "chain": chain,
                                "confidence": round(cluster.confidence, 3),
                                "heuristic": "clustering",
                            }
                        )
                # Boost confidence from multiple detection methods
                if cluster.detection_methods:
                    method_bonus = min(0.2, len(cluster.detection_methods) * 0.05)
                    result["confidence"] = min(0.99, result["confidence"] + method_bonus)
        except Exception as e:
            logger.debug(f"Cluster lookup failed for {addr}: {e}")

        return result

    # ── Primary API: Wallet Profile (WalletSafe frontend calls this) ─

    async def get_wallet_profile(self, address: str, chain: str, depth: int = 1) -> dict[str, Any]:
        """
        Full wallet profile for the WalletSafe product.
        Includes entity info, risk, labels, cluster, and connected wallets.
        Auto-triggers ingestion pipeline when profile is empty or stale.
        """
        addr = address.lower().strip()

        # Check storage first (may have been enriched before)
        stored = await self.storage.get_wallet_profile(addr, chain)

        # ── Auto-ingestion: fetch fresh data if profile is empty or stale ──
        if self.ingestion and self.ingestion.should_ingest(addr, chain, stored):
            try:
                ingest_ok = await self.ingestion.ingest_if_needed(addr, chain)
                if ingest_ok:
                    logger.info(f"Ingestion triggered for {addr} on {chain}")
                    # Re-fetch stored profile after ingestion may have updated it
                    stored = await self.storage.get_wallet_profile(addr, chain)
            except Exception as e:
                logger.debug(f"Ingestion trigger failed for {addr}: {e}")

        # Build fresh profile
        intel = await self.get_deployer_intelligence(addr, chain)

        profile = {
            "address": addr,
            "chain": chain,
            "entity_id": intel.get("entity_id"),
            "entity_label": intel.get("entity_label"),
            "entity_category": intel.get("entity_category"),
            "risk_score": intel.get("risk_score", 0),
            "risk_level": intel.get("risk_level", "unknown"),
            "labels": intel.get("labels", []),
            "scam_associations": intel.get("scam_associations", []),
            "deployer_history": intel.get("deployer_history", []),
            "cross_chain_presence": intel.get("cross_chain_presence", []),
            "linked_wallets": intel.get("linked_wallets", []),
            "confidence": intel.get("confidence", 0),
            # From stored profile if available
            "total_transactions": stored.get("total_transactions", 0) if stored else 0,
            "total_volume": stored.get("total_volume", 0) if stored else 0,
            "first_seen": stored.get("first_seen_at") if stored else None,
            "last_seen": stored.get("last_seen_at") if stored else None,
        }

        # Save/refresh the profile
        try:
            await self._persist_profile(profile)
        except Exception as e:
            logger.debug(f"Profile persist failed: {e}")

        return profile

    # ── Data flywheel: flag deployer from scan results ────────────

    async def flag_deployer(
        self,
        address: str,
        chain: str,
        reason: str = "high_risk_token",
        token: str = "",
        score: float = 0.0,
    ) -> bool:
        """
        Feed scan results back into the Wallet Memory Bank.
        This is the data flywheel: scans produce intelligence that makes future scans better.
        """
        addr = address.lower().strip()

        # Save scam address if high risk
        if score >= 70:
            try:
                await self.storage.save_scam_address(
                    address=addr,
                    chain_id=chain,
                    source="sentinel_scan",
                    threat_type=reason,
                    confidence=round(min(1.0, score / 100), 2),
                    evidence=f"token={token},risk_score={score:.1f}",
                )
            except Exception as e:
                logger.warning(f"Failed to flag deployer {addr}: {e}")

        # Record the deployment event
        if token:
            try:
                is_scam = score >= 70
                await self.storage.save_deployer_event(
                    deployer=addr,
                    chain_id=chain,
                    token_address=token,
                    outcome="flagged" if is_scam else "unknown",
                    is_scam=is_scam,
                    risk_score=round(score / 100, 2),
                    deployed_at=datetime.now(UTC),
                )
            except Exception as e:
                logger.warning(f"Failed to save deployer event for {addr}: {e}")

        # Update the wallet profile with new risk info
        try:
            stored = await self.storage.get_wallet_profile(addr, chain) or {}
            new_risk = max(stored.get("risk_score", 0) or 0, score)
            await self.storage.save_wallet_profile(
                {
                    "wallet_address": addr,
                    "chain_id": chain,
                    "risk_score": round(new_risk / 100, 2),
                    "risk_level": _risk_level(new_risk),
                    "entity_id": stored.get("entity_id", ""),
                    "entity_label": stored.get("entity_label", ""),
                    "entity_category": stored.get("entity_category", "unknown"),
                    "total_transactions": stored.get("total_transactions", 0),
                    "total_volume": stored.get("total_volume", 0),
                    "unique_counterparties": stored.get("unique_counterparties", 0),
                    "first_seen_at": stored.get("first_seen_at", datetime.now(UTC)),
                    "last_seen_at": datetime.now(UTC),
                }
            )
        except Exception as e:
            logger.debug(f"Profile update after flag failed: {e}")

        logger.info(f"Flagged {addr} on {chain}: reason={reason}, score={score:.1f}")
        return True

    # ── Search across all wallets ────────────────────────────────

    async def search_wallets(self, query: str, chain: str = "", limit: int = 20) -> list[dict]:
        """
        Search for wallets by address prefix, label, or entity name.
        Uses Redis search first, then ClickHouse if available.
        """
        results = []
        q = query.lower().strip()

        # Try Redis key prefix scan
        try:
            redis = self.storage._redis
            if not redis:
                await self.storage._ensure_connections()
                redis = self.storage._redis

            if redis:
                # Scan profile keys
                pattern = f"wm:profile:*{q}*"
                async for key in redis.scan_iter(match=pattern, count=100):
                    cached = await redis.get(key)
                    if cached:
                        import json

                        profile = json.loads(cached)
                        if len(results) < limit:
                            results.append(profile)
        except Exception as e:
            logger.debug(f"Redis search failed: {e}")

        return results[:limit]

    # ── Feed transactions into clustering engine ─────────────────

    def feed_transactions(self, transactions: list[dict], chain_id: str = ""):
        """
        Feed transaction data into the in-memory clustering engine.
        Call this when pulling data from Helius/Etherscan for a wallet.

        Expected transaction dict keys:
          address, counterparty, timestamp (datetime or ISO string), amount, program
        """
        for tx in transactions:
            addr = tx.get("address", tx.get("from", "")).lower()
            cp = tx.get("counterparty", tx.get("to", "")).lower()
            ts = tx.get("timestamp")
            if isinstance(ts, str):
                from datetime import datetime as dt

                ts = dt.fromisoformat(ts.replace("Z", "+00:00"))
            elif not isinstance(ts, datetime):
                ts = datetime.now(UTC)

            self.clustering.add_transaction(
                address=addr,
                counterparty=cp,
                timestamp=ts,
                amount=tx.get("amount", 0),
                chain_id=chain_id,
                program=tx.get("program", ""),
            )

    # ── Persist clusters to storage ──────────────────────────────

    async def persist_clusters(self) -> int:
        """Run all heuristics and persist resulting clusters to ClickHouse/Redis."""
        clusters = self.clustering.run_all_heuristics()
        persisted = 0

        for cluster in clusters:
            for wallet in cluster.wallets:
                try:
                    await self.storage.save_entity_link(
                        entity_id=cluster.cluster_id,
                        wallet_address=wallet,
                        chain_id="",  # Cross-chain cluster
                        heuristic_type=",".join(cluster.detection_methods),
                        confidence=cluster.confidence,
                        label=cluster.label,
                        category=cluster.category,
                    )
                    persisted += 1
                except Exception as e:
                    logger.debug(f"Entity link persist failed for {wallet}: {e}")

        logger.info(f"Persisted {persisted} entity links from {len(clusters)} clusters")
        return persisted

    # ── Internal ─────────────────────────────────────────────────

    async def _persist_profile(self, profile: dict):
        """Save a wallet profile to storage."""
        await self.storage.save_wallet_profile(
            {
                "wallet_address": profile["address"],
                "chain_id": profile["chain"],
                "entity_id": profile.get("entity_id", ""),
                "entity_label": profile.get("entity_label", ""),
                "entity_category": profile.get("entity_category", "unknown"),
                "risk_score": profile.get("risk_score", 0),
                "risk_level": profile.get("risk_level", "unknown"),
                "total_transactions": profile.get("total_transactions", 0),
                "total_volume": profile.get("total_volume", 0),
                "unique_counterparties": 0,
                "first_seen_at": profile.get("first_seen_at") or datetime.now(UTC),
                "last_seen_at": datetime.now(UTC),
            }
        )


# ── Risk level helper ───────────────────────────────────────────


def _risk_level(score: float) -> str:
    if score >= 80:
        return "critical"
    elif score >= 60:
        return "high"
    elif score >= 40:
        return "medium"
    elif score >= 20:
        return "low"
    return "minimal"


# ── Global singleton ────────────────────────────────────────────

_engine: WalletMemoryEngine | None = None


def get_wallet_engine() -> WalletMemoryEngine:
    """Get global wallet memory engine instance."""
    global _engine
    if _engine is None:
        _engine = WalletMemoryEngine()
    return _engine
