"""
RAG Firehose - Feed Sources
============================
All data sources the firehose can pull from. Each method pulls from
a different API/data source and returns formatted documents ready for
the ingestion pipeline.
"""

from __future__ import annotations

import csv
import os
from datetime import UTC, datetime

import httpx

from app.core.logging import get_logger

logger = get_logger("rag.firehose")

class FeedSources:
    """All data sources the firehose can pull from."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client

    async def pull_news_rss(self) -> list[dict]:
        """Pull latest news from DataBus news provider (200+ RSS feeds)."""
        try:
            resp = await self.client.get(
                "http://localhost:8000/api/v1/databus/fetch/news",
                params={"limit": 30},
                timeout=30,
            )
            if resp.status_code != 200:
                return []
            data = resp.json()
            articles = data.get("articles", data.get("data", []))

            docs = []
            for article in (articles if isinstance(articles, list) else [])[:20]:
                title = article.get("title", "")
                desc = article.get("description", article.get("summary", ""))
                if not title:
                    continue
                content = f"News: {title}. {desc}"
                docs.append(
                    {
                        "collection": "news_articles",
                        "content": content[:2000],
                        "metadata": {
                            "source": article.get("source", "news_rss"),
                            "url": article.get("url", ""),
                            "published": article.get("published_at", article.get("date", "")),
                            "category": article.get("category", "crypto"),
                            "title": title,
                        },
                    }
                )
            return docs
        except Exception as e:
            logger.warning(f"News RSS pull failed: {e}")
            return []

    async def pull_ct_rundown(self) -> list[dict]:
        """Pull CT Rundown stories."""
        try:
            resp = await self.client.get(
                "http://localhost:8000/api/v1/databus/fetch/ct_rundown",
                params={"limit": 10},
                timeout=30,
            )
            if resp.status_code != 200:
                return []
            data = resp.json()
            stories = data.get("stories", data.get("data", []))

            docs = []
            for story in (stories if isinstance(stories, list) else [])[:5]:
                content = f"CT: {story.get('title', '')} - {story.get('summary', '')}"
                docs.append(
                    {
                        "collection": "news_articles",
                        "content": content[:1500],
                        "metadata": {
                            "source": "ct_rundown",
                            "category": "crypto_twitter",
                            "handle": story.get("handle", ""),
                            "engagement": story.get("engagement", 0),
                        },
                    }
                )
            return docs
        except Exception as e:
            logger.warning(f"CT rundown pull failed: {e}")
            return []

    async def pull_social_sentiment(self) -> list[dict]:
        """Pull social sentiment and scam alerts."""
        docs = []
        try:
            # Scam monitor
            resp = await self.client.get(
                "http://localhost:8000/api/v1/databus/fetch/scam_monitor", timeout=20
            )
            if resp.status_code == 200:
                data = resp.json()
                alerts = data.get("alerts", data.get("data", []))
                for alert in (alerts if isinstance(alerts, list) else [])[:10]:
                    docs.append(
                        {
                            "collection": "social_sentiment",
                            "content": f"Scam alert: {alert.get('title', '')} - {alert.get('description', '')}",
                            "metadata": {
                                "source": "scam_monitor",
                                "severity": alert.get("severity", "medium"),
                                "token": alert.get("token_address", ""),
                                "chain": alert.get("chain", ""),
                            },
                        }
                    )

            # Social metrics
            resp2 = await self.client.get(
                "http://localhost:8000/api/v1/databus/fetch/social_metrics", timeout=20
            )
            if resp2.status_code == 200:
                data2 = resp2.json()
                metrics = data2.get("metrics", data2.get("data", {}))
                if metrics:
                    docs.append(
                        {
                            "collection": "social_sentiment",
                            "content": f"Social metrics: Sentiment {metrics.get('sentiment', '?')}, "
                            f"Trending: {metrics.get('trending_topics', [])}",
                            "metadata": {"source": "social_metrics", "type": "daily_summary"},
                        }
                    )
        except Exception as e:
            logger.warning(f"Social pull failed: {e}")

        return docs

    async def pull_etherscan_labels(self) -> list[dict]:
        """Pull etherscan labeled addresses (via existing CSV)."""
        docs = []
        csv_path = os.path.join(os.path.dirname(__file__), "..", "data", "etherscan_phish_hack.csv")
        if not os.path.exists(csv_path):
            return docs

        try:
            with open(csv_path, newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))

            for row in rows:
                addr = row.get("address", "").strip()
                if not addr:
                    continue
                content = (
                    f"Etherscan label: {addr} on {row.get('chain', 'Ethereum')}. "
                    f"Tag: {row.get('name_tag', '')}. Type: {row.get('label_type', 'scam')}."
                )
                docs.append(
                    {
                        "collection": "known_scams",
                        "content": content,
                        "metadata": {
                            "address": addr.lower(),
                            "chain": (row.get("chain", "Ethereum") or "Ethereum").lower(),
                            "label_type": row.get("label_type", "scam"),
                            "source": "etherscan",
                            "severity": "critical"
                            if "phish" in row.get("label_type", "").lower()
                            else "high",
                        },
                    }
                )
        except Exception as e:
            logger.warning(f"Etherscan pull failed: {e}")

        return docs

    async def pull_solana_scams(self) -> list[dict]:
        """Pull Solana token registry flagged tokens."""
        docs = []
        try:
            resp = await self.client.get(
                "https://raw.githubusercontent.com/solana-labs/token-list/main/src/tokens/solana.tokenlist.json",
                timeout=30,
            )
            if resp.status_code == 200:
                data = resp.json()
                for token in data.get("tokens", []):
                    tags = [t.lower() for t in token.get("tags", [])]
                    if any(kw in str(tags) for kw in ["scam", "spam", "fake"]):
                        docs.append(
                            {
                                "collection": "known_scams",
                                "content": f"Solana scam token: {token.get('name', '')} ({token.get('symbol', '')}) "
                                f"at {token.get('address', '')}. Tags: {tags}.",
                                "metadata": {
                                    "address": token.get("address", ""),
                                    "name": token.get("name", ""),
                                    "symbol": token.get("symbol", ""),
                                    "chain": "solana",
                                    "source": "solana_token_registry",
                                    "tags": tags,
                                    "severity": "high",
                                },
                            }
                        )
        except Exception as e:
            logger.warning(f"Solana pull failed: {e}")

        return docs

    async def pull_prediction_markets(self) -> list[dict]:
        """Pull Polymarket prediction data for market intel."""
        docs = []
        try:
            resp = await self.client.get(
                "http://localhost:8000/api/v1/databus/fetch/prediction_markets", timeout=20
            )
            if resp.status_code == 200:
                data = resp.json()
                markets = data.get("markets", data.get("data", []))
                for m in (markets if isinstance(markets, list) else [])[:10]:
                    docs.append(
                        {
                            "collection": "market_intel",
                            "content": f"Prediction market: {m.get('question', '')} - "
                            f"YES: {m.get('yes_price', '?')} NO: {m.get('no_price', '?')}",
                            "metadata": {"source": "polymarket", "type": "prediction"},
                        }
                    )
        except Exception as e:
            logger.warning(f"Prediction market pull failed: {e}")

        return docs

    async def extract_scam_patterns(self) -> list[dict]:
        """Extract common patterns from confirmed scam documents."""
        docs = []
        try:
            resp = await self.client.get(
                f"{RAG_API}/search",
                params={
                    "q": "rug pull honeypot scam confirmed",
                    "collection": "known_scams",
                    "limit": 50,
                },
                timeout=30,
            )
            if resp.status_code == 200:
                data = resp.json()
                results = data.get("results", [])
                if len(results) >= 10:
                    content_parts = []
                    for r in results[:20]:
                        c = r.get("content", "")[:200]
                        if c:
                            content_parts.append(c)

                    combined = "Common scam patterns observed: " + " | ".join(content_parts)
                    docs.append(
                        {
                            "collection": "scam_patterns",
                            "content": combined[:5000],
                            "metadata": {
                                "source": "pattern_extraction",
                                "pattern_count": len(results),
                                "extracted_at": datetime.now(UTC).isoformat(),
                            },
                        }
                    )
        except Exception as e:
            logger.warning(f"Pattern extraction failed: {e}")

        return docs
