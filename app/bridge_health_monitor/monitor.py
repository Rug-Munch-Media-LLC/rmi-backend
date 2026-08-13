"""
Core BridgeHealthMonitor class — TVL fetching, security scoring, exploit detection.

Provides the main scan() and alert_if_exploit() async methods that orchestrate
the full bridge health analysis pipeline.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import aiohttp

from app.bridge_health_monitor.registry import BRIDGE_REGISTRY
from app.bridge_health_monitor.types import (
    BridgeHealthReport,
    BridgeSecurityScore,
    BridgeTVLSnapshot,
    ExploitSignal,
    RiskTier,
    TrustModel,
)
from app.core.logging import get_logger

logger = get_logger(__name__)


class BridgeHealthMonitor:
    """Cross-chain bridge health & exploit monitor."""

    def __init__(self, defillama_api: str = "https://coins.llama.fi") -> None:
        self.defillama_api = defillama_api
        self.session: aiohttp.ClientSession | None = None
        self._cache: dict[str, Any] = {}
        self._cache_ttl: int = 300

        self.TVL_DROP_24H_WARN_PCT = -15.0
        self.TVL_DROP_24H_CRIT_PCT = -40.0
        self.TVL_DROP_7D_DANGER_PCT = -50.0

    async def _get_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15),
                headers={"User-Agent": "RMI-BridgeMonitor/1.0"},
            )
        return self.session

    async def _fetch_tvl(self, slug: str) -> dict[str, Any] | None:
        cache_key = f"tvl_{slug}"
        if cache_key in self._cache:
            cached, ts = self._cache[cache_key]
            if time.time() - ts < self._cache_ttl:
                return cached
        try:
            session = await self._get_session()
            url = f"{self.defillama_api}/protocol/{slug}"
            async with session.get(url) as resp:
                if resp.status != 200:
                    logger.warning(f"DeFiLlama TVL fetch failed for {slug}: {resp.status}")
                    return None
                data = await resp.json()
                self._cache[cache_key] = (data, time.time())
                return data
        except (TimeoutError, aiohttp.ClientError, json.JSONDecodeError) as e:
            logger.error(f"Error fetching TVL for {slug}: {e}")
            return None

    async def _fetch_current_tvl(self, slug: str) -> float:
        data = await self._fetch_tvl(slug)
        if not data:
            return 0.0
        current_chart = data.get("chainTvls", data.get("tvl", []))
        if isinstance(current_chart, dict):
            total = 0.0
            for chain_data in current_chart.values():
                if chain_data and isinstance(chain_data, list) and len(chain_data) > 0:
                    total += chain_data[-1].get("totalLiquidityUSD", 0)
            return total
        elif isinstance(current_chart, list) and len(current_chart) > 0:
            return current_chart[-1].get("totalLiquidityUSD", 0)
        return 0.0

    async def _fetch_tvl_change(self, slug: str, hours: int = 24) -> float:
        data = await self._fetch_tvl(slug)
        if not data:
            return 0.0
        tvl_data = data.get("chainTvls", data.get("tvl", []))
        if isinstance(tvl_data, dict):
            current_total = 0.0
            historical_total = 0.0
            for chain_data in tvl_data.values():
                if isinstance(chain_data, list) and len(chain_data) >= 2:
                    current_total += chain_data[-1].get("totalLiquidityUSD", 0)
                    idx = min(hours // 24, len(chain_data) - 1)
                    historical_total += chain_data[-1 - idx].get("totalLiquidityUSD", 0)
            if historical_total > 0 and current_total > 0:
                return ((current_total - historical_total) / historical_total) * 100
            return 0.0
        elif isinstance(tvl_data, list) and len(tvl_data) > 0:
            current = tvl_data[-1].get("totalLiquidityUSD", 0)
            idx = min(hours // 24, len(tvl_data) - 1)
            historical_val = tvl_data[-1 - idx].get("totalLiquidityUSD", current)
            if historical_val > 0 and current > 0:
                return ((current - historical_val) / historical_val) * 100
        return 0.0

    async def _compute_security_score(self, bridge_key: str, bridge: dict[str, Any]) -> BridgeSecurityScore:
        vulnerabilities: list[str] = []
        strengths: list[str] = []

        tvl = await self._fetch_current_tvl(bridge.get("defillama_slug", ""))
        if tvl >= 1_000_000_000:
            tvl_score = 20
            strengths.append("High TVL (>$1B) - well-capitalized and battle-tested")
        elif tvl >= 100_000_000:
            tvl_score = 15
        elif tvl >= 10_000_000:
            tvl_score = 10
        elif tvl > 0:
            tvl_score = 5
        else:
            tvl_score = 0

        validator_count = bridge.get("validator_count", 0)
        trust_model = bridge.get("trust_model", TrustModel.LIQUIDITY_NETWORK)

        if trust_model == TrustModel.EXTERNAL_VALIDATORS:
            if validator_count >= 50:
                dec_score = 25
                strengths.append(f"Highly decentralized ({validator_count} validators)")
            elif validator_count >= 20:
                dec_score = 20
                strengths.append(f"Moderately decentralized ({validator_count} validators)")
            elif validator_count > 0:
                dec_score = 15
            else:
                dec_score = 10
        elif trust_model == TrustModel.OPTIMISTIC:
            dec_score = 20
            strengths.append("Optimistic trust model - no active validator set required")
        elif trust_model == TrustModel.INTENT_BASED:
            dec_score = 18
            strengths.append("Intent-based architecture - minimal trust assumptions")
        elif trust_model == TrustModel.LIQUIDITY_NETWORK:
            dec_score = 12
        else:
            dec_score = 15

        audit_days = bridge.get("audit_recency_days", 365)
        if audit_days <= 60:
            audit_score = 20
            strengths.append("Recent audit (<60 days)")
        elif audit_days <= 120:
            audit_score = 15
        elif audit_days <= 180:
            audit_score = 10
        else:
            audit_score = 5
            vulnerabilities.append(f"Audit is {audit_days} days old - recommend re-audit")

        exploit_loss = bridge.get("total_exploit_loss_usd", 0)
        if exploit_loss == 0:
            exploit_score = 20
            strengths.append("No history of major exploits")
        elif exploit_loss < 10_000_000:
            exploit_score = 10
            vulnerabilities.append(f"Past exploit(s) - ${exploit_loss:,} total losses")
        elif exploit_loss < 100_000_000:
            exploit_score = 5
            vulnerabilities.append(f"Significant past exploit(s) - ${exploit_loss:,} total losses")
        else:
            exploit_score = 0
            vulnerabilities.append(f"Major past exploit(s) - ${exploit_loss:,} total losses")

        has_upgrade = bridge.get("has_upgradeability", True)
        has_pause = bridge.get("has_pause", False)
        if not has_upgrade:
            upgrade_score = 15
            strengths.append("Non-upgradeable - immutable contracts")
        elif has_pause:
            upgrade_score = 10
            vulnerabilities.append(
                "Upgradeable with pause - admin can modify contracts, but pause provides emergency response"
            )
        else:
            upgrade_score = 5
            vulnerabilities.append(
                "Upgradeable without pause - admin can modify contracts with no emergency stop mechanism"
            )

        total_score = tvl_score + dec_score + audit_score + exploit_score + upgrade_score

        if total_score >= 85:
            risk_tier = RiskTier.SAFE
        elif total_score >= 65:
            risk_tier = RiskTier.WATCH
        elif total_score >= 45:
            risk_tier = RiskTier.DANGER
        else:
            risk_tier = RiskTier.CRITICAL

        return BridgeSecurityScore(
            bridge_key=bridge_key,
            bridge_name=bridge.get("name", bridge_key),
            overall_score=total_score,
            tvl_depth_score=tvl_score,
            decentralization_score=dec_score,
            audit_score=audit_score,
            exploit_history_score=exploit_score,
            upgrade_risk_score=upgrade_score,
            risk_tier=risk_tier,
            vulnerabilities=vulnerabilities,
            strengths=strengths,
        )

    async def _detect_exploit_signals(
        self, bridge_key: str, bridge: dict[str, Any], tvl_snapshot: BridgeTVLSnapshot | None
    ) -> list[ExploitSignal]:
        signals: list[ExploitSignal] = []
        if tvl_snapshot is None:
            return signals

        if tvl_snapshot.tvl_change_24h_pct <= self.TVL_DROP_24H_CRIT_PCT:
            signals.append(
                ExploitSignal(
                    bridge_key=bridge_key,
                    bridge_name=bridge.get("name", bridge_key),
                    signal_type="tvl_drop",
                    severity="critical",
                    description=(
                        f"Critical TVL drop of {tvl_snapshot.tvl_change_24h_pct:.1f}% in 24h "
                        f"(${tvl_snapshot.tvl_usd:,.0f} remaining). Possible active exploit."
                    ),
                    detected_value=tvl_snapshot.tvl_change_24h_pct,
                    threshold_value=self.TVL_DROP_24H_CRIT_PCT,
                )
            )
        elif tvl_snapshot.tvl_change_24h_pct <= self.TVL_DROP_24H_WARN_PCT:
            signals.append(
                ExploitSignal(
                    bridge_key=bridge_key,
                    bridge_name=bridge.get("name", bridge_key),
                    signal_type="tvl_drop",
                    severity="high",
                    description=(
                        f"Significant TVL drop of {tvl_snapshot.tvl_change_24h_pct:.1f}% in 24h "
                        f"(${tvl_snapshot.tvl_usd:,.0f} remaining). Requires investigation."
                    ),
                    detected_value=tvl_snapshot.tvl_change_24h_pct,
                    threshold_value=self.TVL_DROP_24H_WARN_PCT,
                )
            )

        if tvl_snapshot.tvl_change_7d_pct <= self.TVL_DROP_7D_DANGER_PCT:
            signals.append(
                ExploitSignal(
                    bridge_key=bridge_key,
                    bridge_name=bridge.get("name", bridge_key),
                    signal_type="tvl_drop",
                    severity="high",
                    description=(
                        f"7-day TVL decline of {tvl_snapshot.tvl_change_7d_pct:.1f}%. "
                        f"Sustained capital outflow - protocol health concern."
                    ),
                    detected_value=tvl_snapshot.tvl_change_7d_pct,
                    threshold_value=self.TVL_DROP_7D_DANGER_PCT,
                )
            )

        return signals

    async def _compute_contagion_risk(
        self, scores: dict[str, BridgeSecurityScore], signals: list[ExploitSignal]
    ) -> list[str]:
        contagion: list[str] = []
        triggered_bridges = {s.bridge_key for s in signals if s.severity in ("high", "critical")}
        if not triggered_bridges:
            return contagion

        for triggered_key in triggered_bridges:
            triggered_score = scores.get(triggered_key)
            if not triggered_score:
                continue
            for other_key, other_score in scores.items():
                if other_key in triggered_bridges or other_key == triggered_key:
                    continue
                score_diff = abs(triggered_score.overall_score - other_score.overall_score)
                if score_diff <= 10:
                    contagion.append(
                        f"{other_score.bridge_name} (score {other_score.overall_score}) "
                        f"- shares similar security profile with compromised "
                        f"{triggered_score.bridge_name} (diff: {score_diff:.0f} pts)"
                    )
        return list(set(contagion))

    async def scan(self, bridge_filter: str | None = None) -> BridgeHealthReport:
        report = BridgeHealthReport()
        bridges_to_scan = (
            {bridge_filter: BRIDGE_REGISTRY[bridge_filter]}
            if bridge_filter and bridge_filter in BRIDGE_REGISTRY
            else BRIDGE_REGISTRY
        )

        # Phase 1: Fetch TVL snapshots
        tvl_tasks = {
            key: asyncio.create_task(self._fetch_current_tvl(bridge.get("defillama_slug", "")))
            for key, bridge in bridges_to_scan.items()
        }
        tvl_24h_tasks = {
            key: asyncio.create_task(self._fetch_tvl_change(bridge.get("defillama_slug", ""), 24))
            for key, bridge in bridges_to_scan.items()
        }
        tvl_7d_tasks = {
            key: asyncio.create_task(self._fetch_tvl_change(bridge.get("defillama_slug", ""), 168))
            for key, bridge in bridges_to_scan.items()
        }
        tvl_30d_tasks = {
            key: asyncio.create_task(self._fetch_tvl_change(bridge.get("defillama_slug", ""), 720))
            for key, bridge in bridges_to_scan.items()
        }

        tvl_results = {}
        for key in bridges_to_scan:
            try:
                tvl_results[key] = {
                    "current": await tvl_tasks[key],
                    "24h": await tvl_24h_tasks[key],
                    "7d": await tvl_7d_tasks[key],
                    "30d": await tvl_30d_tasks[key],
                }
            except Exception as e:
                logger.error(f"TVL fetch error for {key}: {e}")
                tvl_results[key] = {"current": 0.0, "24h": 0.0, "7d": 0.0, "30d": 0.0}

        # Phase 2: Build TVL snapshots
        for key, bridge in bridges_to_scan.items():
            tvl_data = tvl_results.get(key, {})
            snapshot = BridgeTVLSnapshot(
                bridge_key=key,
                bridge_name=bridge.get("name", key),
                tvl_usd=tvl_data.get("current", 0.0),
                tvl_change_24h_pct=tvl_data.get("24h", 0.0),
                tvl_change_7d_pct=tvl_data.get("7d", 0.0),
                tvl_change_30d_pct=tvl_data.get("30d", 0.0),
            )
            report.bridge_snapshots[key] = snapshot
            report.total_tvl_usd += snapshot.tvl_usd

        report.total_bridges = len(bridges_to_scan)

        # Phase 3: Compute security scores
        score_tasks = {
            key: asyncio.create_task(self._compute_security_score(key, bridge))
            for key, bridge in bridges_to_scan.items()
        }
        for key in bridges_to_scan:
            try:
                report.security_scores[key] = await score_tasks[key]
            except Exception as e:
                logger.error(f"Score computation error for {key}: {e}")

        # Phase 4: Detect exploit signals
        for key, bridge in bridges_to_scan.items():
            tvl_snapshot: BridgeTVLSnapshot | None = report.bridge_snapshots.get(key)
            signals = await self._detect_exploit_signals(key, bridge, tvl_snapshot)
            report.exploit_signals.extend(signals)

        # Phase 5: Contagion risk
        report.contagion_risk = await self._compute_contagion_risk(report.security_scores, report.exploit_signals)

        # Phase 6: Tally risk tiers
        for score in report.security_scores.values():
            if score.risk_tier == RiskTier.SAFE:
                report.bridges_healthy += 1
            elif score.risk_tier == RiskTier.WATCH:
                report.bridges_watch += 1
            elif score.risk_tier == RiskTier.DANGER:
                report.bridges_danger += 1
            elif score.risk_tier == RiskTier.CRITICAL:
                report.bridges_critical += 1

        # Aggregate TVL change
        if report.bridge_snapshots:
            total_old_tvl = sum(
                s.tvl_usd / (1 + s.tvl_change_24h_pct / 100) if s.tvl_change_24h_pct != -100 else 0
                for s in report.bridge_snapshots.values()
                if s.tvl_usd > 0
            )
            if total_old_tvl > 0:
                report.tvl_change_24h_pct = (report.total_tvl_usd - total_old_tvl) / total_old_tvl * 100

        return report

    async def alert_if_exploit(self, bridge_filter: str | None = None) -> BridgeHealthReport | None:
        report = await self.scan(bridge_filter)
        if report.exploit_signals or report.bridges_danger > 0 or report.bridges_critical > 0:
            return report
        return None

    async def close(self) -> None:
        if self.session and not self.session.closed:
            await self.session.close()


__all__ = ["BridgeHealthMonitor"]
