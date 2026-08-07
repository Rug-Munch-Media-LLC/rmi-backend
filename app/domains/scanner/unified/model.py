"""ScanResult dataclass — data model for token scan results."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.domains.scanner.unified.scanner import MarketContext
    from app.rag.intelligence import RAGContext


# ── SENTINEL typed deep-scan output profiles ──────────────────────────
# These dataclasses are the structured output of the SENTINEL deep-forensic
# phase (see sentinel_pipeline / sentinel_phase.py).  Populated on the
# ScanResult via ``deep_profile`` when a deep-tier scan runs; left ``None``
# for quick-tier scans.


@dataclass
class WalletBreakdown:
    """Holder-wallet breakdown for the SENTINEL deep scan."""

    total_holders: int
    fresh_wallet_count: int  # wallets <7 days old
    fresh_wallet_pct: float
    exchange_funded_count: int  # funded from CEX hot wallets
    exchange_funded_pct: float
    instant_buy_count: int  # funded+bought within 60s
    instant_buy_pct: float
    exchange_breakdown: dict[str, int]  # {"binance": 187, "coinbase": 124}
    fake_diversification_score: float  # 0-100
    hhi: float = 0.0  # Herfindahl-Hirschman Index from holder_analysis (0-10000)
    hhi_risk_level: str = "unknown"  # "low" / "moderate" / "high" / "critical"


@dataclass
class BundleProfile:
    """Same-block bundle / sniper behaviour profile."""

    bundles_detected: int
    deployer_funded_wallets: int
    deployer_funded_pct: float
    same_block_snipers: int
    jito_bundles: int
    behavior_clusters: int
    cross_launch_repeat_wallets: int
    bundle_risk_score: int  # 0-100
    bundle_risk_level: str  # LOW/MEDIUM/HIGH/CRITICAL


@dataclass
class DeployerProfile:
    """Deployer wallet behaviour profile."""

    wallet_age_days: int
    total_deployments: int
    rug_rate: float  # pct of deployments that rugged
    funded_from: str  # "coinbase", "tornado", "unknown"
    chains_active: list[str]
    sleep_pattern: str  # "normal", "irregular", "bot-like"


@dataclass
class DeepScanProfile:
    """Aggregated SENTINEL deep-scan profile (quick scan = None)."""

    wallet_breakdown: WalletBreakdown | None = None
    bundle_profile: BundleProfile | None = None
    deployer_profile: DeployerProfile | None = None
    wash_trading_score: float = 0.0
    honeypot_risk: str = "unknown"  # "none", "possible", "detected"
    mev_exposure: str = "unknown"
    sentiment_score: float = 0.0
    social_velocity: str = "normal"
    pump_dump_detected: bool = False
    flash_loan_risk: bool = False
    oracle_manipulation_risk: bool = False


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

    # SENTINEL critical-finding boost (set by scoring.compute_score). When
    # two or more SENTINEL deep-forensic modules independently report
    # critical/high findings, an additional capped penalty is applied to the
    # safety score (0 = no boost, positive = extra risk).
    sentinel_boost: float = 0.0

    # SENTINEL typed deep-scan output (populated when a deep-tier scan runs;
    # None for quick-tier scans).  Structured profiles derived from the raw
    # ``sentinel`` dict above.
    deep_profile: DeepScanProfile | None = None

    # Market conditions that influenced this scan (Part 1 — market-aware
    # scanning). Populated by the scanner before scoring from RugBrain's
    # /api/v1/fear-index and /api/v1/market/mood endpoints (cached 5 min).
    market_context: MarketContext | None = None

    # Explainability (populated by scoring.compute_score)
    breakdown: dict[str, Any] | None = None  # per-dimension score breakdown
    evidence: dict[str, Any] | None = None  # raw findings backing the score

    # Metadata
    modules_run: list[str] = field(default_factory=list)
    enrichment_sources: list[str] = field(default_factory=list)
    tier: str = "free"
    scan_tier: str = "quick"  # "quick" | "deep" — which SENTINEL module set ran
    scan_duration_ms: float = 0.0
    rag_context: RAGContext | None = None  # RAG enrichment context (from RAGIntelligenceEnricher)

    # Background-phase status. The scanner returns a fast core+baseline result
    # while deep/intel/SENTINEL/news phases continue in a background task.
    # "complete" — every phase finished before the HTTP deadline;
    # "running"  — background phases still in flight (poll the status endpoint).
    background_status: str = "complete"
