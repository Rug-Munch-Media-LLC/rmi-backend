"""UnifiedTokenScanner — main scan orchestration."""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from typing import Any, ClassVar

from app.core.logging import get_logger
from app.domains.scanner.unified.model import ScanResult
from app.domains.scanner.unified.persistence import (
    auto_label_deployer,
    persist_result,
    publish_rag_finding,
)
from app.domains.scanner.unified.scoring import compute_score

logger = get_logger(__name__)


class UnifiedTokenScanner:
    """THE single token scanner.

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
        """Full token security scan.

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
            compute_score(result)

            # ── Record own-data coverage metrics ──
            try:
                from app.domains.scanner.metrics import record_token_scan_coverage

                modules_run_list = [{"module": m, "status": "success", "result": {}} for m in result.modules_run]
                record_token_scan_coverage(result.enrichment_sources, modules_run_list)
            except ImportError:
                pass
            except Exception:
                pass

            # ── Persist: Postgres + Redis cache (fire-and-forget) ──
            asyncio.create_task(persist_result(result))

            # ── Publish high-risk findings to RAG pipeline ──
            if result.risk_level in ("high", "critical"):
                try:
                    from app.core.events import ScanCompleted, get_event_bus

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

                try:
                    await publish_rag_finding(result)
                except Exception as e:
                    logger.debug("rag_finding_publish_failed", error=str(e)[:80])

            # ── Auto-label high-risk deployer wallets ──
            await auto_label_deployer(result)

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
        timeout: float = 30,
    ) -> dict[str, Any]:
        """Parallel DataBus fetch for multiple chains."""
        from app.databus import databus

        async def _fetch_one(chain_name: str) -> tuple:
            try:
                result_data = await asyncio.wait_for(
                    databus.fetch(chain_name, admin_key=admin_key, **params),
                    timeout=timeout,
                )
                return chain_name, result_data
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
        return results

    # ── MERGE METHODS ──

    def _merge_core(self, r: ScanResult, data: dict) -> None:
        """Extract core identity from price, metadata, and security checks."""
        r.modules_run.extend(data.keys())

        price_data = data.get("token_price", {})
        if isinstance(price_data, dict):
            r.price_usd = float(price_data.get("price_usd", 0) or 0)
            r.market_cap_usd = float(price_data.get("market_cap_usd", 0) or 0)
            r.volume_24h_usd = float(price_data.get("volume_24h", 0) or 0)
            r.liquidity_usd = float(price_data.get("liquidity_usd", 0) or 0)
            if price_data.get("source"):
                r.enrichment_sources.append(f"price:{price_data['source']}")

        meta = data.get("token_metadata", {})
        if isinstance(meta, dict):
            r.name = str(meta.get("name", "") or "")
            r.symbol = str(meta.get("symbol", "") or "")
            r.age_hours = float(meta.get("age_hours", 0) or 0)

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
