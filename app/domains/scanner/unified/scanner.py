"""UnifiedTokenScanner — main scan orchestration.

Degodded 2026-08-07: defensive scalar-coercion + risk-mapping helpers moved to
``unified/_coercion.py``; backend-compat wrappers (``scan_token_unified``,
``_to_legacy_dict``) moved to ``unified/_legacy.py``. 2026-08-08: SENTINEL
module → typed ``DeepScanProfile`` mapping (``_collect_deep_profile`` /
``_module_result``) moved to ``unified/deep_profile.py``. This module keeps
the class plus the scan-status helpers (``scan_status`` public entry point).
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import UTC, datetime
from typing import Any, ClassVar

from app.core.logging import get_logger
from app.domains.scanner.unified._coercion import (
    _extract_meta_identity,
    _resolve_scan_tier,
)
from app.domains.scanner.unified._legacy import _from_legacy_dict, _to_legacy_dict, scan_token_unified
from app.domains.scanner.unified.deep_profile import collect_deep_profile
from app.domains.scanner.unified.model import ScanResult
from app.domains.scanner.unified.persistence import (
    auto_label_deployer,
    persist_result,
    publish_rag_finding,
    read_stability,
)
from app.domains.scanner.unified.scoring import compute_score

logger = get_logger(__name__)

__all__ = [
    "UnifiedTokenScanner",
    "scan_status",
    "scan_token_unified",
]


# ── Scan status / background-phase config ───────────────────────────────
# Heavy phases (deep/intel/SENTINEL/news) run in a background task so the
# HTTP scan response returns within SCAN_HARD_TIMEOUT_S instead of blocking
# ~110s while HTTP clients (30-60s timeouts) drop the connection. Progress
# plus the final result are published to Redis under
# ``scanner:status:{chain}:{address}`` and polled by
# ``GET /api/v2/scanner/token/scan/{chain}/{address}/status``.
SCAN_HARD_TIMEOUT_S = float(os.getenv("SCAN_HARD_TIMEOUT_S", "25"))
SCAN_STATUS_TTL = int(os.getenv("SCAN_STATUS_TTL", "900"))  # 15 min


def _scan_status_key(chain: str, address: str) -> str:
    return f"scanner:status:{chain}:{address}"


async def _store_scan_status(chain: str, address: str, payload: dict[str, Any]) -> None:
    """Best-effort publish of scan status to Redis (never raises)."""
    try:
        from app.core.redis import get_redis_async

        r = get_redis_async()
        if r is None:
            return
        await r.setex(_scan_status_key(chain, address), SCAN_STATUS_TTL, json.dumps(payload, default=str))
    except Exception as exc:
        logger.debug("scan_status_store_failed", chain=chain, address=address[:12], error=str(exc)[:80])


async def scan_status(chain: str, address: str) -> dict[str, Any] | None:
    """Read the status of an in-flight scan (or a completed cached result).

    Returns ``None`` when no status exists for the token. Falls back to the
    1h result cache written by ``persist_result`` for completed scans.
    """
    try:
        from app.core.redis import get_redis_async

        r = get_redis_async()
        if r is None:
            return None

        raw = await r.get(_scan_status_key(chain, address))
        if raw:
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    return parsed
            except (TypeError, ValueError):
                return None

        raw = await r.get(f"sentinelscan:{chain}:{address}")
        if raw:
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    return {"state": "complete", "source": "result_cache", "result": parsed}
            except (TypeError, ValueError):
                return None
    except Exception as exc:
        logger.debug("scan_status_read_failed", chain=chain, address=address[:12], error=str(exc)[:80])
    return None


def _status_result_dict(result: ScanResult, background_status: str = "running") -> dict[str, Any]:
    """JSON-safe snapshot of a ScanResult for the status endpoint / Redis.

    Reuses the legacy serializer (already JSON-safe) and adds the background
    phase status so clients can tell whether deep/intel/news are still running.
    """
    data = _to_legacy_dict(result)
    data["background_status"] = background_status
    return data


async def _gather_freshness(tasks: list[Any]) -> dict[str, Any]:
    """Aggregate per-wallet freshness results into a token-level summary.

    Runs the given freshness tasks concurrently and returns a summary dict
    with the fresh-wallet concentration signal (``fresh_wallet_pct`` is the
    share of analyzed top holders classified FRESH_SUSPICIOUS).
    """
    results = await asyncio.gather(*tasks, return_exceptions=True)
    analyzed: list[dict[str, Any]] = []
    for res in results:
        if not isinstance(res, BaseException) and isinstance(res, dict) and res.get("result") == "scanned":
            analyzed.append(res)

    if not analyzed:
        return {"result": "unavailable", "reason": "no freshness data", "analyzed": 0}

    classifications = [r.get("classification", "ESTABLISHED") for r in analyzed]
    suspicious = sum(1 for c in classifications if c == "FRESH_SUSPICIOUS")
    clean = sum(1 for c in classifications if c == "FRESH_CLEAN")
    established = sum(1 for c in classifications if c == "ESTABLISHED")
    avg_score = sum(float(r.get("freshness_score", 0) or 0) for r in analyzed) / len(analyzed)

    return {
        "result": "scanned",
        "analyzed": len(analyzed),
        "fresh_suspicious": suspicious,
        "fresh_clean": clean,
        "established": established,
        "fresh_wallet_pct": round(suspicious / len(analyzed) * 100, 1) if analyzed else 0.0,
        "avg_freshness_score": round(avg_score, 1),
        "wallets": [
            {
                "address": r.get("address"),
                "freshness_score": r.get("freshness_score"),
                "classification": r.get("classification"),
                "age_days": r.get("age_days"),
                "total_txs": r.get("total_txs"),
                "funded_by_cex": r.get("funded_by_cex"),
            }
            for r in analyzed
        ],
    }


class UnifiedTokenScanner:
    """THE single token scanner.

    Usage:
        scanner = UnifiedTokenScanner()
        result = await scanner.scan("0x...", "ethereum", tier="pro")
    """

    # Phase 1: Core identity - always run, needed for scoring
    CORE_CHAINS: ClassVar[list[Any]] = [
        "token_price",
        "token_metadata",
        "token_security",
    ]

    # Phase 2: Deep analysis - parallel, all free
    DEEP_CHAINS: ClassVar[list[Any]] = [
        "holder_data",
        "volume_authenticity",
        "chain_indexer_patterns",
    ]

    # Phase 3: Premium - requires elite tier (pro gets CORE + DEEP only)
    PREMIUM_CHAINS: ClassVar[list[Any]] = [
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
    INTEL_CHAINS: ClassVar[list[Any]] = [
        "token_launches",
        "cross_chain_entity",
    ]

    # Phase 5: SENTINEL deep-forensic modules (direct, no DataBus hop).
    # Free-tier modules always run; premium modules are gated on the same
    # elite/admin tier gate as PREMIUM_CHAINS. See sentinel_phase.py.
    SENTINEL_FREE_MODULES: ClassVar[tuple[Any, ...]] = (
        "holder_analysis",
        "liquidity_verification",
        "dev_reputation",
        "metadata_fingerprint",
        "contract_authority",
        "proxy_detection",
    )

    # Strong references to in-flight background continuation tasks so they are
    # never garbage-collected mid-run (shared singleton instance).
    _background_tasks: ClassVar[set[asyncio.Task[Any]]] = set()

    # ── Result caching (score stability) ──────────────────────────────
    # Tokens scanned within the last hour are served from the Redis result
    # cache written by ``persist_result`` (key ``sentinelscan:{chain}:{addr}``,
    # TTL 3600). This makes repeated scans deterministic: the same token in
    # the same hour returns the same score instead of re-scoring with drifting
    # external data (the WETH 23→28→38→43 drift bug).
    CACHE_TTL_S = 3600

    async def _load_cached_result(self, address: str, chain: str) -> ScanResult | None:
        """Return a fresh cached scan result for the token, or ``None``.

        Best-effort: Redis outage / missing key / malformed payload all yield
        ``None`` so the fast path always falls through to a real scan.
        """
        try:
            from app.core.redis import get_redis_async

            r = get_redis_async()
            if r is None:
                return None
            raw = await r.get(f"sentinelscan:{chain}:{address}")
            if not raw:
                return None
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                return None
            cached = _from_legacy_dict(payload)
            if cached is None:
                return None
            cached.cached = True
            cached.background_status = "complete"
            cached.scan_duration_ms = 0.0
            # Attach the stability summary for this token (how many recent
            # scans agree on the risk level) — cheap Redis read, best-effort.
            cached.stability = await read_stability(chain, address)
            logger.info(
                "scan_cache_hit",
                token=address[:16],
                chain=chain,
                safety_score=cached.safety_score,
                risk_level=cached.risk_level,
            )
            return cached
        except (ValueError, TypeError, KeyError, OSError) as exc:
            logger.debug("scan_cache_read_failed", token=address[:16], error=str(exc)[:100])
            return None
        except Exception as exc:
            logger.debug("scan_cache_read_unexpected", token=address[:16], error=str(exc)[:100])
            return None

    async def scan(
        self,
        address: str,
        chain: str = "solana",
        tier: str = "free",
        admin_key: str | None = None,
    ) -> ScanResult:
        """Full token security scan.

        Fast path (hard-bounded to ``SCAN_HARD_TIMEOUT_S`` ≈ 25s total):
          1. Market context (best-effort, cached 5 min)
          2. Core identity (price, meta, security) — needed for baseline scoring
          3. Baseline score from core data

        Deep, premium, intel, SENTINEL, news, RAG, persistence and Telegram
        phases continue in a background task so HTTP clients (30-60s timeouts)
        are not blocked by ~110s scans. Progress + the final result are
        published to Redis under ``scanner:status:{chain}:{address}`` and
        polled via ``scan_status`` / the status endpoint.

        The returned ``ScanResult.background_status`` is:
          "complete" — every phase finished before the deadline
          "running"  — background phases still in flight (check status endpoint)

        Score stability: if this token was scanned within the last hour
        (Redis result cache written by ``persist_result``), the cached result
        is returned with ``cached=True`` instead of re-scoring with drifting
        external data.
        """
        # ── Cache fast path: serve the 1h cached result when present ──
        # Elite/admin (premium SENTINEL deep scans) always run fresh — the
        # cache stores the last completed result which may be a quick-tier
        # scan without premium modules.
        if (tier or "").lower() not in ("elite", "admin"):
            cached = await self._load_cached_result(address, chain)
            if cached is not None:
                return cached

        start = time.monotonic()
        params = {"address": address, "chain": chain}
        result = ScanResult(
            token_address=address,
            chain=chain,
            scanned_at=datetime.now(UTC).isoformat(),
            tier=tier,
        )
        result.background_status = "running"

        # ── Fast path: market context (cached 5 min, best-effort) ──
        # Market-aware scanning: fetch fear index + market mood + token
        # social velocity from RugBrain so scoring can adjust risk when the
        # market is fearful or recent exploits match this token's category.
        try:
            from app.domains.scanner.unified.market_context import fetch_market_context

            market_context = await asyncio.wait_for(
                fetch_market_context(address, chain),
                timeout=8,
            )
            if market_context is not None:
                result.market_context = market_context
                result.modules_run.append("market_context")
                if market_context.source != "unknown":
                    result.enrichment_sources.append(f"market_context:{market_context.source}")
        except Exception as exc:
            logger.debug("market_context_fetch_failed", error=str(exc)[:120])

        try:
            # ── Phase 1: Core (sequential - needed for scoring) ──
            core_results = await self._fetch_batch(self.CORE_CHAINS, params, admin_key, timeout=15)
            self._merge_core(result, core_results)
        except (ValueError, TypeError, KeyError, OSError) as e:
            logger.error(f"Scan core failed for {address}: {e}")
            result.risk_flags.append(f"SCAN_ERROR: {str(e)[:100]}")
        except Exception as e:
            logger.error(f"Scan core crashed for {address}: {e}")
            result.risk_flags.append(f"SCAN_ERROR: {str(e)[:100]}")

        # ── Baseline score (fast, from core data) — the background task
        #    recomputes the score once every module has finished ──
        try:
            scoring = compute_score(result)
            result.breakdown = scoring["breakdown"]
            result.evidence = scoring["evidence"]
        except Exception as exc:
            logger.debug("baseline_scoring_failed", error=str(exc)[:120])

        # ── Store initial status so clients can poll progress ──
        await _store_scan_status(
            chain,
            address,
            {
                "state": "running",
                "phase": "core",
                "started_at": result.scanned_at,
                "updated_at": datetime.now(UTC).isoformat(),
                "elapsed_ms": (time.monotonic() - start) * 1000,
                "result": _status_result_dict(result),
            },
        )

        # ── Background continuation: deep/intel/SENTINEL/news + persistence ──
        # Heavy phases run as a background task so the HTTP response returns
        # promptly. ``asyncio.shield`` keeps it alive if the 25s budget expires.
        bg_task = asyncio.create_task(self._run_background_phases(result, address, chain, tier, admin_key))
        self._track_background_task(bg_task)

        # ── 25s hard wrapper: wait for the background up to the remaining
        #    budget. If it does not finish, return the core+baseline result and
        #    let the background task continue (results land in Redis). ──
        elapsed = time.monotonic() - start
        remaining = max(1.0, SCAN_HARD_TIMEOUT_S - elapsed)
        try:
            await asyncio.wait_for(asyncio.shield(bg_task), timeout=remaining)
            result.background_status = "complete"
        except TimeoutError:
            logger.info(
                "scan_background_deferred",
                token=address[:16],
                chain=chain,
                elapsed_s=round(time.monotonic() - start, 1),
            )
            result.background_status = "running"

        result.scan_duration_ms = (time.monotonic() - start) * 1000
        return result

    def _track_background_task(self, task: asyncio.Task[Any]) -> None:
        """Keep a strong reference to a background scan task (avoids GC)."""
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _run_background_phases(
        self,
        result: ScanResult,
        address: str,
        chain: str,
        tier: str,
        admin_key: str | None,
    ) -> None:
        """Continue deep/intel/SENTINEL/news phases in the background.

        Runs after the fast path returns so HTTP clients are never blocked by
        slow external modules. Publishes progress + the final result to Redis
        (see ``scan_status``). Never raises — errors are captured into the
        result and the status record.
        """
        start = time.monotonic()
        params = {"address": address, "chain": chain}

        async def _status(phase: str, state: str = "running", error: str | None = None) -> None:
            payload: dict[str, Any] = {
                "state": state,
                "phase": phase,
                "started_at": result.scanned_at,
                "updated_at": datetime.now(UTC).isoformat(),
                "elapsed_ms": (time.monotonic() - start) * 1000,
                "result": _status_result_dict(result),
            }
            if error:
                payload["error"] = error
            await _store_scan_status(chain, address, payload)

        try:
            # ── Phase 2: Deep (parallel) ──
            await _status("deep")
            deep_results = await self._fetch_batch(self.DEEP_CHAINS, params, admin_key, timeout=30)
            self._merge_deep(result, deep_results)

            # ── Phase 2b: Clever wallet analysis (own-data engines, free) ──
            # Exchange-funded %, wallet freshness, holder overlap vs scam tokens.
            # Runs after deep because it reuses the top-holder list from holder_data.
            await self._run_wallet_analysis(result, address, chain)

            # ── Phase 3: Premium (tier-gated — elite/admin only) ──
            if tier in ("elite", "admin"):
                premium_results = await self._fetch_batch(self.PREMIUM_CHAINS, params, admin_key, timeout=30)
                self._merge_premium(result, premium_results)

            # ── Phase 4: Intel (free enrichment) ──
            await _status("intel")
            intel_results = await self._fetch_batch(self.INTEL_CHAINS, params, admin_key, timeout=20)
            self._merge_intel(result, intel_results)

            # ── Phase 4b: News-event-to-price correlation (free, own data) ──
            await self._run_event_correlation(result, address, chain)

            # ── Phase 5: SENTINEL deep-forensic modules (free + tier-gated) ──
            # Free tier always runs the core rug-detection modules; the
            # remaining deep-forensic modules unlock on elite/admin tier
            # (or via the paid x402 SENTINEL deep scan). Best-effort:
            # a failing module never fails the scan.
            await _status("sentinel")
            await self._run_sentinel_phase(result, address, chain, tier)

            # ── Score (recomputed with all modules) ──
            scoring = compute_score(result)
            result.breakdown = scoring["breakdown"]
            result.evidence = scoring["evidence"]

            # ── News sentiment (free enrichment, after scoring so the
            #    HIGH_MEDIA_ATTENTION flag survives compute_score) ──
            await self._run_news_sentiment(result, address, chain)

            # ── Ground News-style news card (free enrichment) ──
            await self._get_news_card(result, address, chain)

            # ── News-driven risk adjustment (post-scoring — news engines
            #    run after compute_score, so the bonus/penalty is applied
            #    in a second pass that keeps score/flags/breakdown in sync) ──
            try:
                from app.domains.scanner.unified.scoring import apply_news_scoring

                apply_news_scoring(result)
            except Exception as exc:
                logger.debug("news_scoring_failed", error=str(exc)[:120])

            # ── Market-aware risk adjustment (post-scoring — market context
            #    is fetched before the phases, then applied here so the
            #    score/flags/breakdown stay in sync) ──
            try:
                from app.domains.scanner.unified.scoring import apply_market_scoring

                apply_market_scoring(result)
            except Exception as exc:
                logger.debug("market_scoring_failed", error=str(exc)[:120])

            # ── Enrich with RAG context (similar patterns from previous scans) ──
            try:
                from app.rag.intelligence import RAGIntelligenceEnricher

                enricher = RAGIntelligenceEnricher()
                rag_context = await enricher.enrich_scan_result(result)
                result.rag_context = rag_context
            except Exception:
                logger.debug("RAG enrichment unavailable", exc_info=True)
                result.rag_context = None

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

            # ── Push Telegram alert (fire-and-forget — never block the scan) ──
            alert_task = self._fire_telegram_alert(result)
            try:
                # Bounded, shielded grace period: lets the alert complete while
                # the background phase is still winding down. On timeout the
                # alert continues independently — it never delays the HTTP
                # response past the 25s budget.
                if alert_task is not None:
                    await asyncio.wait_for(asyncio.shield(alert_task), timeout=5)
            except TimeoutError:
                pass

            # ── Auto-label high-risk deployer wallets (fire-and-forget) ──
            try:
                asyncio.create_task(auto_label_deployer(result))
            except Exception as exc:
                logger.debug("deployer_label_schedule_failed", error=str(exc)[:80])

            result.background_status = "complete"
            await _status("done", state="complete")
            logger.info(
                "scan_background_complete",
                token=address[:16],
                chain=chain,
                safety_score=result.safety_score,
                risk_flags=len(result.risk_flags),
                elapsed_s=round(time.monotonic() - start, 1),
            )

        except (ValueError, TypeError, KeyError, OSError) as e:
            logger.debug("unknown_error", exc_info=True)
            logger.error(f"Scan background failed for {address}: {e}")
            result.risk_flags.append(f"SCAN_ERROR: {str(e)[:100]}")
            await _status("error", state="error", error=str(e)[:200])
        except Exception as e:
            logger.debug("scan_unexpected_error", exc_info=True)
            logger.error(f"Scan background crashed for {address}: {e}")
            result.risk_flags.append(f"SCAN_ERROR: {str(e)[:100]}")
            await _status("error", state="error", error=str(e)[:200])

    def _fire_telegram_alert(self, result: ScanResult) -> asyncio.Task[Any] | None:
        """Schedule the Telegram scanner alert without blocking the scan.

        Fire-and-forget: returns the background task (or ``None`` when the
        alert is gated off); the caller awaits it with a bounded shield.
        """
        try:
            from app.domains.telegram.alert_hooks import send_scanner_alert, should_send_scanner_alert

            if not should_send_scanner_alert(result.risk_level, list(result.risk_flags)):
                return None

            async def _send() -> None:
                try:
                    await send_scanner_alert(
                        token_address=result.token_address,
                        chain=result.chain,
                        risk_score=100 - float(result.safety_score),
                        threats=list(result.risk_flags),
                        token_name=result.name or "Unknown",
                        token_symbol=result.symbol or "???",
                    )
                except Exception as exc:
                    logger.debug("scanner_alert_failed", error=str(exc)[:80])

            return asyncio.create_task(_send())
        except Exception as exc:
            logger.debug("scanner_alert_schedule_failed", error=str(exc)[:80])
            return None

    # ── Tiered SENTINEL scan entry points (quick vs deep) ──────────────

    async def scan_token(
        self,
        address: str,
        chain: str = "solana",
        tier: str = "quick",
        admin_key: str | None = None,
    ) -> ScanResult:
        """Tiered SENTINEL token scan.

        ``tier="quick"`` (default) runs only the free SENTINEL modules —
        fast baseline safety scan, identical to the existing ``scan`` free
        path.  ``tier="deep"`` runs every SENTINEL module (free + premium
        deep-forensic) and populates ``ScanResult.deep_profile`` with the
        typed profiles (WalletBreakdown / BundleProfile / DeployerProfile).

        Legacy tier names are accepted: free/pro behave like quick,
        elite/admin behave like deep.
        """
        phase_tier, scan_tier = _resolve_scan_tier(tier)
        result = await self.scan(address=address, chain=chain, tier=phase_tier, admin_key=admin_key)
        result.scan_tier = scan_tier
        if scan_tier == "deep":
            try:
                result.deep_profile = collect_deep_profile(result.sentinel)
            except Exception as exc:
                logger.warning("deep_profile_collect_failed", error=str(exc)[:160])
        return result

    async def scan_token_deep(
        self,
        address: str,
        chain: str = "solana",
        admin_key: str | None = None,
    ) -> ScanResult:
        """Full SENTINEL deep scan — same signature family as ``scan_token``.

        Runs every SENTINEL module (free + premium deep-forensic tier) and
        returns the same ``ScanResult`` as a quick scan but with
        ``deep_profile`` populated.
        """
        return await self.scan_token(address=address, chain=chain, tier="deep", admin_key=admin_key)

    async def _fetch_batch(
        self,
        chains: list[str],
        params: dict[str, Any],
        admin_key: str | None,
        timeout: float = 30,
    ) -> dict[str, Any]:
        """Parallel DataBus fetch for multiple chains."""
        from app.databus import databus

        async def _fetch_one(chain_name: str) -> tuple[Any, ...]:
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
            except Exception as e:
                logger.debug("_fetch_one_unexpected", exc_info=True)
                logger.warning(f"DataBus chain '{chain_name}' raised unexpected error: {e}")
                return chain_name, None

        tasks = [_fetch_one(c) for c in chains]
        gathered = await asyncio.gather(*tasks, return_exceptions=True)

        results = {}
        for item in gathered:
            if isinstance(item, Exception):
                continue
            name, data = item
            if data is None:
                continue
            # Defense: treat any dict with an "error" key as a miss.
            # DataBus returns {"error": "unknown_data_type"} for chains
            # that are not registered, which must not be treated as valid
            # enrichment data (avoid false penalties like LP_LOCK_LOW -20).
            if isinstance(data, dict) and "error" in data:
                logger.debug(
                    "databus_chain_error",
                    chain=name,
                    error=data.get("error", "unknown"),
                    message=data.get("message", ""),
                )
                continue
            # Unwrap the DataBus envelope.  ``databus.fetch`` returns
            # {"data": {...}, "source": ..., "tier": ..., "is_local": ...}
            # but the merge methods below read raw payload keys
            # (price_usd, name, symbol, findings, ...) directly, so we
            # surface the payload as the chain result.  The envelope-level
            # source is carried into the payload (when absent) so
            # enrichment_sources still records where data came from.
            if isinstance(data, dict) and isinstance(data.get("data"), dict):
                payload = data["data"]
                if isinstance(payload, dict) and "source" not in payload and data.get("source"):
                    payload = dict(payload)
                    payload["source"] = data["source"]
                data = payload
            results[name] = data
        return results

    # ── MERGE METHODS ──

    def _merge_core(self, r: ScanResult, data: dict[str, Any]) -> None:
        """Extract core identity from price, metadata, and security checks."""
        r.modules_run.extend(data.keys())

        price_data = data.get("token_price", {})
        if isinstance(price_data, dict):
            r.price_usd = float(price_data.get("price_usd", 0) or 0)
            r.market_cap_usd = float(price_data.get("market_cap_usd", 0) or 0)
            # DataBus price payload uses ``volume_24h_usd``; accept the
            # legacy ``volume_24h`` alias too so a renamed provider key
            # never zeroes out the 24h volume.
            r.volume_24h_usd = float(price_data.get("volume_24h_usd", price_data.get("volume_24h", 0)) or 0)
            r.liquidity_usd = float(price_data.get("liquidity_usd", 0) or 0)
            if price_data.get("source"):
                r.enrichment_sources.append(f"price:{price_data['source']}")

        meta = data.get("token_metadata", {})
        if isinstance(meta, dict):
            # token_metadata providers return raw external shapes (DexScreener
            # ``{"pairs": [...]}`` or Alchemy JSON-RPC ``{"result": {...}}``)
            # as well as the normalized ``{name, symbol, age_hours}`` form.
            # Normalize all three defensively — a raw shape must not wipe out
            # the fields populated by the price oracle.
            meta_name, meta_symbol = _extract_meta_identity(meta)
            r.name = str(meta_name or r.name or "")
            r.symbol = str(meta_symbol or r.symbol or "")
            r.age_hours = float(meta.get("age_hours", 0) or 0)

        # Fallback: when metadata is missing/empty, the price payload already
        # carries name/symbol (the F2 price oracle enriches its payload with
        # them).  Keep whatever non-empty value exists.
        if not r.name and isinstance(price_data, dict):
            r.name = str(price_data.get("name", "") or "")
        if not r.symbol and isinstance(price_data, dict):
            r.symbol = str(price_data.get("symbol", "") or "")

        security = data.get("token_security", {})
        if isinstance(security, dict):
            r.security = security
            checks = security.get("checks", {})
            r.modules_run.append(f"security_checks:{len(checks)}")
            r.enrichment_sources.append(f"security:{security.get('source', 'goplus')}")

    def _merge_deep(self, r: ScanResult, data: dict[str, Any]) -> None:
        """Merge deep analysis results."""
        r.modules_run.extend(data.keys())

        def _ok(key: str) -> Any:
            """Return the value only if it is not an error dict from DataBus."""
            v = data.get(key)
            if isinstance(v, dict) and "error" in v:
                return None
            return v

        r.holders = _ok("holder_data")
        r.holder_health = _ok("holder_health")
        r.liquidity = _ok("liquidity_risk")
        r.dev_reputation = _ok("dev_reputation")
        r.rug_patterns = _ok("rug_patterns")
        r.volume_auth = _ok("volume_authenticity")
        r.chain_indexer_patterns = _ok("chain_indexer_patterns")

    def _merge_premium(self, r: ScanResult, data: dict[str, Any]) -> None:
        """Merge premium-tier results."""
        r.modules_run.extend(data.keys())
        r.premium = {k: v for k, v in data.items() if k in self.PREMIUM_CHAINS and v is not None}

    def _merge_intel(self, r: ScanResult, data: dict[str, Any]) -> None:
        """Merge intelligence enrichment results."""
        r.modules_run.extend(data.keys())

        def _ok(key: str) -> Any:
            """Return the value only if it is not an error dict from DataBus."""
            v = data.get(key)
            if isinstance(v, dict) and "error" in v:
                return None
            return v

        r.smart_money = _ok("smart_money")
        r.whale_alerts = _ok("whale_alerts")
        r.cross_chain = _ok("cross_chain_entity")
        r.launches = _ok("token_launches")

    # ── Clever wallet analysis (Phase 2b) ──

    async def _run_wallet_analysis(self, r: ScanResult, address: str, chain: str) -> None:
        """Run exchange-funding, freshness, and scam-overlap analysis.

        Best-effort: each engine degrades gracefully when own ClickHouse data
        is unavailable, so a failed sub-engine never fails the scan.
        """
        try:
            from app.domains.scanner.engines.funding_analysis import (
                analyze_funding_sources,
                analyze_scam_overlap,
                compute_wallet_freshness,
            )

            holders: list[str] = []
            holder_data = r.holders if isinstance(r.holders, dict) else None
            if holder_data:
                top = holder_data.get("top_holders") or []
                if isinstance(top, list):
                    holders = [str(h.get("address", "")) for h in top if isinstance(h, dict) and h.get("address")]
                elif isinstance(top, dict):
                    # Some providers return {address: pct}
                    holders = [str(a) for a in top if a]

            # ── Metric 1: Exchange-funded wallet % ──
            funding_task = analyze_funding_sources(address, chain, holders=holders or None)

            # ── Metric 2: Freshness of the top few holders (bounded) ──
            freshness_holders = holders[:10]
            freshness_tasks = [compute_wallet_freshness(h, chain) for h in freshness_holders]
            freshness_task = _gather_freshness(freshness_tasks)

            # ── Metric 3: Holder overlap vs known scam tokens ──
            overlap_task = analyze_scam_overlap(address, chain)

            funding: Any
            freshness: Any
            overlap: Any
            funding, freshness, overlap = await asyncio.gather(
                funding_task,
                freshness_task,
                overlap_task,
                return_exceptions=True,
            )

            if not isinstance(funding, BaseException) and isinstance(funding, dict):
                r.funding_analysis = funding
            if not isinstance(freshness, BaseException) and isinstance(freshness, dict):
                r.wallet_freshness = freshness
            if not isinstance(overlap, BaseException) and isinstance(overlap, dict):
                r.holder_overlap = overlap

            r.modules_run.extend(["funding_analysis", "wallet_freshness", "holder_overlap"])
            r.enrichment_sources.append("wallet_analysis:own")
        except Exception as exc:
            logger.debug("wallet_analysis_failed", error=str(exc)[:120])

    async def _run_news_sentiment(self, r: ScanResult, address: str, chain: str) -> None:
        """Analyze news sentiment for the scanned token (best-effort).

        Queries RugBrain RAG ``news_v2`` for articles mentioning the token,
        computes BULLISH/BEARISH/NEUTRAL/NO_DATA sentiment plus a 0-100 media
        attention (trending) score, and stores the result on
        ``ScanResult.news_sentiment``. Adds the ``HIGH_MEDIA_ATTENTION``
        green flag when trending_score > 70.

        Runs AFTER ``compute_score`` so the appended flag is not overwritten
        by scoring's ``green_flags`` assignment. Time-boxed so a slow RAG
        backend can never stall the scan. Never raises — news sentiment is
        best-effort enrichment, not a scan blocker.
        """
        try:
            from app.domains.scanner.engines.news_sentiment import analyze_token_news_sentiment

            sentiment = await asyncio.wait_for(
                analyze_token_news_sentiment(
                    token_symbol=r.symbol or "",
                    token_name=r.name or "",
                    chain=chain,
                ),
                timeout=10,
            )
            if not isinstance(sentiment, dict):
                return
            r.news_sentiment = sentiment
            r.modules_run.append("news_sentiment")
            if sentiment.get("result") == "scanned" and int(sentiment.get("article_count", 0) or 0) > 0:
                r.enrichment_sources.append("news_sentiment:rag")
            if int(sentiment.get("trending_score", 0) or 0) > 70:
                flag = "HIGH_MEDIA_ATTENTION"
                if flag not in r.green_flags:
                    r.green_flags.append(flag)
        except Exception as exc:
            logger.debug("news_sentiment_failed", error=str(exc)[:120])

    async def _get_news_card(self, r: ScanResult, address: str, chain: str) -> None:
        """Build the Ground News-style news card for the scanned token.

        Queries RugBrain RAG ``news_v2`` and rates each article across
        factuality / source reputation / market impact / sentiment axes,
        then stores the card on ``ScanResult.news_card``. On-chain
        corroboration is passed through from ``event_correlation`` when the
        scanner has its own on-chain evidence of the news event.

        Runs AFTER ``_run_news_sentiment`` so both news enrichment fields
        are available to Telegram/report rendering. Time-boxed so a slow
        RAG backend can never stall the scan. Never raises — the news card
        is best-effort enrichment, not a scan blocker.
        """
        try:
            from app.domains.scanner.engines.news_sentiment import get_token_news_card

            on_chain: bool | None = None
            if isinstance(r.event_correlation, dict) and r.event_correlation.get("events"):
                on_chain = True

            card = await asyncio.wait_for(
                get_token_news_card(
                    token_symbol=r.symbol or "",
                    token_name=r.name or "",
                    chain=chain,
                    limit=5,
                    on_chain_evidence=on_chain,
                ),
                timeout=10,
            )
            if not isinstance(card, dict):
                return
            r.news_card = card
            r.modules_run.append("news_card")
            if card.get("result") == "scanned" and card.get("articles"):
                r.enrichment_sources.append("news_card:rag")
        except Exception as exc:
            logger.debug("news_card_failed", error=str(exc)[:120])

    async def _run_event_correlation(self, r: ScanResult, address: str, chain: str) -> None:
        """Correlate recent news events with price moves for the scanned token.

        Best-effort: runs AFTER intel enrichment but BEFORE scoring so the
        result is available to the report. Uses own ClickHouse dex_events
        (price at news timestamp) plus the news catalog; degrades gracefully
        to empty events when news/price data is unavailable.
        """
        try:
            from app.domains.scanner.engines.event_correlation import correlate_news_with_price

            correlation = await correlate_news_with_price(address, chain, lookback_hours=24)
            if not isinstance(correlation, dict):
                return
            events = correlation.get("events") or []
            if events:
                r.event_correlation = correlation
                r.modules_run.append("event_correlation")
                r.enrichment_sources.append("event_correlation:own")
        except Exception as exc:
            logger.debug("event_correlation_failed", error=str(exc)[:120])

    async def _run_sentinel_phase(self, r: ScanResult, address: str, chain: str, tier: str) -> None:
        """Run Phase 5 — SENTINEL deep-forensic modules (free + tier-gated).

        Free-tier modules (holder HHI, LP lock, dev reputation, metadata
        fingerprint, contract authority, proxy detection) always populate
        ``r.sentinel["free"]``. Premium modules populate
        ``r.sentinel["premium"]`` only when the tier is elite/admin; for
        other tiers a ``premium_preview`` list advertises the locked modules
        with an unlock prompt (paid path: x402 SENTINEL deep scan).

        Best-effort: a broken module is recorded in ``sentinel.errors`` and
        never fails the scan. Time-boxed so external API latency cannot
        stall the overall scan.
        """
        try:
            from app.domains.scanner.unified.sentinel_phase import (
                extract_deployer_from_security,
                run_sentinel_phase,
            )

            deployer = extract_deployer_from_security(r.security)
            sentinel = await asyncio.wait_for(
                run_sentinel_phase(address=address, chain=chain, tier=tier, deployer=deployer),
                timeout=30,
            )
            if not isinstance(sentinel, dict):
                return
            r.sentinel = sentinel

            # ── Wire SENTINEL holder analysis into the unified holder phase ──
            # The SENTINEL free-tier holder_analysis module computes HHI /
            # concentration directly from DexScreener/Birdeye/Solscan. When
            # the DataBus holder_health chain produced no HHI, promote the
            # SENTINEL HHI so the unified holder phase (scoring + evidence)
            # sees the deep-forensic concentration signal.
            sentinel_free = sentinel.get("free") or {}
            holder_analysis = sentinel_free.get("holder_analysis")
            if isinstance(holder_analysis, dict) and holder_analysis.get("hhi") is not None:
                hhi = holder_analysis.get("hhi")
                if not isinstance(r.holder_health, dict) or r.holder_health.get("hhi") is None:
                    merged = dict(r.holder_health) if isinstance(r.holder_health, dict) else {}
                    merged["hhi"] = hhi
                    merged.setdefault("gini", None)
                    merged["hhi_source"] = "sentinel_holder_analysis"
                    r.holder_health = merged

            # Surface free-tier module names in modules_run + enrichment so
            # the SENTINEL phase is visible in metrics and the API report.
            free_ran = [k for k in self.SENTINEL_FREE_MODULES if k in (sentinel.get("modules_run") or [])]
            if free_ran:
                r.modules_run.extend(free_ran)
                r.enrichment_sources.append("sentinel:free")
            if sentinel.get("premium_unlocked"):
                r.enrichment_sources.append("sentinel:premium")
        except Exception as exc:
            logger.debug("sentinel_phase_failed", error=str(exc)[:160])
            r.sentinel = {
                "free": {},
                "premium": None,
                "premium_unlocked": False,
                "premium_modules": list(self.SENTINEL_FREE_MODULES),
                "premium_preview": [],
                "errors": {"_phase": str(exc)[:200]},
                "modules_run": [],
                "deployer_resolved": False,
            }
