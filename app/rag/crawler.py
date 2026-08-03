"""Crawl4AI integration for RAG ingestion.

Handles: crypto news sites, project docs, whitepapers, audit reports.

Pipeline (crawl → chunk → embed → ANN store):
  1. ``crawl_crypto_page``     — crawl a single page with Crawl4AI (JS-aware)
  2. ``crawl_and_ingest``      — crawl + ingest into the RAG engine
  3. ``discover_crypto_news``  — RSS/sitemap discovery from unified_news_sources.json
  4. ``discover_and_ingest``   — periodic sweep: discover → crawl → ingest

Run standalone (cron / manual):
    python3 -m app.rag.crawler discover_and_ingest
    python3 -m app.rag.crawler crawl "https://example.com"

Crawl4AI is imported lazily so importing this module never requires the
optional dependency (the RAG engine works without it; only the crawler
path needs ``pip install crawl4ai`` + Playwright browsers).
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import email.utils
import hashlib
import html
import json
import os
import re
import socket
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from app.config import settings
from app.core.logging import get_logger
from app.rag.dedup import (
    check_duplicates,
    content_fingerprint,
    lookup_group_by_url,
    mark_content_ingested,
    mark_url_ingested,
    new_group_id,
    record_fp_group,
    record_group_membership,
)
from app.rag.ratings import rate_article

log = get_logger(__name__)

# ── Configuration ────────────────────────────────────────────────────────────

# Unified master source registry (all crypto news sources across the codebase).
# Path: app/databus/unified_news_sources.json
_SOURCES_FILE = Path(__file__).resolve().parents[1] / "databus" / "unified_news_sources.json"
# Legacy per-repo crawl list — fallback for old deployments.
_LEGACY_SOURCES_FILE = Path(__file__).parent / "crawl_sources.json"
# Source types that yield article feeds the crawler can follow. Social streams
# (reddit / nitter) are excluded: the crawler follows article links, not posts.
_CRAWLABLE_TYPES = ("rss", "atom", "sitemap")
DEFAULT_COLLECTION = "news_v2"
DEFAULT_PAGE_TIMEOUT_MS = 45_000
DEFAULT_MAX_URLS_PER_SWEEP = 25

# Dedup counter keys returned in sweep summaries (distinguish dup levels).
DUP_COUNTERS = ("url_dup", "title_dup", "content_dup", "entity_dup")

# Article quality / relevance / freshness knobs (sweep pipeline).
MAX_ARTICLES_PER_SOURCE = 10  # per-source cap per sweep (rate limiting)
MIN_ARTICLE_QUALITY = 0.3  # skip articles scoring below this (0-1)
MAX_ARTICLE_AGE_HOURS = 6.0  # older articles are stale → skip
FRESH_AGE_HOURS = 1.0  # <1h = fresh → ingest immediately

# ── Relevance / quality vocab ────────────────────────────────────────────────

# Uppercase token symbols matched as whole words (case-sensitive) so common
# prose words ("op", "ton", "arb") do not trip the relevance filter.
_TOKEN_SYMBOLS: tuple[str, ...] = (
    "BTC",
    "ETH",
    "SOL",
    "BNB",
    "XRP",
    "ADA",
    "DOGE",
    "DOT",
    "AVAX",
    "LINK",
    "MATIC",
    "POL",
    "USDC",
    "USDT",
    "DAI",
    "LTC",
    "BCH",
    "SHIB",
    "PEPE",
    "WIF",
    "BONK",
    "SUI",
    "APT",
    "ARB",
    "OP",
    "TON",
    "TRX",
    "NEAR",
    "ATOM",
    "INJ",
    "SEI",
    "JUP",
    "UNI",
    "AAVE",
    "MKR",
    "LDO",
    "CRV",
    "GMX",
    "SNX",
    "PYTH",
    "WLD",
    "ENA",
    "PENDLE",
    "EIGEN",
    "STRK",
    "ZRO",
    "RENDER",
    "TAO",
    "KAS",
    "XLM",
    "ALGO",
    "HBAR",
    "ETC",
    "IMX",
)

_PROTOCOL_WORDS: tuple[str, ...] = (
    "bitcoin",
    "ethereum",
    "solana",
    "cardano",
    "polkadot",
    "avalanche",
    "polygon",
    "arbitrum",
    "optimism",
    "chainlink",
    "uniswap",
    "aave",
    "lido",
    "makerdao",
    "curve finance",
    "synthetix",
    "compound",
    "pancakeswap",
    "jupiter",
    "raydium",
    "celestia",
    "binance",
    "coinbase",
    "tether",
    "stablecoin",
)

_DEFI_WORDS: tuple[str, ...] = (
    "staking",
    "yield",
    "liquidity",
    "dex",
    "defi",
    "decentralized finance",
    "amm",
    "lending",
    "borrowing",
    "farming",
    "restaking",
    "perpetual",
    "oracle",
    "bridge",
    "liquidity pool",
    "tvl",
    "vault",
)

_SECURITY_WORDS: tuple[str, ...] = (
    "hack",
    "hacked",
    "hacking",
    "exploit",
    "audit",
    "vulnerability",
    "breach",
    "scam",
    "rug pull",
    "rugpull",
    "phishing",
    "stolen",
    "theft",
    "drain",
    "drained",
    "zero-day",
    "malware",
    "honeypot",
    "wallet drainer",
    "attack",
    "compromised",
)

_AGENCY_WORDS: tuple[str, ...] = ("SEC", "CFTC", "MiCA", "FinCEN", "OCC", "DOJ")
_REGULATORY_WORDS: tuple[str, ...] = (
    "regulation",
    "regulatory",
    "enforcement",
    "lawsuit",
    "indictment",
    "sanction",
    "subpoena",
    "probe",
    "banning",
    "banned",
    "ETF",
    "approved",
)

_TOKEN_RE = re.compile(r"\b(?:" + "|".join(_TOKEN_SYMBOLS) + r")\b")
_PROTOCOL_RE = re.compile(r"\b(?:" + "|".join(_PROTOCOL_WORDS) + r")\b", re.IGNORECASE)
_DEFI_RE = re.compile(r"\b(?:" + "|".join(_DEFI_WORDS) + r")\b", re.IGNORECASE)
_SECURITY_RE = re.compile(r"\b(?:" + "|".join(_SECURITY_WORDS) + r")\b", re.IGNORECASE)
_AGENCY_RE = re.compile(r"\b(?:" + "|".join(_AGENCY_WORDS) + r")\b")
_REGULATORY_RE = re.compile(r"\b(?:" + "|".join(_REGULATORY_WORDS) + r")\b", re.IGNORECASE)

# Paywall / teaser markers — content that is only a locked summary.
_PAYWALL_MARKERS: tuple[str, ...] = (
    "subscribe to read",
    "sign in to read",
    "sign in to continue",
    "login to read",
    "premium content",
    "premium article",
    "members only",
    "this is a preview",
    "unlock full",
    "become a member",
    "subscribe for full",
    "paywall",
    "registration required",
    "full story requires",
)

# ── News content quality gates ─────────────────────────────────────────────
# Spam / clickbait, HTML boilerplate extraction, summarisation, title
# cleaning, and language detection. These keep low-quality content out of
# the news collection so ratings and search stay trustworthy.

# Phrases that signal promotional clickbait — skip the article entirely.
SPAM_INDICATORS: tuple[str, ...] = (
    "price prediction",
    "will explode",
    "next 100x",
    "don't miss",
    "urgent",
    "breaking: price",
    "just in: price",
    "you won't believe",
    "guaranteed",
    "risk free",
    "secret method",
)

# Sources known for recycled / promotional content — never rate or ingest.
LOW_QUALITY_DOMAINS: tuple[str, ...] = (
    "zycrypto.com",
    "coinpedia.org",
    "bitcoinist.com",
    "cryptopotato.com",
    "coingape.com",
)

# Elements whose whole subtree is boilerplate — never part of the article body.
_BOILERPLATE_TAGS: frozenset[str] = frozenset(
    {
        "script",
        "style",
        "noscript",
        "iframe",
        "svg",
        "canvas",
        "template",
        "nav",
        "header",
        "footer",
        "aside",
        "form",
        "button",
        "dialog",
        "select",
        "input",
        "textarea",
    }
)

# Class/id fragments (case-insensitive substring) that mark boilerplate
# containers: cookie banners, newsletter signups, related-article rails,
# sidebars, share widgets, popups, and navigation.
_BOILERPLATE_FRAGMENTS: tuple[str, ...] = (
    "cookie",
    "consent",
    "newsletter",
    "signup",
    "subscribe",
    "related",
    "recommended",
    "sidebar",
    "advert",
    "-ad",
    "promo",
    "share",
    "social",
    "breadcrumb",
    "popup",
    "modal",
    "overlay",
    "trending",
    "most-read",
    "comments",
    "widget",
    "menu",
    "navigation",
)

# Class/id fragments that mark the primary article body container.
_CONTENT_FRAGMENTS: tuple[str, ...] = (
    "article-body",
    "article_content",
    "article-content",
    "post-content",
    "post_content",
    "entry-content",
    "entry_content",
    "story-body",
    "story_content",
    "news-body",
    "single-post",
    "post-body",
    "page-content",
)

# Block-level tags whose boundary implies a paragraph break in extracted text.
_BLOCK_TAGS: frozenset[str] = frozenset(
    {
        "p",
        "div",
        "section",
        "article",
        "li",
        "blockquote",
        "figure",
        "tr",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "br",
        "hr",
        "table",
        "ul",
        "ol",
    }
)

_TAG_RE = re.compile(r"<(/?)([a-zA-Z][a-zA-Z0-9]*)((?:\s+[^<>]*?)?)(/?)>", re.DOTALL)

# Language gate: a word counts as "ASCII" when every code point is < 128.
# CJK text has no inter-word spaces, so the word count is naturally low —
# 3 is enough to distinguish a real foreign-language paragraph.
_ENGLISH_MIN_WORDS = 3
_ENGLISH_ASCII_THRESHOLD = 0.8

_SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[.!?])\s+")


def _article_domain(url: str) -> str:
    """Bare lowercase hostname for an article URL (``''`` when unparseable)."""
    try:
        return (urlparse(url).hostname or "").lower().removeprefix("www.")
    except ValueError:
        return ""


def is_spam_or_clickbait(title: str, domain: str) -> bool:
    """Filter out spam and clickbait articles."""
    if domain in LOW_QUALITY_DOMAINS:
        return True
    title_lower = title.lower()
    return any(indicator in title_lower for indicator in SPAM_INDICATORS)


def filter_spam(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep only articles that pass the spam/clickbait gate."""
    kept: list[dict[str, Any]] = []
    for item in items:
        domain = _article_domain(str(item.get("url") or ""))
        if is_spam_or_clickbait(str(item.get("title") or ""), domain):
            log.info("article_spam_filtered", url=item.get("url"), domain=domain)
            continue
        kept.append(item)
    return kept


