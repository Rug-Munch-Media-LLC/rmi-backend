"""ScanResult dataclass — data model for token scan results."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.rag.intelligence import RAGContext


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

    # Clever wallet analysis (own-data engines — funding_analysis.py)
    funding_analysis: dict | None = None  # exchange-funded / fresh / mixer holder %
    wallet_freshness: dict | None = None  # aggregate freshness of top holders
    holder_overlap: dict | None = None  # holder overlap vs known scam tokens

    # News-event-to-price correlation (own-data engine — event_correlation.py)
    event_correlation: dict | None = None  # which news events moved the price

    # News-based sentiment (engines/news_sentiment.py — RugBrain news_v2)
    news_sentiment: dict | None = None  # {sentiment, confidence, article_count, trending_score, ...}

    # Ground News-style news card (engines/news_sentiment.py — get_token_news_card)
    news_card: dict[str, Any] | None = None  # {sentiment, trending_score, articles, corroboration}

    # SENTINEL deep-forensic phase (Phase 5 — sentinel_phase.py).
    # Always populated with free-tier module results; premium module results
    # are gated behind elite/admin tier (or the x402 SENTINEL deep scan).
    sentinel: dict[str, Any] | None = None  # {free, premium, premium_unlocked, premium_preview, ...}

    # Explainability (populated by scoring.compute_score)
    breakdown: dict[str, Any] | None = None  # per-dimension score breakdown
    evidence: dict[str, Any] | None = None  # raw findings backing the score

    # Metadata
    modules_run: list[str] = field(default_factory=list)
    enrichment_sources: list[str] = field(default_factory=list)
    tier: str = "free"
    scan_duration_ms: float = 0.0
    rag_context: RAGContext | None = None  # RAG enrichment context (from RAGIntelligenceEnricher)
