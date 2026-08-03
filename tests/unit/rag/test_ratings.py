"""Unit tests for the multi-axis rating engine (``app.rag.ratings``).

Covers the Ground News-style rating card:

  1. Enum serialization + default rating card shape.
  2. Source reputation tiering (established / independent / unknown) with
     URL normalisation and override maps.
  3. Factuality classification from language patterns + source tier.
  4. Market impact assessment (regulatory/ETF, hacks with $ thresholds,
     listings, upgrades, partnerships, background noise).
  5. Entity extraction reusing the ``app.rag.dedup`` vocabularies.
  6. Sentiment (BULLISH / NEUTRAL / BEARISH) and timeliness tiers.
  7. Confidence composition and the end-to-end ``rate_article`` card.
  8. Crawler wiring: ``crawl_and_ingest`` attaches the rating card to the
     ingested article metadata.

No network, Redis, or RAG-engine access happens in these tests — all
classifiers are pure functions over text.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.rag.ratings import (
    ArticleRating,
    Factuality,
    MarketImpact,
    SourceReputation,
    assess_market_impact,
    classify_factuality,
    classify_sentiment,
    classify_timeliness,
    compute_rating_confidence,
    extract_entities,
    rate_article,
)

_URL = "https://example.com/crypto-article"


# ── Enums + rating card shape ────────────────────────────────────────────


def test_enum_values_lowercase() -> None:
    """Rating enums serialize to their lowercase Ground News-style values."""
    assert Factuality.VERIFIED.value == "verified"
    assert Factuality.REPORTED.value == "reported"
    assert Factuality.OPINION.value == "opinion"
    assert Factuality.RUMOR.value == "rumor"
    assert SourceReputation.ESTABLISHED.value == "established"
    assert SourceReputation.INDEPENDENT.value == "independent"
    assert SourceReputation.UNKNOWN.value == "unknown"
    assert MarketImpact.HIGH.value == "high"
    assert MarketImpact.MEDIUM.value == "medium"
    assert MarketImpact.LOW.value == "low"


def test_article_rating_defaults() -> None:
    """A bare rating card carries conservative defaults."""
    rating = ArticleRating(article_id="a1", title="T", source="s")
    assert rating.factuality == Factuality.REPORTED
    assert rating.source_reputation == SourceReputation.UNKNOWN
    assert rating.market_impact == MarketImpact.LOW
    assert rating.sentiment == "NEUTRAL"
    assert rating.timeliness == "RECENT"
    assert rating.tokens_mentioned == []
    assert rating.confidence == 0.5
    assert rating.corroborating_sources == 0
    assert rating.on_chain_evidence is False


def test_article_rating_to_dict_serializable() -> None:
    """to_dict() flattens enums to values and is JSON-friendly."""
    rating = ArticleRating(
        article_id="a1",
        title="T",
        source="https://coindesk.com/x",
        factuality=Factuality.VERIFIED,
        source_reputation=SourceReputation.ESTABLISHED,
        market_impact=MarketImpact.HIGH,
        sentiment="BULLISH",
        tokens_mentioned=["btc"],
        protocols_mentioned=["uniswap"],
        people_mentioned=["vitalik"],
        timeliness="BREAKING",
        confidence=0.9,
        corroborating_sources=2,
        on_chain_evidence=True,
    )
    d = rating.to_dict()
    assert d["factuality"] == "verified"
    assert d["source_reputation"] == "established"
    assert d["market_impact"] == "high"
    assert d["tokens_mentioned"] == ["btc"]
    assert d["confidence"] == 0.9
    assert d["on_chain_evidence"] is True


# ── Source reputation ────────────────────────────────────────────────────


def test_source_tier_established() -> None:
    """Known mainstream domains map to ESTABLISHED."""
    from app.rag.ratings import _source_tier

    assert _source_tier("https://www.coindesk.com/markets/1") == SourceReputation.ESTABLISHED
    assert _source_tier("https://cointelegraph.com/rss") == SourceReputation.ESTABLISHED
    assert _source_tier("theblock.co") == SourceReputation.ESTABLISHED
    assert _source_tier("reuters.com") == SourceReputation.ESTABLISHED


def test_source_tier_independent() -> None:
    """Blog platforms map to INDEPENDENT."""
    from app.rag.ratings import _source_tier

    assert _source_tier("https://medium.com/@analyst/post") == SourceReputation.INDEPENDENT
    assert _source_tier("https://immunefi.com/blog/feed") == SourceReputation.INDEPENDENT


def test_source_tier_unknown() -> None:
    """Unrecognised sources fall back to UNKNOWN without raising."""
    from app.rag.ratings import _source_tier

    assert _source_tier("https://randomblog.xyz/post") == SourceReputation.UNKNOWN
    assert _source_tier("") == SourceReputation.UNKNOWN


def test_source_tier_override_map() -> None:
    """A caller-supplied map overrides the default for its domains."""
    from app.rag.ratings import _source_tier

    override = {"randomblog.xyz": "established"}
    assert _source_tier("https://randomblog.xyz/post", override) == SourceReputation.ESTABLISHED
    # Domains not in the override still consult the default map.
    assert _source_tier("https://coindesk.com/x", override) == SourceReputation.ESTABLISHED
    assert _source_tier("https://randomblog.xyz/post") == SourceReputation.UNKNOWN


# ── Factuality ───────────────────────────────────────────────────────────


def test_factuality_verified_established_source() -> None:
    """Confirmation language + established source → VERIFIED."""
    text = "The SEC confirmed the spot ETF was announced today, according to the filing."
    assert classify_factuality(text, "https://www.coindesk.com/markets/1") == Factuality.VERIFIED


def test_factuality_rumor_strong_signals() -> None:
    """Strong rumor words win even on established sources."""
    text = "Rumor: an unnamed source alleges the exchange was hacked."
    assert classify_factuality(text, "https://www.coindesk.com/markets/1") == Factuality.RUMOR


def test_factuality_opinion() -> None:
    """Analysis/editorial framing → OPINION."""
    text = "Opinion: why Ethereum could mean the end of L2 fragmentation."
    assert classify_factuality(text, "https://www.coindesk.com/markets/1") == Factuality.OPINION


def test_factuality_reported_default() -> None:
    """No strong signals → REPORTED (single source)."""
    text = "Bitcoin trading volumes rose today on major exchanges."
    assert classify_factuality(text, "https://www.coindesk.com/markets/1") == Factuality.REPORTED


def test_factuality_confirmed_but_not_established_is_reported() -> None:
    """Confirmation language from a non-established source stays REPORTED."""
    text = "The team confirmed the token launch date on Telegram."
    assert classify_factuality(text, "https://randomblog.xyz/post") == Factuality.REPORTED


def test_factuality_single_hedge_is_reported() -> None:
    """A single 'could/might/potential' hedge is REPORTED, not RUMOR."""
    text = "Analysts say the upgrade could boost throughput next quarter."
    assert classify_factuality(text, "https://www.coindesk.com/markets/1") == Factuality.REPORTED


def test_factuality_multiple_hedges_is_rumor() -> None:
    """Multiple hedge words push toward RUMOR territory."""
    text = "An unnamed source says SOL could reach new highs; it might, but potential risks remain."
    assert classify_factuality(text, "https://www.coindesk.com/markets/1") == Factuality.RUMOR


# ── Market impact ────────────────────────────────────────────────────────


def test_market_impact_sec_etf_high() -> None:
    """Regulatory/ETF news is HIGH impact."""
    assert assess_market_impact("SEC approves spot Ethereum ETF", []) == MarketImpact.HIGH
    assert assess_market_impact("The bitcoin ETF saw record inflows", []) == MarketImpact.HIGH


def test_market_impact_hack_over_10m_high() -> None:
    """Hack/exploit above $10M → HIGH impact."""
    text = "Exploit drains $25M from the lending protocol."
    assert assess_market_impact(text, ["eth"]) == MarketImpact.HIGH


def test_market_impact_hack_under_10m_medium() -> None:
    """Hack/exploit below $10M → MEDIUM impact."""
    text = "A small exploit drained $2M from a testnet faucet."
    assert assess_market_impact(text, ["eth"]) == MarketImpact.MEDIUM


def test_market_impact_listing_high() -> None:
    """CEX listing/delisting is HIGH impact."""
    assert assess_market_impact("Binance announces listing of new token", []) == MarketImpact.HIGH
    assert assess_market_impact("Coinbase to delist the token after review", []) == MarketImpact.HIGH


def test_market_impact_major_upgrade_high() -> None:
    """Major protocol upgrade / mainnet / halving → HIGH impact."""
    assert assess_market_impact("Ethereum mainnet upgrade goes live", []) == MarketImpact.HIGH
    assert assess_market_impact("Major protocol upgrade scheduled for Q3", []) == MarketImpact.HIGH
    assert assess_market_impact("Bitcoin halving set for next month", []) == MarketImpact.HIGH


def test_market_impact_minor_upgrade_medium() -> None:
    """Minor upgrade → MEDIUM impact."""
    assert assess_market_impact("Protocol ships minor upgrade with bug fixes", []) == MarketImpact.MEDIUM


def test_market_impact_partnership_medium() -> None:
    """Partnership / funding / regulatory comment → MEDIUM impact."""
    assert assess_market_impact("Exchange announces partnership with custody firm", []) == MarketImpact.MEDIUM
    assert assess_market_impact("Startup raises $15M seed round", []) == MarketImpact.MEDIUM


def test_market_impact_general_low() -> None:
    """General commentary / price predictions → LOW impact."""
    assert assess_market_impact("Analyst predicts ETH will rally in 2027", []) == MarketImpact.LOW
    assert assess_market_impact("Weekly market commentary roundup", []) == MarketImpact.LOW
    assert assess_market_impact("", []) == MarketImpact.LOW


# ── Entity extraction ────────────────────────────────────────────────────


def test_extract_entities_categorized() -> None:
    """Tokens, protocols, and people are extracted into separate lists."""
    text = "BTC and ETH rallied as Uniswap announced UNI staking; Vitalik Buterin commented on $SOL."
    entities = extract_entities(text)
    assert "btc" in entities["tokens"]
    assert "eth" in entities["tokens"]
    assert "sol" in entities["tokens"]  # via $SOL cashtag
    assert "uni" in entities["tokens"]
    assert "uniswap" in entities["protocols"]
    assert "vitalik buterin" in entities["people"]
    assert entities["tokens"] == sorted(set(entities["tokens"]), key=lambda x: entities["tokens"].index(x))  # deduped


def test_extract_entities_empty() -> None:
    """Empty text yields empty categories."""
    assert extract_entities("") == {"tokens": [], "protocols": [], "people": []}


# ── Sentiment ────────────────────────────────────────────────────────────


def test_sentiment_bullish() -> None:
    assert classify_sentiment("BTC surges to a record high on ETF approval inflows") == "BULLISH"


def test_sentiment_bearish() -> None:
    assert classify_sentiment("Ether crashes after exploit; outflows and liquidations") == "BEARISH"


def test_sentiment_neutral() -> None:
    assert classify_sentiment("The protocol published its monthly report") == "NEUTRAL"


# ── Timeliness ───────────────────────────────────────────────────────────


def test_timeliness_breaking_recent_archived() -> None:
    now = datetime.now(UTC)
    assert classify_timeliness((now - timedelta(minutes=10)).isoformat(), now=now) == "BREAKING"
    assert classify_timeliness((now - timedelta(hours=5)).isoformat(), now=now) == "RECENT"
    assert classify_timeliness((now - timedelta(days=3)).isoformat(), now=now) == "ARCHIVED"


def test_timeliness_unknown_is_recent() -> None:
    """Missing publish date fails open to RECENT."""
    assert classify_timeliness("") == "RECENT"
    assert classify_timeliness("not-a-date") == "RECENT"


# ── Confidence ───────────────────────────────────────────────────────────


def test_confidence_composition() -> None:
    """Established + corroborated + verified scores highest."""
    strong = ArticleRating(
        article_id="1",
        title="T",
        source="coindesk.com",
        factuality=Factuality.VERIFIED,
        source_reputation=SourceReputation.ESTABLISHED,
        corroborating_sources=3,
        on_chain_evidence=True,
    )
    weak = ArticleRating(
        article_id="2",
        title="T",
        source="randomblog.xyz",
        factuality=Factuality.RUMOR,
        source_reputation=SourceReputation.UNKNOWN,
        corroborating_sources=0,
    )
    assert compute_rating_confidence(strong) == 1.0
    assert compute_rating_confidence(weak) == 0.2
    assert compute_rating_confidence(strong) > compute_rating_confidence(weak)


# ── End-to-end rate_article ─────────────────────────────────────────────


def test_rate_article_full_card() -> None:
    """rate_article produces a fully populated rating card."""
    rating = rate_article(
        {
            "article_id": "abc123",
            "title": "SEC confirms spot Bitcoin ETF approved",
            "content": "The SEC confirmed the approval today. BTC surged to a record high. "
            "Vitalik Buterin commented on the news.",
            "source": "https://www.coindesk.com/markets/2026/08/03/sec-approves-etf/",
            "published": "2026-08-03T12:00:00Z",
            "corroborating_sources": 2,
            "on_chain_evidence": True,
        }
    )
    assert rating.article_id == "abc123"
    assert rating.title == "SEC confirms spot Bitcoin ETF approved"
    assert rating.factuality == Factuality.VERIFIED
    assert rating.source_reputation == SourceReputation.ESTABLISHED
    assert rating.market_impact == MarketImpact.HIGH
    assert rating.sentiment == "BULLISH"
    assert rating.timeliness == "RECENT"
    assert "btc" in rating.tokens_mentioned
    assert "vitalik" in rating.people_mentioned or "vitalik buterin" in rating.people_mentioned
    assert rating.corroborating_sources == 2
    assert rating.on_chain_evidence is True
    assert 0.0 <= rating.confidence <= 1.0


def test_rate_article_missing_fields_defaults() -> None:
    """Malformed/sparse articles never raise and get conservative defaults."""
    rating = rate_article({"title": "Just a headline"})
    assert rating.article_id == ""
    assert rating.source == ""
    assert rating.factuality == Factuality.REPORTED
    assert rating.source_reputation == SourceReputation.UNKNOWN
    assert rating.market_impact == MarketImpact.LOW
    assert rating.sentiment == "NEUTRAL"
    assert rating.timeliness == "RECENT"


def test_rate_article_uses_override_map() -> None:
    """A caller-supplied source map changes the reputation axis."""
    rating = rate_article(
        {
            "title": "Confirmed: upgrade ships next week",
            "content": "Team confirmed the upgrade timeline.",
            "source": "https://mysteryfeed.io/rss",
        },
        source_tier_map={"mysteryfeed.io": "established"},
    )
    assert rating.source_reputation == SourceReputation.ESTABLISHED
    assert rating.factuality == Factuality.VERIFIED


# ── Crawler wiring ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_crawl_and_ingest_attaches_rating() -> None:
    """crawl_and_ingest stores the rating card in ingested article metadata."""
    from app.rag.crawler import crawl_and_ingest

    source_url = "https://www.coindesk.com/markets/2026/08/03/sec-approves-etf/"
    dedup_result = SimpleNamespace(is_duplicate=False, level=None, group_id="grp:test", matched_doc_id=None, details={})
    ingest_result = {"doc_id": "crawl:abc123", "chunks": 4, "status": "ok"}

    with (
        patch("app.rag.engine.ingest_document", new=AsyncMock(return_value=ingest_result)) as ingest_mock,
        patch("app.rag.crawler.check_duplicates", new=AsyncMock(return_value=dedup_result)),
    ):
        result = await crawl_and_ingest(
            source_url,
            content="SEC confirmed the spot Bitcoin ETF approved today.",
            metadata={"source_url": source_url, "crawler": "crawl4ai", "title": "SEC confirms ETF"},
            published_at="2026-08-03T12:00:00Z",
        )

    assert result["status"] == "ok"
    metadata = ingest_mock.await_args.kwargs["metadata"]
    assert "rating" in metadata
    rating = metadata["rating"]
    assert rating["article_id"] == source_url
    assert rating["factuality"] == "verified"
    assert rating["source_reputation"] == "established"
    assert rating["market_impact"] == "high"
    assert "rating_confidence" in metadata
    assert 0.0 <= float(metadata["rating_confidence"]) <= 1.0
