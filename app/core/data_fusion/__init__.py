"""
Data Fusion Engine — Intelligently Combine Multiple Data Sources

Design principle: No single data source is perfectly reliable. By fusing
results from independent providers — weighting by historical accuracy,
removing outliers, and scoring confidence — we produce intelligence that
is more trustworthy and actionable than any individual API.

Usage:
    from app.core.data_fusion import DataFusion

    fusion = DataFusion()
    risk = await fusion.fuse_token_risk(...)
    price = await fusion.fuse_price_data(...)
"""

from __future__ import annotations

import datetime
import statistics
import time
from typing import Any

from app.core.data_fusion.config import SOURCE_WEIGHTS
from app.core.data_fusion.helpers import (
    _deduplicate_findings,
    _empty_result,
    _extract_list,
    _extract_price,
    _get_freshness,
    _merge_holder_lists,
    _one_liner,
    _remove_outliers,
    _risk_level,
    _top_findings,
)
from app.core.logging import get_logger

logger = get_logger("data_fusion")


def envelope(
    success: bool,
    data: Any = None,
    confidence: float = 0,
    source: str = "",
    error: str = "",
) -> dict:
    """Standard response envelope for all fusion results."""
    return {
        "success": success,
        "data": data,
        "confidence": min(1.0, max(0.0, confidence)),
        "source": source or "data_fusion",
        "error": error,
        "fused_at": datetime.datetime.now(datetime.UTC).isoformat(),
    }


class WeightedVote:
    """A vote from one data source."""

    value: Any
    source_key: str
    weight: float
    freshness: float
    timestamp: float

    def __init__(self, value: Any, source_key: str, weight: float | None = None, freshness: float = 1.0) -> None:
        self.value = value
        self.source_key = source_key
        self.weight = weight if weight is not None else SOURCE_WEIGHTS.get(source_key, 0.3)
        self.freshness = freshness
        self.timestamp = time.time()


