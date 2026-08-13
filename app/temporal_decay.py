#!/usr/bin/env python3
"""
TEMPORAL DECAY SCORING
=====================
Applies age-based score decay to retrieval results.
Critical for crypto: a 3-year-old exploit shouldn't rank the same as yesterday's.

Per-domain half-lives:
  - Crypto price data:     1 day   (stale in hours)
  - Trading signals:       3 days  (short-term relevance)
  - News/investigation:   30 days  (still relevant for weeks)
  - Protocol docs:         90 days  (changes slowly)
  - Scam patterns:        365 days  (patterns persist)
  - Forensic/audit:        ∞        (permanent reference)

Formula: adjusted_score = raw_score * e^(-lambda * age_days)
where lambda = ln(2) / half_life

Also supports recency boosting for very fresh content (<24h).
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

# ── Domain half-lives (days) ──────────────────────────────────────────
HALF_LIVES: dict[str, float] = {
    # Sub-1-day: prices, real-time data
    "price_feed": 1.0,
    "trading_signal": 3.0,
    # Weeks: news, investigations
    "news_article": 30.0,
    "market_intel": 30.0,
    "investigation": 30.0,
    # Months: protocols, governance
    "token_analysis": 90.0,
    "contract_audit": 90.0,
    "protocol_doc": 90.0,
    # Year+: patterns persist
    "scam_pattern": 365.0,
    "known_scams": 365.0,
    "forensic_report": float("inf"),  # never decays
    "transaction_pattern": 365.0,
    # Default
    "default": 60.0,
}

# ── Collection → domain mapping ──────────────────────────────────────
COLLECTION_DOMAIN: dict[str, str] = {
    "news_articles": "news_article",
    "market_intel": "market_intel",
    "token_analysis": "token_analysis",
    "contract_audits": "contract_audit",
    "scam_patterns": "scam_pattern",
    "known_scams": "known_scams",
    "forensic_reports": "forensic_report",
    "wallet_profiles": "token_analysis",
    "transaction_patterns": "transaction_pattern",
    "bundle_patterns": "trading_signal",
    "wallet_clusters": "trading_signal",
    "cluster_labels": "scam_pattern",
}


def get_half_life(collection: str) -> float:
    """Get the half-life in days for a collection."""
    domain = COLLECTION_DOMAIN.get(collection, "default")
    return HALF_LIVES.get(domain, HALF_LIVES["default"])


def compute_decay(age_days: float, half_life_days: float) -> float:
    """
    Compute the decay multiplier: e^(-lambda * age_days)
    where lambda = ln(2) / half_life_days.

    Returns 1.0 for zero age or infinite half-life (no decay).
    """
    if age_days <= 0:
        return 1.0
    if half_life_days == float("inf"):
        return 1.0
    if half_life_days <= 0:
        return 1.0

    lam = math.log(2) / half_life_days
    return math.exp(-lam * age_days)


def compute_recency_boost(age_days: float, boost_days: float = 1.0, boost_factor: float = 1.15) -> float:
    """
    Boost very fresh content (published within last boost_days).
    Linearly decays from boost_factor to 1.0 over boost_days.

    E.g., content <4h old gets 1.15x boost, content >1 day old gets 1.0x.
    """
    if age_days >= boost_days:
        return 1.0
    if age_days <= 0:
        return boost_factor
    # Linear interpolation
    return 1.0 + (boost_factor - 1.0) * (1.0 - age_days / boost_days)


def apply_temporal_decay(
    results: list[dict[str, Any]],
    now: datetime | None = None,
    apply_recency_boost: bool = True,
) -> list[dict[str, Any]]:
    """
    Apply temporal decay scoring to RAG search results.

    Each result dict should have:
      - "similarity": raw retrieval score (0-1)
      - "collection": which collection the doc came from
      - "stored_at" or "metadata.stored_at" or "created_at": timestamp

    Adds/updates:
      - "raw_similarity": original score before decay
      - "similarity": decay-adjusted score
      - "decay_factor": the multiplier applied
      - "age_days": age in days
      - "recency_boost": boost for fresh content (if applicable)
    """
    if now is None:
        now = datetime.now(UTC)

    for r in results:
        # Extract timestamp
        ts_str = (
            r.get("stored_at") or r.get("metadata", {}).get("stored_at", "")
            if isinstance(r.get("metadata"), dict)
            else "" or r.get("created_at", "")
        )
        if isinstance(ts_str, datetime):
            ts = ts_str
        elif ts_str:
            try:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                ts = None
        else:
            ts = None

        # Compute age
        age_days = 0.0
        if ts:
            try:
                age_days = max(0.0, (now - ts).total_seconds() / 86400.0)
            except TypeError:
                age_days = 0.0

        # Compute decay
        collection = r.get("collection", "default")
        half_life = get_half_life(collection)
        decay = compute_decay(age_days, half_life)

        # Compute recency boost
        recency = 1.0
        if apply_recency_boost and age_days < 1.0:
            recency = compute_recency_boost(age_days, boost_days=1.0, boost_factor=1.15)

        # Apply
        raw = r.get("similarity", 0.0)
        r["raw_similarity"] = raw
        r["age_days"] = round(age_days, 2)
        r["decay_factor"] = round(decay, 4)
        r["recency_boost"] = round(recency, 4)
        r["similarity"] = round(raw * decay * recency, 6)

    # Re-sort by adjusted similarity
    results.sort(key=lambda x: x.get("similarity", 0), reverse=True)
    return results


def get_collection_freshness_summary(
    collection_stats: dict[str, int],
    doc_timestamps: dict[str, list[str]],
) -> dict[str, Any]:
    """
    Summarize the freshness of each collection.
    Returns median age, stale docs count, and overall health.
    """
    now = datetime.now(UTC)
    summary: dict[str, dict[str, str | int | float | None]] = {}

    for coll, count in collection_stats.items():
        if coll not in doc_timestamps or not doc_timestamps[coll]:
            summary[coll] = {"count": count, "median_age_days": None, "health": "unknown"}
            continue

        ages = []
        for ts_str in doc_timestamps[coll][:100]:  # sample 100
            try:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                ages.append(max(0, (now - ts).total_seconds() / 86400.0))
            except (ValueError, AttributeError):
                continue

        if not ages:
            summary[coll] = {"count": count, "median_age_days": None, "health": "unknown"}
            continue

        ages.sort()
        median_age = ages[len(ages) // 2]
        half_life = get_half_life(coll)

        # Health: what fraction of score remains at median age?
        remaining_score = compute_decay(median_age, half_life)
        if remaining_score > 0.7:
            health = "fresh"
        elif remaining_score > 0.3:
            health = "aging"
        else:
            health = "stale"

        summary[coll] = {
            "count": count,
            "median_age_days": round(median_age, 1),
            "half_life_days": half_life if half_life != float("inf") else "∞",
            "score_retention": round(remaining_score, 3),
            "health": health,
        }

    return summary
