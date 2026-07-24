#!/usr/bin/env python3
"""
TIER-1 SCAM INGESTION - Automated Knowledge Base Builder
=========================================================
Pulls scam/exploit data from public sources and indexes them.

Sources:
  - REKT News DB (rekt.news) - largest crypto exploit database
  - Chainabuse - community-reported scams
  - GoPlus Security API - real-time token risk assessment
  - QuickIntel API - honeypot/rug detection
  - TokenSniffer - scam detection scores
  - Web3IsGoingGreat - incident timeline
  - SlowMist Hacked - investigation reports
  - CertiK Alerts - audit findings

Architecture:
  1. Periodically poll sources (configurable intervals)
  2. Extract structured data (name, description, contract, txns, losses)
  3. Embed using CryptoEmbedder
  4. Store in pgvector + Redis (dual-write)
  5. Trigger re-scan of related tokens/wallets
"""
from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime
from typing import Any

import feedparser
import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)

# ══════════════════════════════════════════════════════════════════════
# SOURCE CONNECTORS
# ══════════════════════════════════════════════════════════════════════

# RSS feeds from security sources - verified working May 2026
SECURITY_RSS_FEEDS = [
    ("https://www.web3isgoinggreat.com/feed", "W3IGG"),
    ("https://slowmist.medium.com/feed", "SlowMist"),
    ("https://peckshield.medium.com/feed", "PeckShield"),
    ("https://certik.medium.com/feed", "CertiK"),
    ("https://immunefi.medium.com/feed", "Immunefi"),
    ("https://blog.trailofbits.com/feed/", "TrailOfBits"),
    ("https://cryptosecurity.substack.com/feed", "CryptoSecurity"),
    ("https://cointelegraph.com/rss/tag/security", "CoinTelegraph-Security"),
    ("https://www.chainalysis.com/blog/feed/", "Chainalysis"),
]

# Scam-adjacent URLs for scraping (no API, no RSS - use HTML parsing)
SCRAPE_SOURCES = [
    {
        "name": "chainabuse",
        "url": "https://chainabuse.com/reports",
        "selector": "a[href*='/report/']",
    },
    {
        "name": "rekt",
        "url": "https://rekt.news/",
        "selector": "article a",
    },
]

class RSSFeedConnector:
    """Generic RSS/Atom feed connector for security feeds."""

    @staticmethod
    async def fetch_feed(url: str, source_name: str, limit: int = 20) -> list[dict]:
        results = []
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
                resp = await client.get(url, headers={"User-Agent": "RMI-Scam-Indexer/1.0"})
                if resp.status_code != 200:
                    logger.debug(f"RSS {source_name}: HTTP {resp.status_code}")
                    return results

            feed = feedparser.parse(resp.text)
            if not feed.entries:
                logger.debug(f"RSS {source_name}: no entries found")
                return results

            for entry in feed.entries[:limit]:
                title = entry.get("title", "")
                link = entry.get("link", "")
                desc = entry.get("summary", "") or entry.get("description", "") or title

                # Parse date
                pub_parsed = entry.get("published_parsed") or entry.get("updated_parsed")
                if pub_parsed:
                    published = datetime(*pub_parsed[:6], tzinfo=UTC).isoformat()
                else:
                    published = datetime.now(UTC).isoformat()

                results.append(
                    {
                        "source": source_name.lower().replace(" ", "_"),
                        "type": "security_feed",
                        "name": title,
                        "description": desc[:500],
                        "date": published,
                        "url": link,
                        "category": "security_intel",
                    }
                )

            logger.info(f"RSS {source_name}: {len(results)} articles")
        except Exception as e:
            logger.warning(f"RSS {source_name} failed: {e}")
        return results

