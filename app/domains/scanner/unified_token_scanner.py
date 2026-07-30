"""
Unified Token Scanner - v2.0 (DataBus-Powered)
================================================
SINGLE source of truth for ALL token scanning at RMI.

Gone: 45 raw httpx enrichment calls, no caching, no fallbacks.
Here: Parallel DataBus.fetch() calls with 4-layer defense:
  cache(SWR) → dedup → local precheck → credit-aware provider chain.

Every enrichment is a DataBus chain. DataBus handles:
  - Redis caching with stale-while-revalidate
  - Request deduplication (same query within 5s = one call)
  - Credit conservation (free providers auto-bumped when paid >80%)
  - Automatic fallback chains (DexScreener → Etherscan → Moralis → ...)
  - RAG auto-indexing

Architecture:
  scan_token(address, chain, tier) → parallel DataBus.fetch() → merge → score → return

Persistence (v2.1):
  Scan results are persisted to Postgres ``scan_results`` table + Redis cache
  as a fire-and-forget background task.  The API response is never blocked by
  persistence.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, ClassVar

from app.core.logging import get_logger

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

# ═══════════════════════════════════════════════════════════════
# DATA TYPES
# ═══════════════════════════════════════════════════════════════


@dataclass
class ScanResult:
    """Single unified scan result."""

    token_address: str
    chain: str
    scanned_at: str = ""

    # Core identity
    name: str = ""
    symbol: str = ""
    price_usd: float = 0.0
    market_cap_usd: float = 0.0
    liquidity_usd: float = 0.0
    volume_24h_usd: float = 0.0
    age_hours: float = 0.0

    # Security
    safety_score: float = 50.0  # 0=scam, 100=safe
    confidence: float = 0.0  # 0-100, how much data we had
    risk_level: str = "unknown"
    risk_flags: list[str] = field(default_factory=list)
    green_flags: list[str] = field(default_factory=list)

    # Enrichment results (keyed by DataBus chain name)
    security: dict | None = None
    holders: dict | None = None
    holder_health: dict | None = None
    liquidity: dict | None = None
    volume_auth: dict | None = None
    dev_reputation: dict | None = None
    rug_patterns: dict | None = None
    premium: dict | None = None  # bundle/cluster/sniper/mev/wash/copy/insider
    smart_money: dict | None = None
    whale_alerts: dict | None = None
    cross_chain: dict | None = None
    launches: dict | None = None
    chain_indexer_patterns: dict | None = None

    # Metadata
    modules_run: list[str] = field(default_factory=list)
    enrichment_sources: list[str] = field(default_factory=list)
    tier: str = "free"
    scan_duration_ms: float = 0.0


# ═══════════════════════════════════════════════════════════════
# SCANNER ORCHESTRATOR
# ═══════════════════════════════════════════════════════════════


class UnifiedTokenScanner:
    """
    THE single token scanner.

    Usage:
        scanner = UnifiedTokenScanner()
        result = await scanner.scan("0x...", "ethereum", tier="pro")
    """

    # Phase 1: Core identity - always run, needed for scoring
    CORE_CHAINS: ClassVar[list] = [
        "token_price",
        "token_metadata",
        "token_security",
    ]

    # Phase 2: Deep analysis - parallel, all free
    DEEP_CHAINS: ClassVar[list] = [
        "holder_data",
        "holder_health",
        "liquidity_risk",
        "dev_reputation",
        "rug_patterns",
        "volume_authenticity",
        "chain_indexer_patterns",
    ]

    # Phase 3: Premium - requires pro/elite tier
    PREMIUM_CHAINS: ClassVar[list] = [
        "bundle_detect",
        "cluster_map",
        "dev_finder",
        "sniper_detect",
        "bot_farm_detect",
        "copy_trade_detect",
        "insider_detect",
        "wash_trade_detect",
        "mev_detect",
        "fresh_wallet_analysis",
    ]

    # Phase 4: Intelligence - always free, enrich the report
    INTEL_CHAINS: ClassVar[list] = [
        "smart_money",
        "whale_alerts",
        "token_launches",
        "insider_detection",
        "cross_chain_entity",
    ]

    async def scan(
        self,
        address: str,
        chain: str = "solana",
        tier: str = "free",
        admin_key: str | None = None,
    ) -> ScanResult:
        """
        Full token security scan.

        Runs phases in dependency order:
          1. Core (price, meta, security) - needed for scoring
          2. Deep (holders, liquidity, dev, rug, volume)
          3. Premium (bundle, cluster, sniper, MEV, wash - tier-gated)
          4. Intel (smart money, whales, launches, cross-chain)
        """
        start = time.monotonic()
        params = {"address": address, "chain": chain}
        result = ScanResult(
            token_address=address,
            chain=chain,
            scanned_at=datetime.now(UTC).isoformat(),
            tier=tier,
        )

        try:
            # ── Phase 1: Core (sequential - needed for scoring) ──
            core_results = await self._fetch_batch(self.CORE_CHAINS, params, admin_key, timeout=15)
            self._merge_core(result, core_results)

            # ── Phase 2: Deep (parallel) ──
            deep_results = await self._fetch_batch(self.DEEP_CHAINS, params, admin_key, timeout=30)
            self._merge_deep(result, deep_results)

            # ── Phase 3: Premium (tier-gated) ──
            if tier in ("pro", "elite", "admin"):
                premium_results = await self._fetch_batch(self.PREMIUM_CHAINS, params, admin_key, timeout=30)
                self._merge_premium(result, premium_results)

            # ── Phase 4: Intel (free enrichment) ──
            intel_results = await self._fetch_batch(self.INTEL_CHAINS, params, admin_key, timeout=20)
            self._merge_intel(result, intel_results)

            # ── Score ──
            self._compute_score(result)

            # ── Persist: Postgres + Redis cache (fire-and-forget) ──
            asyncio.create_task(self._persist_result(result))

            # ── Publish high-risk findings to RAG pipeline ──
            if result.risk_level in ("high", "critical"):
                # 1. Publish via EventBus (in-process subscribers + Redis event channel)
                try:
                    from app.core.events import get_event_bus, ScanCompleted  # noqa: I001

                    bus = get_event_bus()
                    await bus.publish(
                        ScanCompleted(
                            token_address=result.token_address,
                            chain=result.chain,
                            safety_score=result.safety_score,
                            risk_level=result.risk_level,
                            risk_flags=result.risk_flags,
                            modules_run=result.modules_run,
                        )
                    )
                except Exception as e:
                    logger.debug("scan_event_publish_failed", error=str(e)[:80])

                # 2. Publish direct JSON to rmi:rag:ingest for rmi-rag subscriber
                try:
                    await self._publish_rag_finding(result)
                except Exception as e:
                    logger.debug("rag_finding_publish_failed", error=str(e)[:80])

            # ── Auto-label high-risk deployer wallets ──
            await self._auto_label_deployer(result)

        except (ValueError, TypeError, KeyError, OSError) as e:
            logger.debug("unknown_error", exc_info=True)
            logger.error(f"Scan failed for {address}: {e}")
            result.risk_flags.append(f"SCAN_ERROR: {str(e)[:100]}")

        result.scan_duration_ms = (time.monotonic() - start) * 1000
        return result

    async def _fetch_batch(
        self,
        chains: list[str],
        params: dict,
        admin_key: str | None,
        timeout: float = 30,  # noqa: ASYNC109
    ) -> dict[str, Any]:
        """Parallel DataBus fetch for multiple chains."""
        from app.databus import databus

        async def _fetch_one(chain_name: str) -> tuple:
            try:
                result = await asyncio.wait_for(
                    databus.fetch(chain_name, admin_key=admin_key, **params),
                    timeout=timeout,
                )
                return chain_name, result
            except TimeoutError:
                logger.warning(f"DataBus chain '{chain_name}' timed out after {timeout}s")
                return chain_name, None
            except (ValueError, TypeError, KeyError, OSError) as e:
                logger.debug("_fetch_one_error", exc_info=True)
                logger.warning(f"DataBus chain '{chain_name}' failed: {e}")
                return chain_name, None

        tasks = [_fetch_one(c) for c in chains]
        gathered = await asyncio.gather(*tasks, return_exceptions=True)

        results = {}
        for item in gathered:
            if isinstance(item, Exception):
                continue
            name, data = item
            if data is not None:
                results[name] = data
                # Track enrichment sources
                if isinstance(data, dict) and data.get("source"):
                    pass  # sources tracked in merge methods
        return results

    # ── MERGE METHODS ──

    def _merge_core(self, r: ScanResult, data: dict) -> None:
        """Extract core identity from price, metadata, and security checks."""
        r.modules_run.extend(data.keys())

        # Price
        price_data = data.get("token_price", {})
        if isinstance(price_data, dict):
            r.price_usd = float(price_data.get("price_usd", 0) or 0)
            r.market_cap_usd = float(price_data.get("market_cap_usd", 0) or 0)
            r.volume_24h_usd = float(price_data.get("volume_24h", 0) or 0)
            r.liquidity_usd = float(price_data.get("liquidity_usd", 0) or 0)
            if price_data.get("source"):
                r.enrichment_sources.append(f"price:{price_data['source']}")

        # Metadata
        meta = data.get("token_metadata", {})
        if isinstance(meta, dict):
            r.name = str(meta.get("name", "") or "")
            r.symbol = str(meta.get("symbol", "") or "")
            r.age_hours = float(meta.get("age_hours", 0) or 0)

        # Security checks (GoPlus - 42 checks)
        security = data.get("token_security", {})
        if isinstance(security, dict):
            r.security = security
            checks = security.get("checks", {})
            r.modules_run.append(f"security_checks:{len(checks)}")
            r.enrichment_sources.append(f"security:{security.get('source', 'goplus')}")

    def _merge_deep(self, r: ScanResult, data: dict) -> None:
        """Merge deep analysis results."""
        r.modules_run.extend(data.keys())

        r.holders = data.get("holder_data")
        r.holder_health = data.get("holder_health")
        r.liquidity = data.get("liquidity_risk")
        r.dev_reputation = data.get("dev_reputation")
        r.rug_patterns = data.get("rug_patterns")
        r.volume_auth = data.get("volume_authenticity")
        r.chain_indexer_patterns = data.get("chain_indexer_patterns")

    def _merge_premium(self, r: ScanResult, data: dict) -> None:
        """Merge premium-tier results."""
        r.modules_run.extend(data.keys())
        r.premium = {k: v for k, v in data.items() if k in self.PREMIUM_CHAINS and v is not None}

    def _merge_intel(self, r: ScanResult, data: dict) -> None:
        """Merge intelligence enrichment results."""
        r.modules_run.extend(data.keys())
        r.smart_money = data.get("smart_money")
        r.whale_alerts = data.get("whale_alerts")
        r.cross_chain = data.get("cross_chain_entity")
        r.launches = data.get("token_launches")

    def _compute_score(self, r: ScanResult) -> None:
        """
        Compute safety score (100 = safest, 0 = scam).

        Starts at 50 (neutral/unknown). Adjusted by security checks, holder data,
        liquidity, rug patterns, premium detections.
        """
        score = 50.0
        confidence = 0.0
        flags = []
        green = []

        # ── Security checks (GoPlus) ──
        if r.security:
            checks = r.security.get("checks", {})
            if checks:
                confidence += min(len(checks) * 2, 30)
                for check_id, check in checks.items():
                    status = check.get("status", "")
                    if status == "fail":
                        score -= 5
                        flags.append(check_id)
                    elif status == "pass":
                        score += 2
                        green.append(check_id)

        # ── Holder concentration ──
        if r.holders:
            h = r.holders
            top10 = float(h.get("top_10_pct", 0) or 0)
            if top10 > 80:
                score -= 15
                flags.append("HOLDER_CONCENTRATION_HIGH")
            elif top10 < 30:
                score += 5
                green.append("HOLDER_DISTRIBUTED")
            confidence += 5

        # ── Holder health ──
        if r.holder_health:
            hh = r.holder_health
            gini = float(hh.get("gini", 0) or 0)
            if gini > 0.8:
                score -= 10
                flags.append("HOLDER_GINI_HIGH")
            confidence += 5

        # ── Liquidity ──
        if r.liquidity:
            liq = r.liquidity
            locked = float(liq.get("locked_pct", 0) or 0)
            if locked < 50:
                score -= 20
                flags.append("LP_LOCK_LOW")
            elif locked > 90:
                score += 5
                green.append("LP_LOCKED")
            confidence += 5

        # ── Dev reputation ──
        if r.dev_reputation:
            dev = r.dev_reputation
            risk = float(dev.get("risk_score", 0) or 0)
            if risk > 70:
                score -= 25
                flags.append("DEV_HIGH_RISK")
            elif risk < 20:
                score += 5
                green.append("DEV_REPUTABLE")
            confidence += 5

        # ── Rug patterns ──
        if r.rug_patterns:
            rp = r.rug_patterns
            matches = int(rp.get("match_count", 0) or 0)
            if matches > 0:
                score -= matches * 10
                flags.append(f"RUG_PATTERN_MATCH:{matches}")
            confidence += 5

        # ── Volume authenticity ──
        if r.volume_auth:
            va = r.volume_auth
            fake_pct = float(va.get("fake_volume_pct", 0) or 0)
            if fake_pct > 50:
                score -= 20
                flags.append("VOLUME_FAKE")
            elif fake_pct < 10:
                score += 5
                green.append("VOLUME_AUTHENTIC")
            confidence += 5

        # ── Chain-indexer pattern detection (on-chain behavioral) ──
        if r.chain_indexer_patterns:
            cip = r.chain_indexer_patterns
            findings = cip.get("findings", cip.get("data", {}).get("findings", []))
            if findings:
                total_risk = sum(f.get("risk_score", 0) for f in findings)
                score -= min(total_risk, 40)
                for f in findings:
                    pattern = f.get("pattern", "unknown")
                    if f.get("detected"):
                        flags.append(f"ONCHAIN_{pattern.upper()}")
                confidence += min(len(findings) * 3, 10)

        # ── Premium detections ──
        if r.premium:
            premium_flags = 0
            for feature, data in r.premium.items():
                if data and isinstance(data, dict):
                    risk = data.get("risk", data.get("risk_level", ""))
                    if risk in ("HIGH", "CRITICAL"):
                        score -= 10
                        flags.append(f"PREMIUM_{feature.upper()}")
                        premium_flags += 1
            confidence += min(premium_flags * 5, 15)

        # ── Clamp ──
        r.safety_score = max(0.0, min(100.0, score))
        r.confidence = min(confidence, 100.0)

        # ── Risk level ──
        if r.safety_score >= 80:
            r.risk_level = "safe"
        elif r.safety_score >= 60:
            r.risk_level = "low"
        elif r.safety_score >= 40:
            r.risk_level = "medium"
        elif r.safety_score >= 20:
            r.risk_level = "high"
        else:
            r.risk_level = "critical"

        r.risk_flags = flags
        r.green_flags = green

    # ── RAG Pipeline Integration ─────────────────────────────────

    async def _publish_rag_finding(self, result: ScanResult) -> None:
        """Publish high-risk scan finding to rmi-rag via Redis channel ``rmi:rag:ingest``.

        rmi-rag's ``scanner_subscriber`` listens on this channel, generates embeddings,
        and upserts into Qdrant ``threat_intel`` collection for live threat search.
        """
        from app.core.redis import get_redis_async

        r = get_redis_async()
        if r is None:
            logger.warning("Redis unavailable — skipping RAG finding publish")
            return

        payload = {
            "token_address": result.token_address,
            "chain": result.chain,
            "name": result.name,
            "symbol": result.symbol,
            "safety_score": result.safety_score,
            "confidence": result.confidence,
            "risk_level": result.risk_level,
            "risk_flags": result.risk_flags[:20],
            "green_flags": result.green_flags[:10],
            "price_usd": result.price_usd,
            "liquidity_usd": result.liquidity_usd,
            "age_hours": result.age_hours,
            "scanned_at": result.scanned_at,
            "modules_run": result.modules_run[:30],
        }

        await r.publish("rmi:rag:ingest", json.dumps(payload))
        logger.info(
            "rag_finding_published",
            token=result.token_address,
            chain=result.chain,
            risk=result.risk_level,
        )

    # ── Deployer Auto-Labeling ────────────────────────────────────

    async def _auto_label_deployer(self, result: ScanResult) -> None:
        """Auto-label the deployer wallet when dev_reputation shows high risk.

        Called after every scan. Wires deployer data into:
          a) RAG wallet profile (ingest_wallet_profile)
          b) ClickHouse scam_addresses + deployer_history tables
          c) AutoLabeler behavioral observation system
        """
        dev = result.dev_reputation
        if not dev or not isinstance(dev, dict):
            return

        deployer_addr = dev.get("deployer_address") or dev.get("address", "")
        risk_score = float(dev.get("risk_score", 0) or 0)
        risk_label = str(dev.get("risk_label", dev.get("reputation_label", "UNKNOWN")))

        # Only label if risk is significant
        if risk_score < 70 or not deployer_addr or deployer_addr == "unknown":
            return

        labels = ["high_risk_deployer"] if risk_score < 85 else ["malicious_deployer"]
        chain = result.chain

        try:
            # (a) Ingest wallet profile to RAG
            try:
                from app.rag.ingestion import ingest_wallet_profile as _ingest_wallet

                await _ingest_wallet(
                    wallet_address=deployer_addr,
                    chain=chain,
                    labels=labels,
                    risk_score=risk_score,
                    entities=[f"deployer_of:{result.token_address[:16]}"],
                    extra={
                        "token_name": result.name,
                        "token_symbol": result.symbol,
                        "safety_score": result.safety_score,
                        "source": "unified_scanner",
                        "risk_flags": result.risk_flags[:10],
                    },
                )
            except ImportError:
                logger.debug("unified_wallet_rag_ingest_skipped")
            except Exception as exc:
                logger.warning("unified_wallet_rag_ingest_failed", error=str(exc)[:100])

            # (b) Persist to ClickHouse
            try:
                from app.wallet_memory.storage import get_storage

                storage = get_storage()
                storage_task = asyncio.gather(
                    storage.save_scam_address(
                        address=deployer_addr,
                        chain_id=chain,
                        source="unified_token_scanner",
                        threat_type=risk_label.lower(),
                        confidence=min(1.0, risk_score / 100.0),
                        evidence=f"Scanned token {result.token_address[:16]} risk={risk_label} flags={','.join(result.risk_flags[:5])}",
                    ),
                    storage.save_deployer_event(
                        deployer=deployer_addr,
                        chain_id=chain,
                        token_address=result.token_address,
                        token_name=result.name,
                        token_symbol=result.symbol,
                        deployed_at=None,
                        outcome="scam" if risk_label == "CRITICAL" else "suspicious",
                        is_scam=(risk_label == "CRITICAL"),
                        risk_score=risk_score,
                    ),
                    return_exceptions=True,
                )
                await storage_task
            except ImportError:
                logger.debug("unified_wallet_storage_persist_skipped")
            except Exception as exc:
                logger.warning("unified_wallet_storage_persist_failed", error=str(exc)[:100])

            # (c) Feed observation to AutoLabeler
            try:
                from app.auto_labeler import get_auto_labeler

                labeler = get_auto_labeler()
                await labeler.observe_wallet(
                    address=deployer_addr,
                    chain=chain,
                    observations={
                        "event": "token_deployed",
                        "token_address": result.token_address,
                        "risk_score": risk_score,
                        "reputation_label": risk_label,
                    },
                )
            except ImportError:
                logger.debug("unified_auto_labeler_observe_skipped")
            except Exception as exc:
                logger.warning("unified_auto_labeler_observe_failed", error=str(exc)[:100])

            logger.info(
                "deployer_auto_labeled",
                deployer=deployer_addr[:16],
                chain=chain,
                labels=labels,
                risk=risk_label,
            )
        except Exception as exc:
            logger.warning("unified_auto_label_deployer_failed", error=str(exc)[:100])

    # ── Persistence ──────────────────────────────────────────────

    async def _persist_result(self, result: ScanResult) -> None:
        """Persist scan result to Postgres + Redis (fire-and-forget).

        1. Save ScanResult ORM row to ``scan_results`` table
        2. Best-effort upsert token into ``tokens`` table if new
           (gracefully handles ORM/db schema mismatch)
        3. Cache in Redis ``sentinelscan:{chain}:{address}`` for 1 hour
        4. All errors are logged but never raised (fire-and-forget contract)
        """
        # ── 1. Save scan_result row ──
        try:
            from app.models.scan import ScanResult as ScanResultORM

            session = await self._get_async_session()
            if session is None:
                return
            async with session:
                raw_json = json.dumps(self._to_persist_dict(result), default=str)

                scan_orm = ScanResultORM(
                    id=uuid.uuid4(),
                    token_address=result.token_address,
                    chain=result.chain,
                    symbol=result.symbol[:64] if result.symbol else "",
                    name=result.name[:256] if result.name else "",
                    safety_score=int(round(result.safety_score)),
                    risk_score=int(round(100 - result.safety_score)),
                    risk_level=result.risk_level,
                    risk_flags=result.risk_flags[:50],
                    warnings=result.green_flags[:50],
                    modules_run=result.modules_run[:50],
                    extra={"raw": raw_json},
                    scanned_at=datetime.fromisoformat(result.scanned_at) if result.scanned_at else datetime.now(UTC),
                    tier_required=result.tier,
                    confidence=int(round(result.confidence)),
                )
                session.add(scan_orm)

                # ── 2. Best-effort token upsert ──
                await self._maybe_upsert_token(session, result)

                await session.commit()
                logger.info(
                    "scan_persisted",
                    chain=result.chain,
                    address=result.token_address,
                    scan_id=str(scan_orm.id),
                )

        except Exception:
            logger.exception(
                "scan_persist_failed",
                chain=result.chain,
                address=result.token_address,
            )
            # Fire-and-forget: never propagate

        # ── 3. Redis cache (independent of Postgres) ──
        try:
            from app.core.redis import get_redis_async

            r = get_redis_async()
            if r is not None:
                cache_key = f"sentinelscan:{result.chain}:{result.token_address}"
                cache_val = json.dumps(self._to_persist_dict(result), default=str)
                await r.setex(cache_key, 3600, cache_val)
                logger.debug("scan_redis_cached", key=cache_key)
        except Exception:
            logger.exception("scan_redis_cache_failed")

    def _to_persist_dict(self, result: ScanResult) -> dict[str, Any]:
        """Convert ScanResult to a plain dict suitable for JSON storage."""
        return {
            "token_address": result.token_address,
            "chain": result.chain,
            "scanned_at": result.scanned_at,
            "name": result.name,
            "symbol": result.symbol,
            "price_usd": result.price_usd,
            "market_cap_usd": result.market_cap_usd,
            "liquidity_usd": result.liquidity_usd,
            "volume_24h_usd": result.volume_24h_usd,
            "age_hours": result.age_hours,
            "safety_score": result.safety_score,
            "confidence": result.confidence,
            "risk_level": result.risk_level,
            "risk_flags": result.risk_flags,
            "green_flags": result.green_flags,
            "tier": result.tier,
            "chain_indexer_patterns": result.chain_indexer_patterns,
            "scan_duration_ms": result.scan_duration_ms,
            "modules_run": result.modules_run,
            "enrichment_sources": result.enrichment_sources,
        }

    async def _maybe_upsert_token(
        self,
        session: Any,
        result: ScanResult,
    ) -> None:
        """Best-effort upsert of discovered token into the ``tokens`` table.

        The ORM model (``app.models.token.Token``) may not match the actual
        database schema — this is a known pre-existing discrepancy.  We catch
        ALL errors here so the caller's scan-result save is never affected.
        """
        if not result.symbol:
            return
        try:
            from sqlalchemy import select

            from app.models.token import Token

            stmt = select(Token).where(
                Token.chain == result.chain,
                Token.address == result.token_address,
            )
            tok_result = await session.execute(stmt)
            existing_token = tok_result.scalar_one_or_none()

            if existing_token is None:
                token_orm = Token(
                    address=result.token_address,
                    chain=result.chain,
                    name=result.name[:256] if result.name else "",
                    symbol=result.symbol[:64] if result.symbol else "",
                    decimals=9,
                    total_supply=0.0,
                    circulating_supply=0.0,
                    holders=0,
                    verified=False,
                    risk_score=int(round(100 - result.safety_score)),
                    risk_level=result.risk_level,
                    extra={"first_scanned_at": result.scanned_at},
                )
                session.add(token_orm)
                logger.info(
                    "token_created_from_scan",
                    chain=result.chain,
                    address=result.token_address,
                    symbol=result.symbol,
                )
        except Exception:
            logger.info(
                "token_upsert_skipped",
                chain=result.chain,
                address=result.token_address,
                detail="Token ORM model may not match DB schema; upsert skipped",
            )

    @staticmethod
    async def _get_async_session() -> AsyncSession | None:
        """Get an async SQLAlchemy session, falling back to a direct connection
        if the global engine uses an unreachable host (e.g. ``localhost``
        inside a Docker container where Postgres is on a different host).

        The fallback constructs the URL from env vars (Docker defaults) when
        the global engine's URL points to a non-existent host.
        """
        from app.core.database import async_session_factory, engine

        # Quick connectivity test — try connecting and executing SELECT 1
        try:
            from sqlalchemy import text

            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            # Engine works — use the standard factory
            return async_session_factory()
        except (ConnectionRefusedError, OSError, ConnectionError):
            pass
        except Exception:
            pass

        # Global engine can't reach the database (e.g. localhost in Docker).
        # Build a working engine from available env vars.
        import os

        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

        host = os.getenv("POSTGRES_HOST") or os.getenv("DB_HOST") or "rmi-postgres"
        user = os.getenv("POSTGRES_USER", "rmi")
        password = os.getenv("POSTGRES_PASSWORD", "rmi")
        port = os.getenv("POSTGRES_PORT", "5432")
        db = os.getenv("POSTGRES_DB", "rmi")
        fallback_url = f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{db}"

        try:
            fallback_engine = create_async_engine(fallback_url, pool_size=2, max_overflow=2)
            fallback_factory = async_sessionmaker(
                fallback_engine,
                class_=AsyncSession,
                expire_on_commit=False,
            )
            logger.info("scan_persist_fallback_engine", host=host, db=db)
            return fallback_factory()
        except Exception as exc:
            logger.warning(
                "scan_persist_fallback_failed",
                host=host,
                error=str(exc)[:100],
            )
            return None


# ═══════════════════════════════════════════════════════════════
# SINGLETON
# ═══════════════════════════════════════════════════════════════

_scanner: UnifiedTokenScanner | None = None


def get_token_scanner() -> UnifiedTokenScanner:
    global _scanner
    if _scanner is None:
        _scanner = UnifiedTokenScanner()
    return _scanner