class DataFusion:
    """Central fusion engine — aggregates multi-source data into confident results."""

    def __init__(self) -> None:
        self._stats: dict[str, int] = {"calls": 0, "sources_fused": 0, "errors": 0}

    # ── Token Risk Fusion ──
    async def fuse_token_risk(
        self,
        goplus_result: dict | None = None,
        dexscreener_result: dict | None = None,
        honeypot_result: dict | None = None,
        sentinel_result: dict | None = None,
        additional: list[dict] | None = None,
    ) -> dict:
        self._stats["calls"] += 1
        votes: list[WeightedVote] = []
        findings: list[dict] = []

        for source_key, result in [
            ("goplus", goplus_result),
            ("dexscreener", dexscreener_result),
            ("honeypot_is", honeypot_result),
        ]:
            if not result:
                continue
            self._stats["sources_fused"] += 1
            now = time.time()
            freshness = _get_freshness(result, source_key, now)
            votes.append(WeightedVote(result, source_key, freshness=freshness))

            raw_findings = result.get("findings", result.get("flags", result.get("alerts", [])))
            for f in raw_findings if isinstance(raw_findings, list) else [raw_findings]:
                if isinstance(f, dict):
                    f["source"] = source_key
                    findings.append(f)

        if additional:
            for item in additional:
                if isinstance(item, dict):
                    now = time.time()
                    sk = item.get("source", item.get("source_key", "unknown"))
                    freshness = _get_freshness(item, sk, now)
                    votes.append(WeightedVote(item, sk, freshness=freshness))
                    f_list = item.get("findings", item.get("flags", []))
                    for f in f_list if isinstance(f_list, list) else [f_list]:
                        if isinstance(f, dict):
                            f["source"] = sk
                            findings.append(f)

        if not votes:
            return _empty_result("No data sources available")

        fused = self._fuse_weighted(votes)
        findings = _deduplicate_findings(findings)

        risk_score = fused.get("risk_score", 50)
        confidence = fused.get("confidence", 0.3)

        return envelope(
            True,
            {
                "risk_score": round(risk_score, 1),
                "risk_level": _risk_level(risk_score),
                "confidence": round(confidence, 2),
                "key_findings": _top_findings(findings),
                "data_sources": [v.source_key for v in votes],
                "fused_at": fused.get("fused_at"),
                "intelligence_brief": self.generate_intelligence_brief(
                    {
                        "risk_score": risk_score,
                        "key_findings": findings,
                        "data_sources": [v.source_key for v in votes],
                    }
                ),
            },
            confidence,
            "token_risk_fusion",
        )

    # ── Price Data Fusion ──
    async def fuse_price_data(self, *sources: dict | None) -> dict:
        self._stats["calls"] += 1
        prices: list[tuple[float, str, float]] = []  # (price, source_key, freshness)

        for result in sources:
            if not result:
                continue
            price = _extract_price(result)
            if price is not None:
                self._stats["sources_fused"] += 1
                now = time.time()
                sk = result.get("source_key", result.get("source", "unknown"))
                freshness = _get_freshness(result, sk, now)
                prices.append((price, sk, freshness))

        if len(prices) < 1:
            return _empty_result("No price data available")

        values = [p[0] for p in prices]
        filtered = _remove_outliers(values)

        if not filtered:
            return _empty_result("All prices removed as outliers")

        confidence = len(filtered) / max(len(values), 1) * min(1.0, len(filtered) / 3)
        fused_price = statistics.mean(filtered)

        return envelope(
            True,
            {
                "price_usd": round(fused_price, 8),
                "sources_count": len(prices),
                "source_list": [p[1] for p in prices],
                "min_price": min(filtered) if filtered else None,
                "max_price": max(filtered) if filtered else None,
                "outliers_removed": len(values) - len(filtered),
            },
            confidence,
            "price_fusion",
        )

    # ── Holder Data Fusion ──
    async def fuse_holder_data(
        self,
        rpc_result: dict | None = None,
        explorer_result: dict | None = None,
        additional: list[dict] | None = None,
    ) -> dict:
        self._stats["calls"] += 1
        votes: list[WeightedVote] = []
        holder_sets: list[list[dict]] = []

        for source_key, result in [("consensus_rpc", rpc_result), ("etherscan", explorer_result)]:
            if not result:
                continue
            self._stats["sources_fused"] += 1
            now = time.time()
            freshness = _get_freshness(result, source_key, now)
            votes.append(WeightedVote(result, source_key, freshness=freshness))
            holders = _extract_list(result, "holders", "data", "holder_list") or result.get("top_holders", [])
            if holders:
                holder_sets.append(holders)

        if additional:
            for item in additional:
                sk = item.get("source", "unknown")
                now = time.time()
                freshness = _get_freshness(item, sk, now)
                votes.append(WeightedVote(item, sk, freshness=freshness))
                h = item.get("holders", item.get("top_holders", []))
                if h:
                    holder_sets.append(h)

        if not votes:
            return _empty_result("No holder data available")

        # Total supply fusion
        fused = self._fuse_weighted(votes)
        total_supply = fused.get("total_supply", 0)
        confidence = fused.get("confidence", 0.3)

        merged_holders = _merge_holder_lists(holder_sets)

        return envelope(
            True,
            {
                "total_supply": total_supply,
                "holder_count": len(merged_holders),
                "top_holders": merged_holders[:50],
                "data_sources": [v.source_key for v in votes],
            },
            confidence,
            "holder_fusion",
        )

    # ── Intelligence Brief ──
    def generate_intelligence_brief(self, token_data: dict) -> dict:
        score = token_data.get("risk_score", 50)
        findings = token_data.get("key_findings", [])

        one_liner = "No risk findings to report" if not findings else _one_liner(score, findings)

        return {
            "one_liner": one_liner,
            "risk_level": _risk_level(score),
            "confidence": "high" if len(findings) >= 3 else "moderate" if findings else "low",
            "key_numbers": {
                "risk_score": score,
                "risk_factors": len(findings),
                "data_sources": len(token_data.get("data_sources", [])),
            },
        }

    # ── Internal Fusion Logic ──
    def _fuse_weighted(self, votes: list[WeightedVote]) -> dict:
        if not votes:
            return {"risk_score": 50, "confidence": 0}

        # Calculate weighted average for numeric fields
        numeric_weights: dict[str, list[tuple[float, float]]] = {}

        for v in votes:
            val = v.value
            if not isinstance(val, dict):
                continue

            adjusted = v.weight * v.freshness
            for key in ("risk_score", "confidence", "total_supply"):
                raw = val.get(key)
                if isinstance(raw, (int, float)):
                    numeric_weights.setdefault(key, []).append((float(raw), adjusted))

        fused: dict[str, float] = {}
        total_confidence = 0.0

        for key, values in numeric_weights.items():
            weighted_sum = sum(v * w for v, w in values)
            total_w = sum(w for _, w in values)
            if total_w > 0:
                fused[key] = weighted_sum / total_w
            else:
                fused[key] = values[0][0]

        if "confidence" in fused:
            total_confidence = fused["confidence"]
        else:
            total_confidence = max(0.0, min(1.0, sum(v.weight * v.freshness for v in votes) / len(votes)))

        fused["confidence"] = round(total_confidence, 3)
        fused["fused_at"] = datetime.datetime.now(datetime.UTC).isoformat()
        return fused

    @property
    def stats(self) -> dict:
        return {
            "total_fusions": self._stats["calls"],
            "sources_fused": self._stats["sources_fused"],
            "errors": self._stats["errors"],
            "avg_sources_per_fusion": (round(self._stats["sources_fused"] / max(self._stats["calls"], 1), 1)),
        }


# Module-level singleton — used by scanner service and other consumers
fusion = DataFusion()