def _find_content_end(html_text: str, open_tag: str, start: int) -> int:
    """Index just past the matching close tag for *open_tag* at *start*.

    Handles self-closing tags and nested same-tag elements. Returns
    ``len(html_text)`` when the tree is unbalanced (caller falls back to
    the whole document).
    """
    depth = 0
    for match in _TAG_RE.finditer(html_text, start):
        closing = match.group(1) or ""
        tag = match.group(2) or ""
        self_close = match.group(4) or ""
        if tag.lower() != open_tag.lower():
            continue
        if self_close:
            continue
        if closing:
            depth -= 1
            if depth == 0:
                return match.end()
        else:
            depth += 1
    return len(html_text)


def _extract_content_region(html_text: str) -> str:
    """Isolate the primary article body container.

    Prefers semantic containers (``<article>`` then ``<main>``), then
    common content-div classes (``article-body``, ``post-content``, ...).
    Returns the whole document when no content container is found — the
    boilerplate stripper then drops sidebar/footer/cookie noise.
    """
    lower = html_text.lower()
    for tag in ("article", "main"):
        match = re.search(rf"<{tag}(?=[\s>])", lower)
        if match:
            start = match.start()
            return html_text[start : _find_content_end(html_text, tag, start)]
    for match in _TAG_RE.finditer(html_text):
        closing = match.group(1) or ""
        tag = match.group(2) or ""
        self_close = match.group(4) or ""
        if closing or self_close:
            continue
        if tag.lower() not in ("div", "section"):
            continue
        attrs = match.group(3) or ""
        if any(frag in attrs.lower() for frag in _CONTENT_FRAGMENTS):
            return html_text[match.start() : _find_content_end(html_text, tag, match.start())]
    return html_text


