#!/usr/bin/env python3
"""
RMI INTELLIGENCE FEED PIPELINE
==============================
Pulls crypto threat intelligence from RSS feeds and open APIs.
Runs continuously, indexing scam/hack/exploit data into RAG.

Feeds (verified working):
  - Web3IsGoingGreat (Molly White) - incident tracker RSS
  - SlowMist - blockchain security firm (Medium)
  - PeckShield - on-chain security monitor (via Nitter)
  - Blockworks - crypto news
  - Cointelegraph Security - tagged articles

Additional sources:
  - Helius webhooks - new Solana tokens
  - GoPlus API - real-time token security checks
  - On-chain scanning - new token → quick risk assessment

Architecture:
  Prometheus → every N minutes pull RSS → extract entities → embed → store
  New token event → quick keyword scan → if suspicious → deep embed → alert
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import feedparser
import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)

# ══════════════════════════════════════════════════════════════════════
# CONFIGURED FEEDS
# ══════════════════════════════════════════════════════════════════════

FEEDS = [
    {
        "name": "web3isgoinggreat",
        "url": "https://www.web3isgoinggreat.com/feed",
        "category": "incident",
        "severity": "high",
        "extractor": "rss",
        "enabled": True,
    },
    {
        "name": "slowmist",
        "url": "https://slowmist.medium.com/feed",
        "category": "hack_report",
        "severity": "critical",
        "extractor": "rss",
        "enabled": True,
    },
    {
        "name": "peckshield",
        "url": "https://peckshield.medium.com/feed",
        "category": "security_alert",
        "severity": "high",
        "extractor": "rss",
        "enabled": True,
    },
    {
        "name": "certik",
        "url": "https://certik.medium.com/feed",
        "category": "audit_report",
        "severity": "high",
        "extractor": "rss",
        "enabled": True,
    },
    {
        "name": "immunefi",
        "url": "https://immunefi.medium.com/feed",
        "category": "bounty_report",
        "severity": "critical",
        "extractor": "rss",
        "enabled": True,
    },
    {
        "name": "trailofbits",
        "url": "https://blog.trailofbits.com/feed/",
        "category": "security_research",
        "severity": "high",
        "extractor": "rss",
        "enabled": True,
    },
    {
        "name": "cryptosecurity",
        "url": "https://cryptosecurity.substack.com/feed",
        "category": "security_news",
        "severity": "medium",
        "extractor": "rss",
        "enabled": True,
    },
    {
        "name": "blockworks",
        "url": "https://blockworks.co/feed",
        "category": "crypto_news",
        "severity": "medium",
        "extractor": "rss",
        "enabled": True,
        "keywords": ["hack", "exploit", "scam", "rug", "drain", "attack", "breach", "stolen"],
    },
    {
        "name": "cointelegraph_security",
        "url": "https://cointelegraph.com/rss/tag/security",
        "category": "security_news",
        "severity": "medium",
        "extractor": "rss",
        "enabled": True,
    },
    {
        "name": "chainalysis",
        "url": "https://blog.chainalysis.com/feed/",
        "category": "forensic_report",
        "severity": "high",
        "extractor": "rss",
        "enabled": True,
        "keywords": ["scam", "ransomware", "sanction", "illicit", "money laundering"],
    },
    {
        "name": "cointelegraph",
        "url": "https://cointelegraph.com/rss",
        "category": "crypto_news",
        "severity": "low",
        "extractor": "rss",
        "enabled": True,
        "keywords": [
            "hack",
            "exploit",
            "scam",
            "rug",
            "drain",
            "attack",
            "breach",
            "stolen",
            "phishing",
            "theft",
        ],
    },
    {
        "name": "decrypt",
        "url": "https://decrypt.co/feed",
        "category": "crypto_news",
        "severity": "low",
        "extractor": "rss",
        "enabled": True,
        "keywords": [
            "hack",
            "exploit",
            "scam",
            "rug",
            "drain",
            "stolen",
            "theft",
            "honeypot",
            "phishing",
        ],
    },
    {
        "name": "theblock",
        "url": "https://www.theblock.co/rss.xml",
        "category": "crypto_news",
        "severity": "low",
        "extractor": "rss",
        "enabled": True,
        "keywords": ["hack", "exploit", "scam", "rug", "drain", "stolen"],
    },
    {
        "name": "bankless",
        "url": "https://www.bankless.com/feed",
        "category": "crypto_news",
        "severity": "low",
        "extractor": "rss",
        "enabled": True,
        "keywords": ["hack", "exploit", "scam", "rug", "drain", "attack"],
    },
    {
        "name": "thedefiant",
        "url": "https://thedefiant.io/feed",
        "category": "defi_news",
        "severity": "medium",
        "extractor": "rss",
        "enabled": True,
        "keywords": ["hack", "exploit", "scam", "rug", "drain", "attack", "breach"],
    },
]

# Entities to extract from articles
ENTITY_PATTERNS = {
    "eth_address": r"0x[a-fA-F0-9]{40}",
    "sol_address": r"[1-9A-HJ-NP-Za-km-z]{32,44}",
    "btc_address": r"[13][a-km-zA-HJ-NP-Z1-9]{25,34}",
    "tornado_cash": r"(?i)tornado\s*cash",
    "amount_usd": r"\$[\d,]+(?:\s*(?:million|billion|M|B|K))?",
    "protocol_name": r"(?<=the\s)(?:Uniswap|Aave|Compound|Curve|Balancer|Sushi|Pancake|Raydium|Orca|Jupiter|Marinade)(?=\s)",
}

# ══════════════════════════════════════════════════════════════════════
# FEED PROCESSORS
# ══════════════════════════════════════════════════════════════════════


@dataclass
class IntelItem:
    id: str
    title: str
    content: str
    source: str
    url: str
    published: str
    category: str
    severity: str
    entities: dict[str, list[str]] = field(default_factory=dict)
    raw: dict = field(default_factory=dict)


class RSSProcessor:
    """Process standard RSS/Atom feeds into IntelItems."""

    @staticmethod
    async def fetch(feed_config: dict) -> list[IntelItem]:
        items = []
        try:
            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                resp = await client.get(
                    feed_config["url"],
                    headers={"User-Agent": "RMI-ThreatIntel/1.0 (crypto security research)"},
                )
                resp.raise_for_status()
                feed = feedparser.parse(resp.text)

            keywords = feed_config.get("keywords", [])

            for entry in feed.entries[:20]:  # Max 20 per feed per run
                title = entry.get("title", "")
                summary = entry.get("summary", entry.get("description", ""))
                content = f"{title}\n\n{html.escape(summary)[:2000]}"

                # Keyword filter
                if keywords:
                    text_lower = (title + " " + summary).lower()
                    if not any(k.lower() in text_lower for k in keywords):
                        continue

                # Extract entities
                entities = RSSProcessor._extract_entities(content)

                item_id = hashlib.sha256(f"{feed_config['name']}:{entry.get('link', title)}".encode()).hexdigest()[:16]

                items.append(
                    IntelItem(
                        id=item_id,
                        title=title[:300],
                        content=content[:3000],
                        source=feed_config["name"],
                        url=entry.get("link", ""),
                        published=entry.get("published", entry.get("updated", "")),
                        category=feed_config["category"],
                        severity=feed_config["severity"],
                        entities=entities,
                        raw={"feed_title": feed.get("feed", {}).get("title", "")},
                    )
                )

        except Exception as e:
            logger.warning(f"RSS fetch failed for {feed_config['name']}: {e}")

        return items

    @staticmethod
    def _extract_entities(text: str) -> dict[str, list[str]]:
        found = {}
        for entity_type, pattern in ENTITY_PATTERNS.items():
            matches = re.findall(pattern, text)
            if matches:
                found[entity_type] = list(set(matches))[:10]
        return found


class NitterProcessor(RSSProcessor):
    """Process Nitter (Twitter mirror) RSS feeds."""

    @staticmethod
    async def fetch(feed_config: dict) -> list[IntelItem]:
        items = await RSSProcessor.fetch(feed_config)
        # Clean up Nitter-specific formatting
        for item in items:
            # Extract tweet content from title (format: "user: tweet text")
            if ":" in item.title:
                parts = item.title.split(":", 1)
                item.title = parts[1].strip()[:300]
            # Clean HTML entities
            item.content = html.unescape(item.content)
        return items


# ══════════════════════════════════════════════════════════════════════
# ON-CHAIN SCANNER
# ══════════════════════════════════════════════════════════════════════


class OnChainScanner:
    """Scan new tokens on Solana/Ethereum for immediate risk assessment."""

    @staticmethod
    async def scan_new_solana_tokens(limit: int = 20) -> list[IntelItem]:
        """Scan recently deployed Solana tokens via Helius/Birdeye."""
        items = []
        try:
            # Use Birdeye new listing API or Helius webhook data
            async with httpx.AsyncClient(timeout=30) as client:
                # Birdeye trending/new tokens
                resp = await client.get(
                    "https://public-api.birdeye.so/defi/tokenlist",
                    params={
                        "sort_by": "v24hChangePercent",
                        "sort_type": "desc",
                        "limit": str(limit),
                    },
                    headers={
                        "X-API-KEY": os.getenv("BIRDEYE_API_KEY", ""),
                        "User-Agent": "RMI-Scanner/1.0",
                    },
                )
                if resp.status_code == 200:
                    data = resp.json()
                    tokens = data.get("data", {}).get("tokens", [])
                    for token in tokens:
                        addr = token.get("address", "")
                        name = token.get("name", "")
                        symbol = token.get("symbol", "")
                        change = token.get("v24hChangePercent", 0)

                        # Quick risk heuristics
                        risk_signals = []
                        if abs(change) > 500:
                            risk_signals.append("extreme_volatility")
                        if not name or len(name) < 3:
                            risk_signals.append("suspicious_name")
                        if float(token.get("liquidity", 0)) < 5000:
                            risk_signals.append("low_liquidity")

                        if risk_signals:
                            item_id = hashlib.sha256(f"solana_new:{addr}".encode()).hexdigest()[:16]
                            items.append(
                                IntelItem(
                                    id=item_id,
                                    title=f"New token alert: {name} ({symbol})",
                                    content=f"Token {name} ({symbol}) on Solana. Address: {addr}. "
                                    f"24h change: {change}%. Risk signals: {', '.join(risk_signals)}. "
                                    f"Liquidity: ${token.get('liquidity', 0):,.0f}. Volume 24h: ${token.get('v24hUSD', 0):,.0f}.",
                                    source="onchain_scanner",
                                    url=f"https://birdeye.so/token/{addr}?chain=solana",
                                    published=datetime.now(UTC).isoformat(),
                                    category="new_token",
                                    severity="medium" if len(risk_signals) < 2 else "high",
                                    entities={"sol_address": [addr]},
                                )
                            )
        except Exception as e:
            logger.warning(f"Solana scan failed: {e}")

        return items

    @staticmethod
    async def check_token_security(address: str, chain: str = "solana") -> dict[str, Any]:
        """Check a token via GoPlus security API (free, no key needed)."""
        try:
            chain_id = {"solana": "solana", "ethereum": "1", "bsc": "56", "base": "8453"}.get(chain, chain)
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    f"https://api.gopluslabs.io/api/v1/token_security/{chain_id}",
                    params={"contract_addresses": address},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    return data.get("result", {}).get(address.lower(), {})
        except httpx.HTTPError:
            pass
        return {}


# ══════════════════════════════════════════════════════════════════════
# INTELLIGENCE PIPELINE ORCHESTRATOR
# ══════════════════════════════════════════════════════════════════════


class IntelPipeline:
    """
    Continuous intelligence ingestion pipeline.

    Usage:
        pipeline = IntelPipeline()
        stats = await pipeline.run_cycle()
    """

    def __init__(self) -> None:
        self._last_run: datetime | None = None
        self._total_ingested = 0
        self._seen_ids: set[str] = set()
        self._feed_processors = {
            "rss": RSSProcessor,
            "nitter_rss": NitterProcessor,
        }

    async def run_cycle(self) -> dict[str, Any]:
        """Run one full ingestion cycle across all sources."""
        from app.crypto_embeddings import get_embedder
        from app.rag.compat import _get_redis

        embedder = await get_embedder()
        r = await _get_redis()
        now = datetime.now(UTC)
        stats = {"feeds": {}, "onchain": {}, "total": 0, "skipped": 0, "errors": 0}

        # Phase 1: RSS feeds
        for feed_config in FEEDS:
            if not feed_config.get("enabled", True):
                continue

            try:
                processor_class = self._feed_processors.get(feed_config["extractor"], RSSProcessor)
                items = await processor_class.fetch(feed_config)

                ingested = 0
                for item in items:
                    if item.id in self._seen_ids:
                        stats["skipped"] += 1
                        continue

                    try:
                        # Embed the content
                        result = await embedder.embed_scam_pattern(
                            pattern_name=item.title,
                            description=item.content,
                            severity=item.severity,
                        )

                        # Store in Redis
                        doc = {
                            "id": item.id,
                            "collection": "market_intel",
                            "vector": result.vector,
                            "dims": result.dims,
                            "model": result.model,
                            "metadata": {
                                "source": item.source,
                                "url": item.url,
                                "published": item.published,
                                "category": item.category,
                                "severity": item.severity,
                                "entities": item.entities,
                            },
                            "content": item.content,
                            "source": item.source,
                            "severity": item.severity,
                            "stored_at": now.isoformat(),
                        }
                        await r.setex(
                            f"rag:market_intel:{item.id}",
                            86400 * 7,  # 7-day TTL for news
                            json.dumps(doc),
                        )
                        await r.sadd("rag:idx:market_intel", item.id)
                        self._seen_ids.add(item.id)
                        ingested += 1

                    except Exception as e:
                        logger.error(f"Failed to ingest {item.id}: {e}")

                stats["feeds"][feed_config["name"]] = ingested
                stats["total"] += ingested

            except Exception as e:
                logger.error(f"Feed {feed_config['name']} failed: {e}")
                stats["errors"] += 1
                stats["feeds"][feed_config["name"]] = 0

        # Phase 2: On-chain scanning
        try:
            new_tokens = await OnChainScanner.scan_new_solana_tokens(limit=10)
            ingested = 0
            for item in new_tokens:
                if item.id in self._seen_ids:
                    continue
                try:
                    result = await embedder.embed_scam_pattern(
                        pattern_name=item.title,
                        description=item.content,
                        severity=item.severity,
                    )
                    doc = {
                        "id": item.id,
                        "collection": "token_analysis",
                        "vector": result.vector,
                        "dims": result.dims,
                        "metadata": {
                            "source": item.source,
                            "category": item.category,
                            "severity": item.severity,
                            "entities": item.entities,
                        },
                        "content": item.content,
                        "source": item.source,
                        "severity": item.severity,
                        "stored_at": now.isoformat(),
                    }
                    await r.setex(f"rag:token_analysis:{item.id}", 86400 * 7, json.dumps(doc))
                    await r.sadd("rag:idx:token_analysis", item.id)
                    self._seen_ids.add(item.id)
                    ingested += 1
                except Exception as e:
                    logger.error(f"Failed to ingest onchain {item.id}: {e}")

            stats["onchain"]["new_tokens"] = ingested
            stats["total"] += ingested
        except Exception as e:
            logger.warning(f"On-chain scan failed: {e}")

        self._last_run = now
        self._total_ingested += stats["total"]

        # Prune seen_ids to prevent OOM (unbounded growth)
        MAX_SEEN_IDS = 50000
        if len(self._seen_ids) > MAX_SEEN_IDS:
            self._seen_ids = set(list(self._seen_ids)[-MAX_SEEN_IDS // 2 :])
            logger.info("pruned_seen_ids", count=len(self._seen_ids))

        # Cleanup old entries (older than 7 days)
        try:
            old_cutoff = (now - timedelta(days=7)).isoformat()
            for coll in ["market_intel", "token_analysis"]:
                all_ids = await r.smembers(f"rag:idx:{coll}")
                for did in all_ids:
                    doc_raw = await r.get(f"rag:{coll}:{did}")
                    if doc_raw:
                        try:
                            doc = json.loads(doc_raw)
                            if doc.get("stored_at", "") < old_cutoff:
                                await r.delete(f"rag:{coll}:{did}")
                                await r.srem(f"rag:idx:{coll}", did)
                        except json.JSONDecodeError:
                            pass
        except json.JSONDecodeError:
            pass

        return {
            "status": "completed",
            "total_ingested": stats["total"],
            "cumulative": self._total_ingested,
            "feeds": stats["feeds"],
            "onchain": stats["onchain"],
            "skipped": stats["skipped"],
            "errors": stats["errors"],
            "seen_cache_size": len(self._seen_ids),
            "timestamp": now.isoformat(),
            "next_run_in": "30 minutes",
        }

    async def get_latest_intel(self, limit: int = 20) -> list[dict[str, Any]]:
        """Get latest threat intelligence for frontend display."""
        from app.rag.compat import _get_redis

        r = await _get_redis()

        results = []
        for coll in ["market_intel", "token_analysis", "known_scams"]:
            doc_ids = await r.smembers(f"rag:idx:{coll}")
            for did in list(doc_ids)[:limit]:
                raw = await r.get(f"rag:{coll}:{did}")
                if raw:
                    try:
                        doc = json.loads(raw)
                        results.append(
                            {
                                "id": doc["id"],
                                "title": doc.get("metadata", {}).get("name", doc.get("content", "")[:100]),
                                "content": doc.get("content", "")[:500],
                                "source": doc.get("source", coll),
                                "severity": doc.get("severity", "medium"),
                                "category": doc.get("metadata", {}).get("category", ""),
                                "url": doc.get("metadata", {}).get("url", ""),
                                "published": doc.get("metadata", {}).get("published", ""),
                                "entities": doc.get("metadata", {}).get("entities", {}),
                                "stored_at": doc.get("stored_at", ""),
                            }
                        )
                    except (ConnectionError, OSError, TimeoutError):
                        pass

        results.sort(key=lambda x: x.get("stored_at", ""), reverse=True)
        return results[:limit]


# ══════════════════════════════════════════════════════════════════════
# SINGLETON
# ══════════════════════════════════════════════════════════════════════

_pipeline: IntelPipeline | None = None


async def get_pipeline() -> IntelPipeline:
    global _pipeline
    if _pipeline is None:
        _pipeline = IntelPipeline()
    return _pipeline
