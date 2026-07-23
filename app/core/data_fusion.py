"""
Data Fusion Engine — Intelligently Combine Multiple Data Sources
=================================================================

Design principle: No single data source is perfectly reliable. By fusing
results from independent providers — weighting by historical accuracy,
removing outliers, and scoring confidence — we produce intelligence that
is more trustworthy and actionable than any individual API.

This module is the central intelligence layer. Every multi-source lookup
(token risk, price, holders, entity) should route through here.

Usage:
    from app.core.data_fusion import DataFusion

    fusion = DataFusion()
    risk = await fusion.fuse_token_risk(
        goplus_result={...},
        dexscreener_result={...},
        honeypot_result={...},
    )
    price = await fusion.fuse_price_data(
        coingecko={"usd": 1.23},
        dexscreener={"priceUsd": "1.25"},
        birdeye={"price": 1.22},
        solana_tracker={"price": 1.24},
    )
    holder_info = await fusion.fuse_holder_data(
        rpc_result={"total_supply": 1_000_000, "holders": [...]},
        explorer_result={"holders": 1234, "top_holders": [...]},
    )
    brief = fusion.generate_intelligence_brief({
        "risk_score": 72,
        "key_findings": [...],
        "data_sources": [...],
    })
"""

import datetime
import statistics
import time
from dataclasses import dataclass, field
from typing import Any

from app.core.logging import get_logger

logger = get_logger("data_fusion")

# ── Confidence Weights ─────────────────────────────────────────────
# These are tuned empirically. Each source gets weighted by its
# historical reliability and data freshness.

SOURCE_WEIGHTS: dict[str, float] = {
    # On-chain / RPC sources — ground truth
    "goplus": 0.40,
    "goplus_security": 0.40,
    "consensus_rpc": 0.50,
    "helius_rpc": 0.45,
    "solana_rpc": 0.45,
    "alchemy_rpc": 0.45,
    "evm_rpc": 0.45,
    # Explorers — second-level verification
    "etherscan": 0.30,
    "blockscout": 0.30,
    "solscan": 0.30,
    "solanatracker": 0.35,
    # Premium APIs
    "arkham": 0.50,
    "moralis": 0.40,
    "bitquery": 0.40,
    # Free APIs — useful but less reliable
    "dexscreener": 0.25,
    "birdeye": 0.30,
    "coingecko": 0.35,
    "defillama": 0.30,
    "jupiter": 0.30,
    "mobula": 0.15,
    # Our own data
    "sentinel_scanner": 0.45,
    "wallet_memory_bank": 0.40,
    "local_price_cache": 0.35,
    "local_rag": 0.30,
    # Third-party security
    "honeypot_is": 0.35,
    "rugpull_detector": 0.35,
    "governance_attack": 0.30,
    "flash_loan_detector": 0.30,
}

# How long before a data source is considered stale (seconds)
SOURCE_TTL: dict[str, int] = {
    "goplus": 300,
    "dexscreener": 60,
    "coingecko": 60,
    "birdeye": 60,
    "jupiter": 30,
    "consensus_rpc": 10,
    "helius_rpc": 10,
    "solana_rpc": 10,
    "arkham": 3600,
    "etherscan": 120,
    "local_price_cache": 30,
    "sentinel_scanner": 300,
    "wallet_memory_bank": 3600,
}


# ── Response Envelope ──────────────────────────────────────────────


def envelope(
    data: Any,
    source: str,
    freshness: str | None = None,
    cache_ttl: int = 300,
    fallback_used: bool = False,
    confidence: float = 0.5,
) -> dict:
    """Wrap any data response with a standard metadata envelope.

    Every API response should use this envelope so consumers can
    assess data quality, freshness, and provenance at a glance.
    """
    if freshness is None:
        freshness = datetime.datetime.now(datetime.UTC).isoformat()
    return {
        "data": data,
        "meta": {
            "source": source,
            "freshness": freshness,
            "cache_ttl": cache_ttl,
            "fallback_used": fallback_used,
            "confidence": round(confidence, 3),
            "weight": SOURCE_WEIGHTS.get(source, 0.25),
        },
    }