class _BoilerplateStripper(HTMLParser):
    """HTMLParser that drops boilerplate subtrees and emits article text.

    Uses ``convert_charrefs=True`` so HTML entities are unescaped on the
    way out. Block-level tags become paragraph breaks; inline tags are
    transparent.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._skip_depth:
            self._skip_depth += 1
            return
        attr_text = " ".join(f"{k}={v}" for k, v in attrs if v is not None)
        if tag in _BOILERPLATE_TAGS or any(frag in attr_text.lower() for frag in _BOILERPLATE_FRAGMENTS):
            self._skip_depth = 1
            return

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._skip_depth or tag in _BOILERPLATE_TAGS:
            return
        if tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if self._skip_depth:
            self._skip_depth -= 1
            return
        if tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth and data.strip():
            self._parts.append(data)

    def text(self) -> str:
        """Visible article text with block boundaries as newlines."""
        return "".join(self._parts)


def _normalise_whitespace(text: str) -> str:
    """Collapse horizontal runs, drop blank lines, cap consecutive newlines.

    Preserves paragraph breaks (double newline) so downstream scoring
    (``score_article_quality``) and summarisation still see structure.
    """
    if not text:
        return ""
    lines = [re.sub(r"[ \t\f\v]+", " ", ln).strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]
    return "\n\n".join(lines).strip()


def clean_article_html(html_text: str) -> str:
    """Strip boilerplate and extract clean article text from raw HTML.

    Pipeline:
      1. Isolate the primary content container (``<article>``/``<main>``
         or a content-div class) when the document has one.
      2. Drop boilerplate subtrees (scripts, styles, nav, cookie banners,
         newsletter signups, related-article rails, sidebars, popups).
      3. Unescape entities and normalise whitespace, keeping paragraph
         breaks.

    Paywalled articles return whatever text is visible in the DOM — the
    downstream quality gate (``score_article_quality``) rejects locked
    teasers via ``_looks_paywalled``.
    """
    if not html_text or not html_text.strip():
        return ""
    region = _extract_content_region(html_text)
    parser = _BoilerplateStripper()
    try:
        parser.feed(region)
        parser.close()
    except Exception:  # pragma: no cover - malformed HTML must never kill a crawl
        # Last-resort tag strip: no paragraph structure, but text survives.
        return _normalise_whitespace(re.sub(r"<[^>]+>", " ", region))
    return _normalise_whitespace(parser.text())


def generate_summary(text: str, max_chars: int = 300) -> str:
    """Extract first 2-3 meaningful sentences as summary.

    Used for articles whose RSS entry has no useful summary. Falls back to
    a word-boundary-truncated prefix when the text has no sentence
    boundaries (headlines, tickers, fragmented crawl output).
    """
    if not text:
        return ""
    text = _normalise_whitespace(text)
    sentences = [s.strip() for s in _SENTENCE_BOUNDARY_RE.split(text) if s.strip()]
    summary = ""
    for sentence in sentences[:3]:
        candidate = f"{summary} {sentence}".strip()
        if summary and len(candidate) > max_chars:
            break
        summary = candidate
        if len(summary) >= max_chars:
            break
    if not summary:
        summary = text
    if len(summary) > max_chars:
        summary = summary[:max_chars].rsplit(" ", 1)[0]
    return summary.strip()


def clean_title(raw_title: str, source_name: str) -> str:
    """Remove source name suffixes, HTML entities, excessive punctuation.

    Handles the common crawler/RSS artifacts: ``"Headline | CoinDesk"``,
    ``"Headline - CoinDesk"``, ``"Headline — CoinDesk"``, ``"CoinDesk:
    Headline"``, double spaces, and runs of ``!``/``?``.
    """
    if not raw_title:
        return ""
    title = html.unescape(str(raw_title)).strip()
    source = (source_name or "").strip().lower()
    if source:
        # Match the full source name and its bare domain part (coindesk.com →
        # coindesk). Use whitespace-tolerant variants so display names work:
        # "theblock" matches "The Block", "coindesk" matches "CoinDesk".
        base_parts = [source]
        if "." in source:
            base_parts.append(source.split(".", 1)[0])
        alternatives = "|".join(r"\s*".join(re.escape(ch) for ch in part) for part in base_parts)
        # Trailing suffix: "Headline | source", " - source", " — source", " » source"
        title = re.sub(
            rf"\s*(?:\||-|—|::|»)\s*(?:{alternatives})\s*$",
            "",
            title,
            flags=re.IGNORECASE,
        ).strip()
        # Leading prefix: "source: Headline", "source | Headline", "source » Headline"
        title = re.sub(
            rf"^\s*(?:{alternatives})\s*(?::|\||»)\s*",
            "",
            title,
            flags=re.IGNORECASE,
        ).strip()
    title = re.sub(r"\s+", " ", title)
    title = re.sub(r"([!?])\1+", r"\1", title)  # !!! → !, ??? → ?
    title = re.sub(r"\s+([,.;:!?])", r"\1", title)  # "word ," → "word,"
    return title.strip()


def is_english(text: str) -> bool:
    """Simple language detection — check if >80% of words are ASCII.

    Words are 2+ letter sequences (unicode-aware). Fails open (returns
    True) on empty or short (<5 word) text so the pipeline never drops
    content it cannot judge.
    """
    if not text:
        return True
    words = re.findall(r"[^\W\d_]{2,}", text)
    if len(words) < _ENGLISH_MIN_WORDS:
        return True
    ascii_words = sum(1 for word in words if all(ord(c) < 128 for c in word))
    return ascii_words / len(words) >= _ENGLISH_ASCII_THRESHOLD


# ── Helpers ─────────────────────────────────────────────────────────────────


def _unwrap_result(result: Any) -> Any:
    """Crawl4AI 0.8.x ``arun`` returns a ``CrawlResultContainer``.

    The container is iterable/indexable; unwrap it to the single
    ``CrawlResult`` so callers get the same API across versions.
    """
    if result is None:
        return None
    # A CrawlResult exposes ``success``; the container does not.
    if hasattr(result, "success"):
        return result
    if hasattr(result, "__getitem__"):
        try:
            return result[0]
        except (IndexError, TypeError):
            return result
    return result


def _extract_markdown(result: Any) -> str:
    """Extract the raw markdown text from a CrawlResult.

    Handles both the string-compatible markdown object (0.8.x) and a
    plain-string fallback so the crawler is resilient to API drift.
    """
    md = getattr(result, "markdown", None)
    if md is None:
        return ""
    if isinstance(md, str):
        return md
    raw = getattr(md, "raw_markdown", None)
    if isinstance(raw, str):
        return raw
    # StringCompatibleMarkdown subclasses str in some versions.
    return str(md)


def _doc_id(url: str) -> str:
    """Stable document id for a crawled URL (matches databus hash style)."""
    h = hashlib.sha256(url.encode()).hexdigest()[:12]
    return f"crawl:{h}"


def load_sources() -> dict[str, list[str]]:
    """Load crawl source feed URLs from ``unified_news_sources.json``.

    Returns a mapping of category → list of feed URLs for crawlable source
    types (``rss``/``atom``/``sitemap``). Social sources (``reddit``,
    ``nitter``) are excluded because the crawler follows article links,
    not social streams.

    Falls back to the legacy ``crawl_sources.json`` (category → URLs) when
    the unified file is missing or malformed, so old deployments keep
    working. Returns an empty mapping if both are unavailable.
    """
    for path in (_SOURCES_FILE, _LEGACY_SOURCES_FILE):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            log.warning("sources_load_failed", path=str(path), error=str(e))
            continue
        if not isinstance(data, dict):
            continue
        if "sources" in data:
            # Unified format: [{id, name, url, category, type, ...}, ...]
            grouped: dict[str, list[str]] = {}
            for entry in data.get("sources", []):
                if not isinstance(entry, dict):
                    continue
                if entry.get("type") not in _CRAWLABLE_TYPES:
                    continue
                url = str(entry.get("url") or "")
                if not url.startswith(("http://", "https://")):
                    continue
                grouped.setdefault(str(entry.get("category") or "crypto_news"), []).append(url)
            return grouped
        # Legacy format: {category: [url, ...]}
        return {k: list(v) for k, v in data.items() if isinstance(v, list)}
    return {}


# ── Article quality / relevance / freshness ────────────────────────────────


def _article_text(article: dict[str, Any]) -> str:
    """Concatenate the text fields used for relevance matching."""
    parts = (article.get("title"), article.get("summary"), article.get("content"))
    return " ".join(str(p) for p in parts if p)


def _mentions_crypto(text: str) -> bool:
    """True when text mentions crypto tokens/protocols, DeFi, security, or regulation."""
    if not text:
        return False
    return bool(
        _TOKEN_RE.search(text)
        or _PROTOCOL_RE.search(text)
        or _DEFI_RE.search(text)
        or _SECURITY_RE.search(text)
        or _AGENCY_RE.search(text)
        or _REGULATORY_RE.search(text)
    )


def _looks_paywalled(text: str) -> bool:
    """True when the text looks like a locked paywall teaser, not full content."""
    lower = text.lower()
    return any(marker in lower for marker in _PAYWALL_MARKERS)


def score_article_quality(article: dict[str, Any]) -> float:
    """Score article quality 0-1. Filter out low-quality content.

    Components (per spec):
      - Length: >500 chars +0.3, >2000 chars +0.5
      - Has paragraphs: +0.2
      - Not paywalled summary: +0.1
      - Has crypto entities mentioned: +0.2

    ``content`` (when present — i.e. after a crawl) is authoritative: an
    empty crawled content scores 0 so failed extractions are rejected.
    Discovery items without a ``content`` key fall back to the feed
    ``summary`` so pre-crawl scoring still works.
    """
    if article.get("content") is not None:
        content = str(article.get("content") or "")
    else:
        content = str(article.get("summary") or "")
    if not content.strip():
        return 0.0

    score = 0.0
    n = len(content)
    if n > 2000:
        score += 0.5
    elif n > 500:
        score += 0.3
    if "\n\n" in content:
        score += 0.2
    if not _looks_paywalled(content):
        score += 0.1
    if _mentions_crypto(_article_text(article)):
        score += 0.2
    return round(min(score, 1.0), 3)


def is_relevant(article: dict[str, Any]) -> bool:
    """Relevance gate: does the article touch crypto, DeFi, security, or regulation?

    Applied pre-crawl on title + summary so off-topic content (NFT drops,
    pure gaming, general tech) never gets crawled. Any one matching
    category is enough to pass.
    """
    return _mentions_crypto(_article_text(article))


def filter_relevant(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep only articles that pass the relevance gate."""
    kept: list[dict[str, Any]] = []
    for item in items:
        if is_relevant(item):
            kept.append(item)
        else:
            log.info("article_irrelevant", url=item.get("url"), title=item.get("title"))
    return kept


