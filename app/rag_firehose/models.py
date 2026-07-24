"""
RAG Firehose - Data Models
===========================
Configuration constants and dataclasses for the firehose ingestion engine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ── Configuration ──────────────────────────────────────────────

RAG_API = "http://localhost:8000/api/v1/rag"

# Per-collection size caps (auto-evict oldest on overflow)
COLLECTION_CAPS = {
    "known_scams": 50000,
    "scam_patterns": 5000,
    "forensic_reports": 10000,
    "contract_audits": 5000,
    "wallet_profiles": 100000,
    "news_articles": 20000,
    "market_intel": 5000,
    "token_analysis": 50000,
    "transaction_patterns": 10000,
    "social_sentiment": 10000,
    "general": 10000,
}

# Quality thresholds (skip docs below this score)
MIN_QUALITY_SCORE = 30  # 0-100

# Rate limits (docs per minute per collection)
RATE_LIMITS = {
    "known_scams": 60,
    "news_articles": 30,
    "social_sentiment": 20,
    "default": 30,
}

# Content hash TTL in Redis (7 days for news, 30 for scams)
HASH_TTL = {
    "news_articles": 604800,  # 7 days
    "social_sentiment": 604800,  # 7 days
    "known_scams": 2592000,  # 30 days
    "default": 1209600,  # 14 days
}

@dataclass
class FeedSource:
    """A data source the firehose pulls from."""

    name: str
    collection: str
    cadence_seconds: int
    fetch_fn: Any = field(repr=False)
    enabled: bool = True
    description: str = ""
    last_run: float = 0
    runs: int = 0
    docs_ingested: int = 0
    docs_skipped: int = 0
    errors: int = 0

@dataclass
class FirehoseStats:
    """Current firehose statistics."""

    running: bool = False
    uptime_seconds: float = 0
    total_sources: int = 0
    total_ingested: int = 0
    total_skipped: int = 0
    total_errors: int = 0
    sources: dict[str, dict] = field(default_factory=dict)
    last_activity: float = 0
