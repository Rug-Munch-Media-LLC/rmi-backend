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
"""

import asyncio
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, ClassVar

from app.core.logging import get_logger

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
                premium_results = await self._fetch_batch(
                    self.PREMIUM_CHAINS, params, admin_key, timeout=30
                )
                self._merge_premium(result, premium_results)

            # ── Phase 4: Intel (free enrichment) ──
            intel_results = await self._fetch_batch(
                self.INTEL_CHAINS, params, admin_key, timeout=20
            )
            self._merge_intel(result, intel_results)

            # ── Score ──
            self._compute_score(result)

        except Exception as e:
            logger.error(f"Scan failed for {address}: {e}")
            result.risk_flags.append(f"SCAN_ERROR: {str(e)[:100]}")

        result.scan_duration_ms = (time.monotonic() - start) * 1000
        return result

    async def _fetch_batch(
        self,
        chains: list[str],
        params: dict,
        admin_key: str | None,
        timeout: float = 30,
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
            except Exception as e:
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


# ═══════════════════════════════════════════════════════════════
# SINGLETON
# ═══════════════════════════════════════════════════════════════

_scanner: UnifiedTokenScanner | None = None


def get_token_scanner() -> UnifiedTokenScanner:
    global _scanner
    if _scanner is None:
        _scanner = UnifiedTokenScanner()
    return _scanner