def _parse_published(value: str) -> datetime | None:
    """Parse an RSS/Atom publish date into a tz-aware UTC datetime.

    Handles RFC 2822 (most RSS feeds) and ISO 8601 (Atom). Returns None
    when the value is missing or unparseable.
    """
    if not value:
        return None
    try:
        dt = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        dt = None
    if dt is not None:
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def freshness_tier(article: dict[str, Any], *, now: datetime | None = None) -> str:
    """Classify article age: ``fresh`` (<1h), ``recent`` (1-6h), ``stale`` (>6h), ``unknown``."""
    published = _parse_published(str(article.get("published", "")))
    if published is None:
        return "unknown"
    reference = now or datetime.now(UTC)
    age_hours = (reference - published).total_seconds() / 3600.0
    if age_hours < FRESH_AGE_HOURS:
        return "fresh"
    if age_hours <= MAX_ARTICLE_AGE_HOURS:
        return "recent"
    return "stale"


def is_fresh(
    article: dict[str, Any], *, max_age_hours: float = MAX_ARTICLE_AGE_HOURS, now: datetime | None = None
) -> bool:
    """Is the article young enough to ingest?

    ``unknown`` dates pass (we cannot prove staleness) but are
    deprioritised by ``prioritize_by_freshness`` so they only run when
    there are not enough datable items.
    """
    published = _parse_published(str(article.get("published", "")))
    if published is None:
        return True
    reference = now or datetime.now(UTC)
    age_hours = (reference - published).total_seconds() / 3600.0
    return age_hours <= max_age_hours