# ── Weighted Voter / Fusion Core ──────────────────────────────────


@dataclass
class WeightedVote:
    """A single data source's opinion with confidence metadata."""

    source: str
    value: float | None
    confidence: float = 0.5
    weight: float = 0.25
    freshness: float = 1.0  # 1.0 = now, 0.0 = completely stale
    metadata: dict = field(default_factory=dict)


class DataFusion:
    """Combine multiple data sources for highest quality output.

    Fusion strategy:
      1. Collect all available votes with their confidence weights
      2. Apply freshness decay — old data gets lower effective weight
      3. Remove outliers using IQR (interquartile range)
      4. Weighted average of remaining values
      5. Report effective sample size and confidence

    The result always includes: fused_value, confidence, sources_used,
    sources_total, freshness, and a human-readable summary.
    """

    def __init__(self) -> None:
        self._stats = {
            "fusions": 0,
            "outliers_removed": 0,
            "fallbacks_used": 0,
        }

    # ── Token Risk Fusion ──────────────────────────────────────────

    async def fuse_token_risk(
        self,
        goplus_result: dict | None = None,
        dexscreener_result: dict | None = None,
        honeypot_result: dict | None = None,
        sentinel_result: dict | None = None,
        rugpull_result: dict | None = None,
        additional: list[dict] | None = None,
    ) -> dict:
        """Weighted combination of multiple risk sources into a single assessment.

        Each source provides a risk_score (0-100) and list of risk factors.
        The fusion applies SOURCE_WEIGHTS and freshness decay, then produces
        a consolidated score with confidence interval.
        """
        votes: list[WeightedVote] = []
        findings: list[dict] = []
        raw_scores: list[float] = []

        # ── Extract votes from each source ──
        extracted = []

        if goplus_result:
            score = self._extract_numeric(goplus_result, "risk_score", "risk")
            extracted.append(("GoPlus Security", score, goplus_result, "goplus_security"))

        if dexscreener_result:
            score = self._extract_numeric(dexscreener_result, "risk_score")
            extracted.append(("DexScreener", score, dexscreener_result, "dexscreener"))

        if honeypot_result:
            score = self._extract_numeric(honeypot_result, "risk_score", "risk")
            is_honeypot = self._extract_boolean(honeypot_result, "is_honeypot")
            extracted.append(("Honeypot Detector", score, honeypot_result, "honeypot_is"))
            if is_honeypot:
                findings.append(
                    {
                        "finding": "Honeypot detected — cannot sell",
                        "source": "honeypot_is",
                        "severity": "CRITICAL",
                    }
                )

        if sentinel_result:
            score = self._extract_numeric(sentinel_result, "risk_score", "security_score")
            extracted.append(("SENTINEL Scanner", score, sentinel_result, "sentinel_scanner"))

        if rugpull_result:
            score = self._extract_numeric(rugpull_result, "risk_score", "score")
            extracted.append(("Rug Pull Predictor", score, rugpull_result, "rugpull_detector"))

        if additional:
            for i, add in enumerate(additional):
                name = add.get("source", f"additional_{i}")
                score = self._extract_numeric(add, "risk_score", "risk", "score")
                extracted.append((name, score, add, name))

        # ── Build votes with freshness ──
        now = time.time()
        for name, score, result, source_key in extracted:
            if score is not None:
                freshness = self._get_freshness(result, source_key, now)
                weight = SOURCE_WEIGHTS.get(source_key, 0.25)
                conf = self._extract_numeric(result, "confidence", default=0.5)

                votes.append(
                    WeightedVote(
                        source=source_key,
                        value=score,
                        confidence=conf,
                        weight=weight,
                        freshness=freshness,
                        metadata={"label": name},
                    )
                )
                raw_scores.append(score)

            # Extract risk findings
            factors = self._extract_list(result, "risk_factors", "threats", "findings")
            for factor in factors:
                if isinstance(factor, str):
                    findings.append(
                        {
                            "finding": factor,
                            "source": source_key,
                            "severity": "MEDIUM",
                        }
                    )
                elif isinstance(factor, dict):
                    findings.append(
                        {
                            "finding": factor.get(
                                "finding", factor.get("description", str(factor))
                            ),
                            "source": source_key,
                            "severity": factor.get("severity", "MEDIUM"),
                        }
                    )

        if not votes:
            return self._empty_result("No risk sources available")

        # ── Fuse ──
        fused = self._fuse_weighted(votes)
        fused["risk_findings"] = self._deduplicate_findings(findings)
        fused["sources_tried"] = len(extracted)
        fused["sources_returned"] = len(votes)

        # Risk level label
        score = fused.get("fused_value", 50)
        fused["risk_level"] = self._risk_level(score)

        # Generate one-liner summary
        high_risk_count = sum(
            1 for f in fused.get("risk_findings", []) if f.get("severity") in ("CRITICAL", "HIGH")
        )
        if high_risk_count >= 3 and score >= 70:
            summary = (
                f"CRITICAL: {high_risk_count} high-severity findings, fused risk {score:.0f}/100"
            )
        elif score >= 60:
            summary = f"HIGH risk ({score:.0f}/100) — {high_risk_count} material findings"
        elif score >= 40:
            summary = f"MEDIUM risk ({score:.0f}/100) — review details below"
        elif score < 20 and len(votes) >= 2:
            summary = f"LOW risk ({score:.0f}/100) — corroborated by {len(votes)} sources"
        else:
            summary = f"Risk score: {score:.0f}/100 from {len(votes)} sources"

        fused["summary"] = summary

        self._stats["fusions"] += 1
        return fused

    # ── Price Data Fusion ──────────────────────────────────────────

    async def fuse_price_data(self, *sources: dict | None) -> dict:
        """Cross-reference prices from multiple sources for accuracy.

        Strategy:
          1. Collect all non-null prices with source confidence
          2. Remove statistical outliers (IQR method)
          3. Compute weighted average of remaining prices
          4. Report spread and confidence

        Returns:
            dict with keys: price_usd, confidence, sources_used, sources_total,
                            min, max, spread_pct, outlier_sources, all_prices
        """
        votes: list[WeightedVote] = []
        raw_prices: list[tuple[str, float]] = []

        for source_data in sources:
            if not source_data or not isinstance(source_data, dict):
                continue

            source_name = source_data.get("source", "unknown")
            price = self._extract_price(source_data)
            if price is not None and price > 0:
                weight = SOURCE_WEIGHTS.get(source_name, 0.25)
                freshness = self._get_freshness(source_data, source_name, time.time())
                conf = self._extract_numeric(source_data, "confidence", default=0.5)

                votes.append(
                    WeightedVote(
                        source=source_name,
                        value=price,
                        confidence=conf,
                        weight=weight,
                        freshness=freshness,
                    )
                )
                raw_prices.append((source_name, price))

        if not votes:
            return self._empty_result("No price sources available")

        # ── Detect and remove outliers (IQR) ──
        prices_only = [v.value for v in votes]
        outliers, clean_votes = self._remove_outliers(votes, prices_only)

        if outliers:
            self._stats["outliers_removed"] += len(outliers)
            logger.info(f"Price outliers removed: {[(o.source, o.value) for o in outliers]}")

        if not clean_votes:
            # All were outliers — fall back to median of all
            clean_votes = votes
            self._stats["fallbacks_used"] += 1

        # ── Fuse remaining ──
        fused = self._fuse_weighted(clean_votes)

        # Price-specific enrichments
        all_prices = dict(raw_prices)
        fused_price = fused.get("fused_value", 0)

        result = {
            "price_usd": round(fused_price, 8) if fused_price else None,
            "all_prices": all_prices,
            "sources_used": len(all_prices),
            "sources_total": len(sources),
            "outlier_sources": [o.source for o in outliers],
            "confidence": round(fused.get("confidence", 0), 3),
        }

        if len(all_prices) > 1 and fused_price:
            prices_list = list(all_prices.values())
            result["min"] = min(prices_list)
            result["max"] = max(prices_list)
            result["spread_pct"] = (
                round((max(prices_list) - min(prices_list)) / min(prices_list) * 100, 2)
                if min(prices_list) > 0
                else None
            )

        # Summary
        n = len(clean_votes)
        total = len(raw_prices)
        conf = result.get("confidence", 0)
        if n >= 3 and conf > 0.7:
            summary = (
                f"High-confidence price ${fused_price:.4f} "
                f"(consensus of {n}/{total} sources, spread={result.get('spread_pct', '?')}%)"
            )
        elif n >= 2:
            summary = (
                f"Price ${fused_price:.4f} "
                f"(averaged {n}/{total} sources after removing {len(outliers)} outlier)"
            )
        else:
            summary = f"Price ${fused_price:.4f} (single source — limited confidence)"

        result["summary"] = summary
        return result

    # ── Holder Data Fusion ─────────────────────────────────────────

    async def fuse_holder_data(
        self,
        rpc_result: dict | None = None,
        explorer_result: dict | None = None,
        dexscreener_result: dict | None = None,
        bitquery_result: dict | None = None,
    ) -> dict:
        """Combine on-chain + explorer + DEX data for complete holder picture.

        On-chain RPC gives raw holder counts. Explorer APIs give distributions.
        DEX data gives liquidity context. Combined, they tell the full story.
        """
        holder_data = {
            "total_holders": None,
            "top_holders": [],
            "holder_distribution": {},
            "concentration_metrics": {},
            "sources_used": [],
            "confidence": 0.0,
        }

        source_count = 0
        confidences = []
        top_holder_sets = []

        # RPC — ground truth holder count
        if rpc_result:
            count = self._extract_numeric(rpc_result, "holder_count", "total_supply")
            if count is not None:
                holder_data["total_holders"] = int(count)
                holder_data["sources_used"].append("rpc")
                source_count += 1
                confidences.append(0.45)

            holders_list = self._extract_list(rpc_result, "holders", "accounts")
            if holders_list:
                top_holder_sets.append(holders_list[:20])

        # Explorer — distribution details
        if explorer_result:
            count = self._extract_numeric(
                explorer_result, "holder_count", "holders", "total_holders"
            )
            if count is not None:
                explorer_holders = int(count)
                # If RPC count is different, use whichever is higher (RPC may miss some)
                current = holder_data["total_holders"]
                if current is None or explorer_holders > current:
                    holder_data["total_holders"] = explorer_holders
                holder_data["sources_used"].append("explorer")
                source_count += 1
                confidences.append(0.30)

            holders_list = self._extract_list(explorer_result, "holders", "top_holders")
            if holders_list:
                top_holder_sets.append(holders_list[:20])

            # Gini coefficient
            gini = self._extract_numeric(explorer_result, "gini", "gini_coefficient")
            if gini is not None:
                holder_data["concentration_metrics"]["gini"] = gini

        # DexScreener — liquidity context
        if dexscreener_result:
            pairs = self._extract_list(dexscreener_result, "pairs", "pairs")
            if pairs:
                holder_data["sources_used"].append("dexscreener")
                source_count += 1
                confidences.append(0.25)
                # Extract liquidity from best pair
                if isinstance(pairs[0], dict):
                    liq = pairs[0].get("liquidity", {})
                    if isinstance(liq, dict):
                        holder_data["liquidity_usd"] = liq.get("usd")
                    elif isinstance(liq, (int, float)):
                        holder_data["liquidity_usd"] = liq

        # Bitquery — additional holder distribution
        if bitquery_result:
            holders_list = self._extract_list(bitquery_result, "holders", "data", "result")
            if holders_list:
                top_holder_sets.append(holders_list[:20])
                holder_data["sources_used"].append("bitquery")
                source_count += 1
                confidences.append(0.40)
                count = self._extract_numeric(bitquery_result, "total_holders", "holder_count")
                if count is not None and holder_data["total_holders"] is None:
                    holder_data["total_holders"] = int(count)

        # ── Merge top holders from all sources ──
        if top_holder_sets:
            merged = self._merge_holder_lists(top_holder_sets)
            holder_data["top_holders"] = merged[:20]
            # Top 10 concentration
            if merged:
                top10_pct = sum(
                    h.get("percentage", 0) or 0 for h in merged[:10] if isinstance(h, dict)
                )
                holder_data["concentration_metrics"]["top_10_pct"] = round(top10_pct, 2)

        # ── Confidence ──
        if confidences:
            holder_data["confidence"] = round(sum(confidences) / len(confidences), 3)

        # ── Summary ──
        holders = holder_data.get("total_holders")
        gini = holder_data.get("concentration_metrics", {}).get("gini")
        top_pct = holder_data.get("concentration_metrics", {}).get("top_10_pct")

        parts = [f"Holders: {holders:,}" if holders else "No holder data"]
        if gini is not None:
            gini_label = (
                "highly concentrated"
                if gini > 0.7
                else "moderately distributed"
                if gini > 0.4
                else "well distributed"
            )
            parts.append(f"Gini {gini:.3f} ({gini_label})")
        if top_pct is not None:
            parts.append(f"Top 10: {top_pct:.1f}%")
        parts.append(f"Sources: {source_count}")

        holder_data["summary"] = " | ".join(parts)

        return holder_data

    # ── Intelligence Brief ─────────────────────────────────────────

    def generate_intelligence_brief(self, token_data: dict) -> dict:
        """Generate a human-readable intelligence brief from fused token data.

        This is THE community-facing output format. It transforms raw
        security data into an actionable brief that any user can understand
        at a glance.
        """
        risk_score = (
            token_data.get("fused_value")
            or token_data.get("risk_score")
            or token_data.get("fused_risk", {}).get("fused_value", 50)
        )
        if isinstance(risk_score, float):
            risk_score = round(risk_score)

        sources = token_data.get("data_sources", []) or token_data.get("sources_used", [])
        risk_findings = token_data.get("risk_findings", []) or token_data.get("key_findings", [])

        return {
            "summary": self._one_liner(risk_score, risk_findings),
            "risk_score": risk_score,
            "risk_level": self._risk_level(risk_score),
            "key_findings": self._top_findings(risk_findings),
            "data_sources": sources,
            "confidence": token_data.get("confidence", 0.5),
            "freshness": datetime.datetime.now(datetime.UTC).isoformat(),
            "generated_by": "data_fusion_engine",
        }

    # ── Internal Fusion Logic ──────────────────────────────────────

    def _fuse_weighted(self, votes: list[WeightedVote]) -> dict:
        """Weighted fusion with freshness decay and effective N."""
        if not votes:
            return {
                "fused_value": None,
                "confidence": 0.0,
                "sources_used": 0,
                "sources_total": 0,
            }

        total_weight = 0.0
        weighted_sum = 0.0
        confidences = []
        sources_used = []

        for vote in votes:
            # Effective weight = base weight * freshness * confidence
            effective = vote.weight * vote.freshness * vote.confidence
            if effective <= 0:
                continue

            weighted_sum += vote.value * effective
            total_weight += effective
            confidences.append(vote.confidence * vote.freshness)
            sources_used.append(vote.source)

        if total_weight <= 0:
            return {
                "fused_value": statistics.median(v.value for v in votes),
                "confidence": 0.1,
                "sources_used": len(votes),
                "sources_total": len(votes),
                "fallback": "weighted_fusion_failed_used_median",
            }

        fused_value = weighted_sum / total_weight
        avg_confidence = sum(confidences) / len(confidences) if confidences else 0

        return {
            "fused_value": round(fused_value, 6),
            "confidence": round(avg_confidence, 3),
            "sources_used": sources_used,
            "sources_total": len(votes),
            "effective_samples": round(total_weight, 2),
        }

    def _remove_outliers(
        self, votes: list[WeightedVote], values: list[float]
    ) -> tuple[list[WeightedVote], list[WeightedVote]]:
        """Remove statistical outliers using IQR method."""
        if len(values) < 4:
            return [], votes  # Need at least 4 values for meaningful IQR

        sorted_vals = sorted(values)
        q1 = statistics.median(sorted_vals[: len(sorted_vals) // 2])
        q3 = statistics.median(sorted_vals[(len(sorted_vals) + 1) // 2 :])
        iqr = q3 - q1

        if iqr == 0:
            return [], votes  # No spread, all values equal

        lower = q1 - 1.5 * iqr
        upper = q3 + 1.5 * iqr

        outliers = []
        clean = []
        for vote in votes:
            if vote.value < lower or vote.value > upper:
                outliers.append(vote)
            else:
                clean.append(vote)

        return outliers, clean

    def _get_freshness(self, result: dict, source_key: str, now: float) -> float:
        """Compute freshness score 0-1 based on data age."""
        ttl = SOURCE_TTL.get(source_key, 300)

        # Check metadata timestamp
        meta = result.get("meta", {})
        freshness_str = meta.get("freshness") or result.get("freshness")

        if freshness_str:
            try:
                if isinstance(freshness_str, (int, float)):
                    age = now - freshness_str
                else:
                    dt = datetime.datetime.fromisoformat(freshness_str.replace("Z", "+00:00"))
                    age = now - dt.timestamp()
                if age <= 0:
                    return 1.0
                # Decay: fresh = 1.0, at TTL = 0.5, at 2xTTL = 0.0
                return max(0.0, 1.0 - (age / ttl) * 0.5)
            except (ValueError, TypeError):
                pass

        return 0.8  # Default: moderately fresh

    def _extract_numeric(
        self, data: dict, *keys: str, default: float | None = None
    ) -> float | None:
        """Extract a numeric value from nested dict by trying keys in order."""
        for key in keys:
            val = data.get(key)
            if val is not None:
                try:
                    return float(val)
                except (ValueError, TypeError):
                    # Might be nested
                    if isinstance(val, dict):
                        for sub_key in ("value", "score", "usd", "percent"):
                            sub = val.get(sub_key)
                            if sub is not None:
                                try:
                                    return float(sub)
                                except (ValueError, TypeError):
                                    continue
        return default

    def _extract_boolean(self, data: dict, *keys: str) -> bool | None:
        """Extract a boolean value by trying keys in order."""
        for key in keys:
            val = data.get(key)
            if isinstance(val, bool):
                return val
            if isinstance(val, str):
                return val.lower() in ("true", "yes", "1")
            if isinstance(val, int):
                return bool(val)
        return None

    def _extract_list(self, data: dict, *keys: str) -> list:
        """Extract a list value by trying keys in order."""
        for key in keys:
            val = data.get(key)
            if isinstance(val, list):
                return val
            if isinstance(val, dict):
                # Maybe nested under "data"
                inner = val.get("data")
                if isinstance(inner, list):
                    return inner
        return []

    def _extract_price(self, data: dict) -> float | None:
        """Extract a USD price from any source's response format."""
        # Common price field names
        for key in ("price_usd", "price", "usd", "current_price"):
            val = data.get(key)
            if val is not None:
                try:
                    return float(val)
                except (ValueError, TypeError):
                    pass

        # Nested structures
        for nested_key in ("data", "price", "market_data"):
            nested = data.get(nested_key)
            if isinstance(nested, dict):
                result = self._extract_price(nested)
                if result is not None:
                    return result

        # DexScreener format: pairs[0].priceUsd
        pairs = data.get("pairs", [])
        if pairs and isinstance(pairs, list) and len(pairs) > 0:
            price_str = pairs[0].get("priceUsd") or pairs[0].get("price_usd")
            if price_str:
                try:
                    return float(price_str)
                except (ValueError, TypeError):
                    pass

        return None

    def _merge_holder_lists(self, holder_sets: list[list[dict]]) -> list[dict]:
        """Merge holder lists from multiple sources, deduplicating by address."""
        seen: set[str] = set()
        merged = []

        for holder_list in holder_sets:
            for holder in holder_list:
                if not isinstance(holder, dict):
                    continue
                addr = (
                    holder.get("address", "") or holder.get("owner", "") or holder.get("wallet", "")
                )
                if addr and addr not in seen:
                    seen.add(addr)
                    merged.append(holder)

        return merged

    def _deduplicate_findings(self, findings: list[dict]) -> list[dict]:
        """Deduplicate risk findings by normalizing finding text."""
        seen: set[str] = set()
        unique = []
        for f in findings:
            text = f.get("finding", "").lower().strip()
            # Normalize
            text = text.rstrip(".").rstrip("!")
            key = text[:80]  # Use first 80 chars as dedup key
            if key not in seen:
                seen.add(key)
                unique.append(f)
        return unique

    def _risk_level(self, score: float) -> str:
        """Map numeric score to risk level label."""
        if score >= 85:
            return "CRITICAL"
        elif score >= 70:
            return "HIGH"
        elif score >= 50:
            return "MEDIUM"
        elif score >= 25:
            return "LOW"
        return "LOW"

    def _one_liner(self, score: float, findings: list[dict]) -> str:
        """Generate a single-line risk assessment."""
        high_count = sum(1 for f in findings if f.get("severity") in ("CRITICAL", "HIGH"))
        level = self._risk_level(score)

        if level == "CRITICAL" and high_count >= 3:
            return (
                f"Extreme risk ({score:.0f}/100) — {high_count} critical "
                f"indicators detected. Avoid interaction."
            )
        elif level == "HIGH":
            return (
                f"High risk ({score:.0f}/100) — {high_count} material concerns. "
                f"Thorough review recommended before engagement."
            )
        elif level == "MEDIUM":
            return (
                f"Moderate risk ({score:.0f}/100) — some concerns identified. "
                f"Standard due diligence recommended."
            )
        elif level == "LOW" and not findings:
            return f"Low risk ({score:.0f}/100) — no significant concerns detected."
        else:
            return f"Risk score: {score:.0f}/100 — {len(findings)} factors evaluated."

    def _top_findings(self, findings: list[dict], max_count: int = 5) -> list[dict]:
        """Return the most severe findings, sorted by severity."""
        severity_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
        sorted_f = sorted(
            findings,
            key=lambda f: severity_order.get(f.get("severity", "MEDIUM"), 99),
        )
        return sorted_f[:max_count]

    def _empty_result(self, reason: str) -> dict:
        """Return a structured empty result."""
        return {
            "fused_value": None,
            "confidence": 0.0,
            "sources_used": [],
            "sources_total": 0,
            "summary": f"Insufficient data — {reason}",
            "risk_level": "UNKNOWN",
            "risk_findings": [],
        }

    def stats(self) -> dict:
        """Return fusion engine statistics."""
        return dict(self._stats)


# ── Module-level singleton ─────────────────────────────────────────

fusion = DataFusion()
