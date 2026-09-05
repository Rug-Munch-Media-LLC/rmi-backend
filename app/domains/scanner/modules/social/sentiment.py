"""Social sentiment check — LunarCrush integration + news fallback.

Split from social.py for maintainability.
Functions:
    _check_social_sentiment: Check social sentiment via LunarCrush.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.domains.scanner.modules._core import logger


async def _check_social_sentiment(token_address: str, chain: str, symbol: str = "") -> dict[str, Any] | None:
    """Check social sentiment — LunarCrush volume, dominance, sentiment score.

    Flags suspicious patterns:
      - High social volume but negative sentiment (paid shills)
      - Very low social volume for a "popular" token (bot-like)
      - Sudden spike in mentions without organic follow-through
    """
    result: dict[str, Any] = {
        "check": "social_sentiment",
        "result": "scanned",
        "social_volume": 0,
        "social_dominance": 0,
        "sentiment_score": 0,
        "mentions_24h": 0,
        "is_suspicious": False,
        "suspicion_reasons": [],
    }

    # Try LunarCrush via the existing connector
    try:
        from app.lunarcrush_connector import get_lunarcrush_connector

        lc = get_lunarcrush_connector()
        sentiment = await lc.get_sentiment(token_address)
        if sentiment:
            result["social_volume"] = sentiment.get("social_volume", 0)
            result["social_dominance"] = sentiment.get("social_dominance", 0)
            result["sentiment_score"] = sentiment.get("sentiment_score", 0)
            result["mentions_24h"] = sentiment.get("mentions_24h", 0)
            result["mentions_change_24h"] = sentiment.get("mentions_change_24h", 0)
            result["source"] = "lunarcrush"

            # Suspicion detection
            volume = result["social_volume"]
            mentions = result["mentions_24h"]
            sentiment_score = result["sentiment_score"]

            # High volume + negative sentiment = paid shills
            if volume > 100 and sentiment_score < -0.3:
                result["is_suspicious"] = True
                result["suspicion_reasons"].append("high_volume_negative_sentiment")

            # Very low mentions for a token with volume
            if mentions < 5 and volume > 10:
                result["is_suspicious"] = True
                result["suspicion_reasons"].append("low_mentions_relative_to_volume")

            # Extreme positive sentiment with no volume (bot-like)
            if sentiment_score > 0.8 and volume < 5:
                result["is_suspicious"] = True
                result["suspicion_reasons"].append("extreme_sentiment_no_volume")

    except ImportError:
        logger.debug("lunarcrush_connector_not_available")
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        logger.debug("social_sentiment_failed", error=str(e)[:80])

    # Fallback: try RMI news sentiment endpoint for signal
    if not result.get("source"):
        try:
            from app.domains.x402.tools.news_tools import get_news_sentiment

            news_data = await get_news_sentiment(token_address, chain=chain)
            if news_data and news_data.get("articles", 0) > 0:
                result["news_articles"] = news_data.get("articles", 0)
                result["news_sentiment"] = news_data.get("sentiment", "neutral")
                result["source"] = "news_fallback"
        except ImportError as e:
            logger.debug(f"_check_social_sentiment failed: {e}")
        except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
            logger.debug("news_sentiment_fallback_failed", error=str(e)[:60])

    # Score: suspicious activity adds risk
    risk_score = 0
    if result["is_suspicious"]:
        risk_score += 25 * len(result["suspicion_reasons"])
    result["risk_score"] = min(100, risk_score)

    return result