def prioritize_by_freshness(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort newest-first; items with unparseable dates go last."""

    def _key(item: dict[str, Any]) -> tuple[int, float]:
        published = _parse_published(str(item.get("published", "")))
        if published is None:
            return (0, 0.0)
        return (1, published.timestamp())

    return sorted(items, key=_key, reverse=True)


# ── Crawl ───────────────────────────────────────────────────────────────────


async def crawl_crypto_page(url: str) -> dict[str, Any]:
    """Crawl a single crypto-related page.

    Args:
        url: The page to crawl.

    Returns:
        ``{"title", "content", "metadata"}`` where ``content`` is raw
        markdown and ``metadata`` includes the source URL, crawl time,
        and the crawler name.

    Raises:
        RuntimeError: If Crawl4AI is unavailable, the crawl fails, or the
            page yields no extractable content.
    """
    try:
        from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig
    except ImportError as e:
        raise RuntimeError(
            "Crawl4AI is not installed — run `pip install crawl4ai` and `playwright install chromium` before crawling"
        ) from e

    browser = BrowserConfig(headless=True)
    run_config = CrawlerRunConfig(page_timeout=DEFAULT_PAGE_TIMEOUT_MS)

    async with AsyncWebCrawler(config=browser) as crawler:
        container = await crawler.arun(url=url, config=run_config)
        result = _unwrap_result(container)

    if result is None:
        raise RuntimeError(f"crawl returned no result for {url}")

    if not getattr(result, "success", False):
        err = getattr(result, "error_message", "unknown crawl error")
        raise RuntimeError(f"crawl failed for {url}: {err}")

    content = _extract_markdown(result)
    if not content or not content.strip():
        # Fall back to extracted content (non-markdown renderers). Crawl4AI
        # returns HTML here — strip cookie/newsletter/sidebar boilerplate so
        # only the article body is ingested.
        content = clean_article_html(str(getattr(result, "extracted_content", "") or ""))
    else:
        # Markdown path: normalise whitespace, keep paragraph breaks.
        content = _normalise_whitespace(content)

    meta = dict(getattr(result, "metadata", None) or {})
    # Crawl4AI titles often carry a site suffix ("Headline | CoinDesk").
    raw_title = str(meta.get("title") or "")[:500]
    title = clean_title(raw_title, _article_domain(url))[:500]

    return {
        "title": title,
        "content": content.strip(),
        "metadata": {
            "source_url": url,
            "crawled_at": datetime.now(UTC).isoformat(),
            "crawler": "crawl4ai",
            "title": title,
            "status_code": getattr(result, "status_code", None),
        },
    }


async def crawl_and_ingest(
    url: str,
    collection: str = DEFAULT_COLLECTION,
    content: str | None = None,
    metadata: dict[str, Any] | None = None,
    published_at: str = "",
) -> dict[str, Any]:
    """Crawl a page and ingest it directly into the RAG engine.

    Multi-level dedup (all fail-open — backend outages never block):
      1. URL hash match            → ``url_dup``
      2. Title similarity (Qdrant) → ``title_dup``
      3. Content fingerprint       → ``content_dup``
      4. Crypto entity overlap     → ``entity_dup``

    On a duplicate the article is NOT ingested; the result carries
    ``status="duplicate"``, ``duplicate_level``, and the existing story's
    ``group_id`` (CryptoPanic-style grouping). Otherwise the article is
    ingested with a ``group_id`` in its metadata linking story variations.

    Args:
        url: The page to crawl and ingest.
        collection: RAG collection to write into (default ``news_v2``).
        content: Pre-extracted content. When supplied (e.g. after a quality
            check in ``discover_and_ingest``) the crawl is skipped and this
            content is ingested — avoids crawling the same page twice.
        metadata: Pre-extracted metadata paired with ``content``. Defaults
            to fresh crawl metadata.
        published_at: ISO/RSS publish timestamp (used by entity-level dedup
            time window). Empty when unknown.

    Returns:
        ``{"doc_id", "chunks", "status", "duplicate_level", "group_id"}``.
        ``duplicate_level`` is one of url|title|content|entity (or None).
    """
    from app.rag.engine import ingest_document

    if content is None:
        page = await crawl_crypto_page(url)
        content = page["content"]
        metadata = page["metadata"]
    elif metadata is None:
        metadata = {
            "source_url": url,
            "crawled_at": datetime.now(UTC).isoformat(),
            "crawler": "crawl4ai",
            "title": url,
        }

    title = str(metadata.get("title") or "")[:500]
    result = await check_duplicates(
        url=url, title=title, content=content, collection=collection, published_at=published_at
    )
    fp = content_fingerprint(content)

    if result.is_duplicate:
        group_id = result.group_id or lookup_group_by_url(url)
        log.debug(
            "dedup_decision level=%s url=%s group=%s matched=%s",
            result.level,
            url,
            group_id,
            result.matched_doc_id,
        )
        return {
            "doc_id": _doc_id(url),
            "chunks": 0,
            "status": "duplicate",
            "duplicate_level": result.level,
            "group_id": group_id,
        }

    # Fresh story: persist the group mapping so variations link to it.
    group_id = result.group_id or new_group_id(url)
    record_group_membership(group_id, url, collection)
    record_fp_group(fp, group_id, collection)

    ingest_metadata = {**metadata, "group_id": group_id}
    if published_at:
        ingest_metadata.setdefault("published_at", published_at)
    # RSS summaries are often empty or boilerplate — generate one from the
    # extracted body so downstream consumers always have a useful blurb.
    if not ingest_metadata.get("summary"):
        ingest_metadata["summary"] = generate_summary(content)[:1000]

    # Multi-axis rating card (Ground News style) attached to metadata so
    # every ingested article carries factuality / source reputation /
    # market impact / sentiment / timeliness. Ratings never block
    # ingestion — a failure degrades to metadata without the card.
    try:
        rating = rate_article(
            {
                "article_id": url,
                "title": title,
                "content": content,
                "source": str(metadata.get("source_url") or url),
                "published": published_at,
            }
        )
        ingest_metadata["rating"] = rating.to_dict()
        ingest_metadata["rating_confidence"] = rating.confidence
    except Exception as e:  # pragma: no cover - defensive
        log.debug("rating_failed url=%s err=%s", url, e)

    result_ingest = await ingest_document(
        collection=collection,
        doc_id=_doc_id(url),
        content=content,
        metadata=ingest_metadata,
    )
    if result_ingest.get("status") == "ok":
        mark_url_ingested(url)
        mark_content_ingested(fp, collection)
    return {
        "doc_id": result_ingest.get("doc_id", _doc_id(url)),
        "chunks": result_ingest.get("chunks", 0),
        "status": result_ingest.get("status", "unknown"),
        "duplicate_level": None,
        "group_id": group_id,
    }


# ── Discovery ───────────────────────────────────────────────────────────────


async def discover_crypto_news(sources: list[str] | None = None) -> list[dict[str, Any]]:
    """Discover crypto content from RSS feeds / sitemaps.

    Args:
        sources: Explicit feed URLs. Defaults to all feeds in
            ``unified_news_sources.json`` (rss/atom/sitemap types).

    Returns:
        A list of discovered items: ``{"url", "title", "published",
        "source", "summary"}``. Feeds that fail to parse are logged and
        skipped (never fatal).
    """
    try:
        import feedparser
    except ImportError as e:
        raise RuntimeError("feedparser is required for RSS discovery") from e

    if sources is not None:
        feed_urls = list(dict.fromkeys(sources))
    else:
        feed_urls = list(dict.fromkeys(u for urls in load_sources().values() for u in urls))
    if not feed_urls:
        log.warning("discover_no_sources", hint=f"add feeds to {_SOURCES_FILE}")
        return []

    items: list[dict[str, Any]] = []
    for feed_url in feed_urls:
        try:
            parsed = await asyncio.to_thread(feedparser.parse, feed_url)
        except Exception as e:
            log.warning("discover_feed_failed", feed=feed_url, error=str(e))
            continue

        if parsed.bozo and not parsed.entries:
            log.warning("discover_feed_bozo", feed=feed_url, error=getattr(parsed, "bozo_exception", None))
            continue

        # Per-source rate limiting: some RSS feeds churn every minute with
        # repeat content — cap the number of items taken per feed per sweep.
        for entry in parsed.entries[:MAX_ARTICLES_PER_SOURCE]:
            link = str(getattr(entry, "link", "") or "").strip()
            if not link.startswith(("http://", "https://")):
                continue
            items.append(
                {
                    "url": link,
                    # RSS titles frequently carry a site suffix (" | CoinDesk").
                    "title": clean_title(str(getattr(entry, "title", "") or "")[:500], _article_domain(link)),
                    "published": str(getattr(entry, "published", "") or ""),
                    "source": feed_url,
                    "summary": str(getattr(entry, "summary", "") or "")[:1000],
                }
            )

    return items


# ── Sweep (cron entrypoint) ─────────────────────────────────────────────────


def _hostname_resolves(hostname: str) -> bool:
    """Best-effort DNS check for a hostname (no raise)."""
    try:
        socket.getaddrinfo(hostname, None)
        return True
    except OSError:
        return False


def _ensure_host_connectivity() -> None:
    """Pre-flight host-side connectivity for cron runs (executed OUTSIDE Docker).

    Inside the rmi-backend container ``infinity-rag`` and ``rmi-redis``
    resolve and Redis needs no password. When this module runs on the host
    (``crawl-crypto-news.sh`` cron) those Docker-internal names fail DNS and
    Redis requires auth, which previously made every sweep item fail
    (25/25, ingested=0).

    This makes the sweep fail open instead of failing hard:

    * ``INFINITY_URL`` falls back to the host-mapped port (``localhost:8001``)
      when the configured hostname does not resolve from this process.
    * Missing ``REDIS_URL`` (no credentials available) is logged and dedup is
      skipped downstream — ingestion still proceeds.

    Never raises: connectivity problems are logged and the sweep continues.
    """
    if not os.getenv("REDIS_URL"):
        log.warning(
            "redis_url_missing host_side dedup_will_be_skipped hint=set REDIS_URL=redis://:password@localhost:6379/0"
        )

    infinity = os.getenv("INFINITY_URL") or settings.infinity_url
    try:
        host = urlparse(infinity).hostname or ""
    except ValueError:
        host = ""
    if host and "infinity-rag" in host and not _hostname_resolves(host):
        fallback = os.getenv("INFINITY_URL_FALLBACK", "http://localhost:8001")
        log.warning("infinity_dns_fallback from=%s to=%s", infinity, fallback)
        os.environ["INFINITY_URL"] = fallback
        # The settings singleton was built at import time; align it so the
        # lazy embedder singleton picks up the reachable URL.
        with contextlib.suppress(Exception):  # best-effort; env var is the source of truth
            settings.infinity_url = fallback


async def discover_and_ingest(
    max_urls: int = DEFAULT_MAX_URLS_PER_SWEEP,
    collection: str = DEFAULT_COLLECTION,
    sources: list[str] | None = None,
    min_quality: float = MIN_ARTICLE_QUALITY,
    max_age_hours: float = MAX_ARTICLE_AGE_HOURS,
) -> dict[str, Any]:
    """Discover new crypto content and ingest it into RAG.

    This is the cron entrypoint used by ``crawl-crypto-news.sh``. Each
    discovered URL is crawled and ingested independently so a single
    failure does not abort the sweep.

    Quality pipeline per sweep:
      1. Spam/clickbait gate — drop promotional domains and clickbait
         titles (``is_spam_or_clickbait``) before anything else.
      2. Relevance gate (title + summary) — drop off-topic articles
         (NFT drops, pure gaming, general tech) before crawling.
      3. Freshness prioritisation — newest first; ``> max_age_hours``
         old articles are skipped as stale.
      4. Language gate — non-English extracted content is skipped
         (we cannot rate it properly).
      5. Quality score on extracted content — articles scoring below
         ``min_quality`` are skipped.
    """
    _ensure_host_connectivity()
    discovered = await discover_crypto_news(sources)
    pre_spam = len(discovered)
    spam_kept = filter_spam(discovered)
    spam_filtered = pre_spam - len(spam_kept)
    relevant = filter_relevant(spam_kept)
    prioritized = prioritize_by_freshness(relevant)

    results: list[dict[str, Any]] = []
    errors: list[str] = []
    stale_skipped: list[str] = []
    non_english_skipped: list[str] = []
    quality_rejected: list[dict[str, Any]] = []

    for item in prioritized[:max_urls]:
        url = item["url"]
        if not is_fresh(item, max_age_hours=max_age_hours):
            stale_skipped.append(url)
            log.info("sweep_item_stale", url=url, published=item.get("published"))
            continue

        try:
            page = await crawl_crypto_page(url)
        except Exception as e:
            errors.append(f"{url}: {e}")
            log.warning("sweep_item_failed", url=url, error=str(e))
            continue

        # Non-English articles are skipped — the ratings engine cannot rate
        # them properly (vocabularies are English-only).
        if not is_english(page["content"]):
            non_english_skipped.append(url)
            log.info("sweep_item_non_english", url=url)
            continue

        article: dict[str, Any] = {**item, "content": page["content"]}
        quality = score_article_quality(article)
        if quality < min_quality:
            quality_rejected.append({"url": url, "quality": round(quality, 3)})
            log.info("sweep_item_low_quality", url=url, quality=round(quality, 3))
            continue

        try:
            ingested = await crawl_and_ingest(
                url,
                collection=collection,
                content=page["content"],
                metadata=page["metadata"],
                published_at=item.get("published") or "",
            )
            results.append({"url": url, **ingested})
        except Exception as e:
            errors.append(f"{url}: {e}")
            log.warning("sweep_item_failed", url=url, error=str(e))

    tier_counts = {"fresh": 0, "recent": 0, "stale": 0, "unknown": 0}
    for item in prioritized:
        tier = freshness_tier(item)
        tier_counts[tier] = tier_counts.get(tier, 0) + 1

    dup_levels = dict.fromkeys(DUP_COUNTERS, 0)
    for r in results:
        level = r.get("duplicate_level")
        counter = f"{level}_dup" if level else None
        if counter in dup_levels:
            dup_levels[counter] += 1

    summary = {
        "collection": collection,
        "discovered": len(discovered),
        "spam_filtered": spam_filtered,
        "relevant": len(relevant),
        "freshness_tiers": tier_counts,
        "attempted": len(results) + len(errors),
        "ingested": sum(1 for r in results if r.get("status") == "ok"),
        "duplicates": sum(1 for r in results if r.get("status") == "duplicate"),
        **dup_levels,
        "stale_skipped": len(stale_skipped),
        "non_english_skipped": len(non_english_skipped),
        "quality_rejected": len(quality_rejected),
        "failed": len(errors),
        "errors": errors[:20],
        "ran_at": datetime.now(UTC).isoformat(),
    }
    log.info("crawl_sweep_complete", **summary)
    return summary


# ── CLI ─────────────────────────────────────────────────────────────────────


def main() -> None:
    """CLI entrypoint: ``python -m app.rag.crawler <subcommand>``."""
    parser = argparse.ArgumentParser(prog="python -m app.rag.crawler", description="Crawl4AI RAG ingestion")
    sub = parser.add_subparsers(dest="command")

    sweep = sub.add_parser("discover_and_ingest", help="Discover + crawl + ingest (cron entrypoint)")
    sweep.add_argument("--max-urls", type=int, default=DEFAULT_MAX_URLS_PER_SWEEP)
    sweep.add_argument("--collection", default=DEFAULT_COLLECTION)
    sweep.add_argument("--feed", action="append", default=None, help="Extra feed URL (repeatable)")
    sweep.add_argument(
        "--min-quality", type=float, default=MIN_ARTICLE_QUALITY, help="Skip articles below this quality score (0-1)"
    )
    sweep.add_argument(
        "--max-age-hours", type=float, default=MAX_ARTICLE_AGE_HOURS, help="Skip articles older than this many hours"
    )

    single = sub.add_parser("crawl", help="Crawl a single URL and print the result")
    single.add_argument("url")
    single.add_argument("--collection", default=None, help="If set, also ingest")

    args = parser.parse_args()

    if args.command == "crawl":

        async def _single() -> None:
            page = await crawl_crypto_page(args.url)
            print(json.dumps(page, indent=2, default=str))
            if args.collection:
                print(json.dumps(await crawl_and_ingest(args.url, collection=args.collection), indent=2))

        asyncio.run(_single())
        return

    # Default (and discover_and_ingest): run the sweep.
    asyncio.run(
        discover_and_ingest(
            max_urls=args.max_urls,
            collection=args.collection,
            sources=args.feed,
            min_quality=args.min_quality,
            max_age_hours=args.max_age_hours,
        )
    )


if __name__ == "__main__":
    main()
