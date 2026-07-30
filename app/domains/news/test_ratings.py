"""Unit tests for article rating system."""

from __future__ import annotations

from app.domains.news.ratings import (
    SOURCE_CREDIBILITY,
    analyze_sentiment_score,
    enrich_article,
    rate_article,
)


def test_source_credibility_has_50_plus_entries() -> None:
    """Verify we have 50+ sources in the credibility database."""
    assert len(SOURCE_CREDIBILITY) >= 50, f"Expected 50+ sources, got {len(SOURCE_CREDIBILITY)}"


def test_rate_article_tier1_source() -> None:
    """Tier 1 sources get high confidence and factual rating."""
    rating = rate_article(
        title="Bitcoin Surges to $100K",
        content="Bitcoin reached a new all-time high",
        source="CoinDesk",
    )
    assert rating.source_tier == 1
    assert rating.factual_rating in ("high", "medium")
    assert rating.confidence >= 0.8


def test_rate_article_tier3_source() -> None:
    """Tier 3 sources get lower confidence."""
    rating = rate_article(
        title="New Crypto Token Will Moon",
        content="This token is going to the moon",
        source="Reddit",
    )
    assert rating.source_tier == 3
    assert rating.confidence < 0.6


def test_sentiment_bullish() -> None:
    """Bullish keywords produce positive sentiment score."""
    score, label = analyze_sentiment_score("Bitcoin surges to new ATH! Major breakout rally incoming.")
    assert score > 0
    assert label in ("bullish", "slightly_bullish")


def test_sentiment_bearish() -> None:
    """Bearish keywords produce negative sentiment score."""
    score, label = analyze_sentiment_score("Crypto market crashes! Major exploit drains millions.")
    assert score < 0
    assert label in ("bearish", "slightly_bearish")


def test_sentiment_neutral() -> None:
    """Neutral text produces near-zero score."""
    score, label = analyze_sentiment_score("The committee will meet next week to discuss the proposal.")
    assert -10 < score < 10
    assert label == "neutral"


def test_enrich_article_adds_rating_fields() -> None:
    """enrich_article adds all rating fields to an article dict."""
    article = {
        "title": "Ethereum Layer-2 Activity Hits All-Time High",
        "source": "The Block",
        "excerpt": "Ethereum layer-2 networks reached a new all-time high in daily active addresses.",
    }
    enriched = enrich_article(article)
    assert "bias" in enriched
    assert "sentiment_score" in enriched
    assert "sentiment_label" in enriched
    assert "factual_rating" in enriched
    assert "source_tier" in enriched
    assert "rating_confidence" in enriched
    assert enriched["bias"] in ("left", "center-left", "center", "center-right", "right")
    assert -100 <= enriched["sentiment_score"] <= 100


def test_enrich_article_unknown_source() -> None:
    """Unknown sources get default ratings."""
    article = {
        "title": "Something happened in crypto",
        "source": "SomeRandomBlog",
        "summary": "No one knows what happened exactly.",
    }
    enriched = enrich_article(article)
    assert enriched["bias"] == "center"
    assert enriched["source_tier"] == 3
    assert enriched["factual_rating"] in ("unrated", "low")


def test_bullish_vs_bearish_scoring() -> None:
    """Very bullish text should score higher than slightly bullish."""
    bullish_score, _ = analyze_sentiment_score("Bitcoin SURGES to new ATH! Massive BREAKOUT! Record highs! Moon!")
    mild_score, _ = analyze_sentiment_score("Bitcoin saw some positive movement today.")
    assert bullish_score >= 30  # should be strongly bullish
    assert 0 < mild_score < 30  # should be slightly bullish


def test_unknown_source_falls_back_to_tier3() -> None:
    """rate_article with no source match uses fallback tier."""
    rating = rate_article(
        title="Crypto news",
        source="totally-unknown-source-12345",
    )
    assert rating.source_tier in (2, 3)
    assert rating.confidence <= 0.5