class GoPlusConnector:
    """GoPlus Security API - real-time token risk detection.
    Free tier: rate limited but no API key needed for basic checks."""

    BASE = "https://api.gopluslabs.io/api/v1"

    @staticmethod
    async def check_token(chain_id: str, address: str) -> dict:
        """Check a single token for security risks."""
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(
                    f"{GoPlusConnector.BASE}/token_security/{chain_id}",
                    params={"contract_addresses": address},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    result = data.get("result", {})
                    token_data = result.get(address.lower(), {})
                    return {
                        "source": "goplus",
                        "type": "token_scan",
                        "address": address,
                        "chain": chain_id,
                        "is_honeypot": token_data.get("is_honeypot") == "1",
                        "is_open_source": token_data.get("is_open_source") == "1",
                        "buy_tax": token_data.get("buy_tax", "0"),
                        "sell_tax": token_data.get("sell_tax", "0"),
                        "can_take_back_ownership": token_data.get("can_take_back_ownership") == "1",
                        "hidden_owner": token_data.get("hidden_owner") == "1",
                        "is_blacklisted": token_data.get("is_blacklisted") == "1",
                        "is_whitelisted": token_data.get("is_whitelisted") == "1",
                        "is_proxy": token_data.get("is_proxy") == "1",
                        "owner_address": token_data.get("owner_address", ""),
                        "risk_score": token_data.get("risk_score", 0),
                    }
        except Exception as e:
            logger.warning(f"GoPlus check failed for {address}: {e}")
        return {}

# ══════════════════════════════════════════════════════════════════════
# KNOWN SCAM DATABASE - Manual curated patterns (expanded)
# ══════════════════════════════════════════════════════════════════════

KNOWN_SCAMS_EXPANDED = [
    # From crypto_embeddings.py KNOWN_SCAM_PATTERNS
    {
        "name": "Honeypot - Sell Disabled",
        "description": "Token where only the creator can sell. maxSellAmount=0, tradingEnabled=false, blacklist of non-owner addresses.",
        "indicators": [
            "maxSellAmount=0",
            "tradingEnabled=false",
            "onlyOwner transfer",
            "blacklist all",
        ],
        "severity": "critical",
        "source": "rmi-curated",
        "code_snippets": ["maxSellAmount = 0", "tradingEnabled = false"],
    },
    {
        "name": "Unlimited Mint / Rug Pull",
        "description": "Owner can mint unlimited tokens, diluting holders to zero before draining liquidity.",
        "indicators": ["unrestricted mint", "onlyOwner mint", "no supply cap"],
        "severity": "critical",
        "source": "rmi-curated",
        "code_snippets": ["function mint(address to, uint256 amount) external onlyOwner"],
    },
    {
        "name": "Hidden Fee Manipulation",
        "description": "Owner sets fees to 99% post-launch, making sells impossible or stealing all transfers.",
        "indicators": ["setTax(99)", "dynamic fee > 50%", "no max fee cap"],
        "severity": "high",
        "source": "rmi-curated",
        "code_snippets": ["function setFee(uint256 _fee) external onlyOwner", "taxFee = 99"],
    },
    {
        "name": "Liquidity Drain Backdoor",
        "description": "Hidden function allowing owner to remove all liquidity via manualSwap or rescueToken.",
        "indicators": ["manualSwap", "removeLiquidity all", "drain", "rescueToken onlyOwner"],
        "severity": "critical",
        "source": "rmi-curated",
        "code_snippets": [
            "function manualSwap() external onlyOwner",
            "uniswapV2Router.removeLiquidity",
        ],
    },
    {
        "name": "Proxy Upgrade Trap",
        "description": "Upgradeable proxy where admin can swap implementation to malicious contract post-launch.",
        "indicators": ["upgradeTo", "upgradeToAndCall", "UUPS", "changeAdmin"],
        "severity": "high",
        "source": "rmi-curated",
        "code_snippets": ["function upgradeTo(address newImplementation) external onlyOwner"],
    },
    {
        "name": "Wallet Drainer / Approval Scam",
        "description": "Contract tricks users into unlimited approval, then drains via transferFrom.",
        "indicators": ["unlimited approval", "transferFrom", "approveAndCall", "permit exploit"],
        "severity": "critical",
        "source": "rmi-curated",
        "code_snippets": ["IERC20(token).transferFrom(victim, attacker, balance)"],
    },
    # Additional patterns from real-world incidents
    {
        "name": "Flash Loan Attack",
        "description": "Exploiter uses flash loan to manipulate price oracle or governance, then extracts value.",
        "indicators": ["flashLoan", "getFlashLoan", "onFlashLoan", "price oracle manipulation"],
        "severity": "critical",
        "source": "rmi-curated",
        "code_snippets": ["function flashLoan(", "function onFlashLoan("],
    },
    {
        "name": "Reentrancy Attack",
        "description": "Contract calls external contract before updating state, allowing recursive withdrawal.",
        "indicators": [
            "reentrancy",
            "call before state update",
            "no reentrancy guard",
            "checks-effects-interactions violated",
        ],
        "severity": "critical",
        "source": "rmi-curated",
        "code_snippets": ["msg.sender.call{value:", "nonReentrant", "ReentrancyGuard"],
    },
    {
        "name": "Unchecked Return Value",
        "description": "Contract ignores return value of external call, assuming success. Classic King of the Ether pattern.",
        "indicators": ["unchecked send", "call without require", ".send( without check"],
        "severity": "high",
        "source": "rmi-curated",
        "code_snippets": ["address(msg.sender).send(", "address(this).balance"],
    },
    {
        "name": "Access Control Missing",
        "description": "Critical functions lack access control (no onlyOwner/modifier), allowing anyone to drain/modify.",
        "indicators": [
            "no onlyOwner",
            "public init function",
            "unprotected withdraw",
            "no modifier",
        ],
        "severity": "critical",
        "source": "rmi-curated",
        "code_snippets": ["function withdraw() public {", "function init("],
    },
    {
        "name": "MEV Sandwich Vulnerability",
        "description": "High-slippage token that sandwich bots exploit repeatedly. Creator often runs the bots.",
        "indicators": [
            "slippage > 10%",
            "sandwich pattern",
            "same wallet MEV + deployer",
            "high tax token",
        ],
        "severity": "medium",
        "source": "rmi-curated",
        "code_snippets": [],
    },
    {
        "name": "Fake Token - Name Squatting",
        "description": "Token with identical name/symbol to legitimate project. Trick users into buying the wrong token.",
        "indicators": [
            "identical name to top-100",
            "different contract from official",
            "recently created",
            "no liquidity",
        ],
        "severity": "medium",
        "source": "rmi-curated",
        "code_snippets": [],
    },
    {
        "name": "Anti-Bot Launch Scam",
        "description": "Claims 'anti-bot' but actually blocks ALL buys except creator addresses. Classic honeypot variant.",
        "indicators": ["antiBot", "isBot", "botDetection", "onlyAllowed", "_isExcluded"],
        "severity": "high",
        "source": "rmi-curated",
        "code_snippets": ["modifier antiBot(", "require(!isBot(msg.sender)"],
    },
    {
        "name": "Cloned Contract with Modified Tax",
        "description": "Copy of popular token contract with tax parameters changed to steal from unaware investors.",
        "indicators": [
            "identical bytecode except tax",
            "same name as verified",
            "different deployer",
            "tax > 20%",
        ],
        "severity": "high",
        "source": "rmi-curated",
        "code_snippets": [],
    },
    {
        "name": "Fake Liquidity Lock",
        "description": "Claims liquidity locked but LP tokens held by creator wallet with no time-lock or fake lock contract.",
        "indicators": [
            "fake lock",
            "LP in EOA",
            "no unicrypt/teamfinance",
            "liquidity lock claim unverified",
        ],
        "severity": "high",
        "source": "rmi-curated",
        "code_snippets": [],
    },
]

# ══════════════════════════════════════════════════════════════════════
# INGESTION ORCHESTRATOR
# ══════════════════════════════════════════════════════════════════════

class ScamIngestionPipeline:
    """
    Automated pipeline for building the scam knowledge base.

    Usage:
        pipeline = ScamIngestionPipeline()
        stats = await pipeline.run_full_ingestion()
    """

    def __init__(self) -> None:
        self._last_run: datetime | None = None
        self._total_ingested = 0
        # Primary: RSS feeds (always work)
        self.connectors = [
            ("curated", self._ingest_curated),
            ("rss_feeds", self._ingest_all_rss),
        ]

    async def run_full_ingestion(self, force: bool = False) -> dict[str, Any]:
        """Run all ingestion sources and return stats."""
        if not force and self._last_run and (datetime.now(UTC) - self._last_run).seconds < 300:
            return {
                "status": "skipped",
                "reason": "rate limited",
                "last_run": self._last_run.isoformat(),
            }

        from app.crypto_embeddings import get_embedder

        embedder = await get_embedder()

        stats = {"sources": {}, "total": 0, "errors": 0}

        for source_name, ingest_fn in self.connectors:
            try:
                docs = await ingest_fn(embedder)
                if docs:
                    # Ingest via Redis + FAISS (pgvector removed)
                    from app.rag_service import ingest_document

                    count = 0
                    for doc in docs:
                        try:
                            meta = doc.get("metadata", {})
                            meta["source"] = doc.get("source", source_name)
                            await ingest_document(
                                collection=doc.get("collection", "known_scams"),
                                content=doc.get("content", ""),
                                metadata=meta,
                            )
                            count += 1
                        except (AttributeError, ImportError):
                            pass
                    stats["sources"][source_name] = count
                    stats["total"] += count
                    logger.info(f"Ingested {count} docs from {source_name}")
            except Exception as e:
                logger.error(f"Ingestion failed for {source_name}: {e}")
                stats["errors"] += 1
                stats["sources"][source_name] = 0

        self._last_run = datetime.now(UTC)
        self._total_ingested += stats["total"]

        # FAISS indexes are built from Redis automatically
        # when first search hits each collection - no manual build needed.

        return {
            "status": "completed",
            "total_ingested": stats["total"],
            "cumulative_total": self._total_ingested,
            "sources": stats["sources"],
            "errors": stats["errors"],
            "timestamp": self._last_run.isoformat(),
        }

    async def _ingest_all_rss(self, embedder) -> list[dict]:
        """Ingest from all security RSS feeds in parallel."""
        all_docs = []
        tasks = [
            RSSFeedConnector.fetch_feed(url, name, limit=15) for url, name in SECURITY_RSS_FEEDS
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for i, articles in enumerate(results):
            if isinstance(articles, Exception):
                logger.warning(f"RSS feed {SECURITY_RSS_FEEDS[i][1]} error: {articles}")
                continue
            if not isinstance(articles, list):
                continue

            source_name = SECURITY_RSS_FEEDS[i][1]
            for article in articles:
                try:
                    content = f"SECURITY INTEL [{source_name}]: {article['name']}. {article.get('description', '')}"
                    result = await embedder.embed_scam_pattern(
                        pattern_name=article["name"],
                        description=content,
                        severity="medium",
                    )
                    doc_id = hashlib.sha256(
                        f"rss:{source_name}:{article['name']}".encode()
                    ).hexdigest()[:16]
                    all_docs.append(
                        {
                            "id": doc_id,
                            "collection": "market_intel",
                            "embedding": result.vector,
                            "content": content,
                            "metadata": article,
                            "source": f"rss-{source_name.lower()}",
                            "severity": "medium",
                        }
                    )
                except Exception as e:
                    logger.warning(f"Failed to embed RSS article {article.get('name')}: {e}")

        logger.info(f"RSS ingestion: {len(all_docs)} docs from {len(SECURITY_RSS_FEEDS)} feeds")
        return all_docs

    async def _ingest_curated(self, embedder) -> list[dict]:
        """Ingest manually curated scam patterns (always available)."""
        docs = []
        for pattern in KNOWN_SCAMS_EXPANDED:
            try:
                result = await embedder.embed_scam_pattern(
                    pattern_name=pattern["name"],
                    description=pattern["description"],
                    code_snippets=pattern.get("code_snippets", []),
                    indicators=pattern.get("indicators", []),
                    severity=pattern["severity"],
                )
                doc_id = hashlib.sha256(f"curated:{pattern['name']}".encode()).hexdigest()[:16]
                docs.append(
                    {
                        "id": doc_id,
                        "collection": "known_scams",
                        "embedding": result.vector,
                        "content": f"{pattern['name']}: {pattern['description']}",
                        "metadata": pattern,
                        "source": pattern["source"],
                        "severity": pattern["severity"],
                    }
                )
            except Exception as e:
                logger.warning(f"Failed to embed curated pattern {pattern['name']}: {e}")
        return docs

# ══════════════════════════════════════════════════════════════════════
# SINGLETON
# ══════════════════════════════════════════════════════════════════════

_pipeline: ScamIngestionPipeline | None = None

async def get_ingestion_pipeline() -> ScamIngestionPipeline:
    global _pipeline
    if _pipeline is None:
        _pipeline = ScamIngestionPipeline()
    return _pipeline
