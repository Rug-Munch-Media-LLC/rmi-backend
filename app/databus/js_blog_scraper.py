"""Crawl4AI + proxy scraper for JS-only crypto blogs that have no usable RSS.

Targets the sources flagged ``DEAD`` in ``news_sources_status.json`` whose
feeds returned empty / WAF-blocked bodies while the *site* still publishes —
Binance, Coinbase, Chainlink, Gemini, Dragonfly, CoinMetrics (+ Talos),
Berkeley Blockchain, and 1inch. Their listing pages are JavaScript-rendered
SPAs, so a plain HTTP GET returns a shell page (HTTP 202/403) with zero
article links.

Pipeline per source (``crawl_js_blogs``):
  1. ``ProxyScraper.fetch`` — the four-tier escalation ladder from
     :mod:`app.databus.proxy_scraper` (direct → FlareSolverr → Webshare
     residential proxy → Crawl4AI headless). The Webshare credentials come
     from env / gopass; nothing is hardcoded here.
  2. Parse the returned body. Tiers 1-3 yield HTML; tier 4 yields markdown
     (``crawl_crypto_page`` output). Both are handled.
  3. If the ladder produced no article candidates (SPA shell served with
     200/202), retry once with an explicit Crawl4AI JS render
     (``_render_js``) so the browser executes the page scripts.
  4. Normalize each article to the RSS-crawler shape consumed by the RAG /
     news pipeline: ``title`` / ``url`` (+ ``source_url`` alias) /
     ``published_at`` / ``summary``, plus ``published`` (raw) and
     ``source`` / ``source_id`` / ``source_type`` for the aggregator.

The aggregate return mirrors ``news_intel.aggregate_all_news``
(``{"articles": [...]}``) so :func:`app.databus.rag_ingestion.index_databus_news`
or any other pipeline consumer can ingest the stream unchanged.

Env-driven configuration (no secrets in the repo):

   JS_BLOG_SCRAPER_ENABLED                master switch, default "1"
   JS_BLOG_SCRAPER_MAX_ARTICLES_PER_SOURCE  per-source cap, default 10
   JS_BLOG_SCRAPER_SOURCES                comma-separated source-id override
                                          (default: all configured sources)
   JS_BLOG_CHROME_CHANNEL                 Playwright channel for the JS-render
                                          fallback (e.g. "chrome" to use a
                                          system Chrome install). When unset,
                                          the fallback uses the canonical
                                          ``crawl_crypto_page`` (managed
                                          chromium) and degrades to a clean
                                          error if no browser is available.
   JS_BLOG_SCRAPER_ALLOW_JS_RENDER        explicit JS-render retry, default "1"

Run standalone (cron / manual):
    python3 -m app.databus.js_blog_scraper                     # all sources
    python3 -m app.databus.js_blog_scraper --source binance-com
    python3 -m app.databus.js_blog_scraper --source blog-coinbase-com --limit 5
"""

from __future__ import annotations

import asyncio
import html
import os
import re
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Any, Final
from urllib.parse import urljoin, urlparse

from app.core.logging import get_logger
from app.rag._crawler_helpers import _article_domain, _parse_published, clean_title, generate_summary

log = get_logger(__name__)

# ── Env-driven defaults ───────────────────────────────────────────────────────

JS_BLOG_SCRAPER_ENABLED: Final[bool] = os.getenv("JS_BLOG_SCRAPER_ENABLED", "1").lower() not in {
    "0",
    "false",
    "no",
}
JS_BLOG_SCRAPER_MAX_ARTICLES_PER_SOURCE: Final[int] = int(os.getenv("JS_BLOG_SCRAPER_MAX_ARTICLES_PER_SOURCE", "10"))
JS_BLOG_SCRAPER_ALLOW_JS_RENDER: Final[bool] = os.getenv("JS_BLOG_SCRAPER_ALLOW_JS_RENDER", "1").lower() not in {
    "0",
    "false",
    "no",
}
JS_BLOG_CHROME_CHANNEL: Final[str] = os.getenv("JS_BLOG_CHROME_CHANNEL", "").strip()

# ── Source registry ───────────────────────────────────────────────────────────
# ``id`` matches the key used in ``unified_news_sources.json`` /
# ``news_sources_status.json`` so status reports stay linkable. ``listing_url``
# is the JS-rendered article-listing page; ``article_path_re`` is a best-effort
# path filter that keeps article hrefs and drops nav/utility links.

JS_BLOG_SOURCES: Final[list[dict[str, Any]]] = [
    {
        "id": "binance-com",
        "name": "Binance",
        "listing_url": "https://www.binance.com/en/feed/news",
        "article_path_re": r"^/en/square/post/",
    },
    {
        "id": "blog-coinbase-com",
        "name": "Coinbase",
        "listing_url": "https://www.coinbase.com/blog",
        "article_path_re": r"^/blog/(?!landing/)",
    },
    {
        "id": "chainlink-blog",
        "name": "Chainlink Blog",
        "listing_url": "https://blog.chain.link/",
        "article_path_re": r"^/[a-z0-9][a-z0-9-]{10,}/?$",
    },
    {
        "id": "gemini-com",
        "name": "Gemini",
        "listing_url": "https://www.gemini.com/blog",
        "article_path_re": r"^/blog/",
    },
    {
        "id": "dragonfly-xyz",
        "name": "Dragonfly",
        "listing_url": "https://www.dragonfly.xyz/blog",
        "article_path_re": r"^/(blog|perspectives|research)/",
    },
    {
        "id": "coinmetrics-io",
        "name": "CoinMetrics",
        "listing_url": "https://coinmetrics.io/blog/",
        "article_path_re": r"^/blog/",
    },
    {
        "id": "talos",
        "name": "Talos Research",
        "listing_url": "https://research.talos.com/",
        "article_path_re": r"^/(blog|research|insights)/",
    },
    {
        "id": "blockchain-berkeley-edu",
        "name": "Berkeley Blockchain",
        "listing_url": "https://blockchain.berkeley.edu/blog/",
        "article_path_re": r"^/(blog|news)/",
    },
    {
        "id": "blog-1inch-io",
        "name": "1inch",
        "listing_url": "https://blog.1inch.io/",
        "article_path_re": r"^/[a-z0-9][a-z0-9-]{10,}/?$",
    },
]

# Path segments that are never article posts (nav, utility, feeds, taxonomies).
_EXCLUDED_PATH_SEGMENTS: Final[frozenset[str]] = frozenset(
    {
        "about",
        "author",
        "authors",
        "careers",
        "category",
        "categories",
        "contact",
        "faq",
        "feed",
        "legal",
        "media",
        "newsroom",
        "page",
        "pages",
        "partners",
        "press",
        "privacy",
        "rss",
        "search",
        "sitemap",
        "tags",
        "team",
        "terms",
        "wp-admin",
        "wp-content",
        "wp-json",
    }
)

_MD_LINK_RE: Final[re.Pattern[str]] = re.compile(r"\[([^\]]{0,2000})\]\((https?://[^)\s]+)\)")
# "Jun 16, 2026", "June 16, 2026", "2026-06-16" (also with "· 3 min read").
_DATE_RE: Final[re.Pattern[str]] = re.compile(
    r"((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},\s+\d{4}|"
    r"\d{4}-\d{2}-\d{2})",
    re.IGNORECASE,
)


# ── Pure extraction helpers ──────────────────────────────────────────────────


def _normalize_published(raw: str) -> str:
    """Normalize a listing-page date to ISO-8601 UTC (``""`` when unparseable)."""
    dt = _parse_published(raw)
    return dt.astimezone(UTC).isoformat() if dt else ""


def _path_parts(path: str) -> list[str]:
    return [p for p in path.strip("/").lower().split("/") if p]


def _looks_like_article(href: str, source: dict[str, Any]) -> bool:
    """Best-effort article-path filter for a listing-page href."""
    if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
        return False
    try:
        parsed = urlparse(href)
    except ValueError:
        return False
    path = parsed.path or "/"
    parts = _path_parts(path)
    if not parts or any(p in _EXCLUDED_PATH_SEGMENTS for p in parts):
        return False
    pattern = source.get("article_path_re")
    if pattern and not re.search(pattern, path):
        return False
    # Bare /blog landing links ("Blog", "Product") are nav, not posts.
    return len(path.strip("/").split("/")) >= 2


def _clean_title(raw: str) -> str:
    return clean_title(raw, "")[:300]


def _split_title_summary(text: str) -> tuple[str, str]:
    """Split a listing-card link text into (title, summary seed).

    Crawl4AI often concatenates a card's title and body into the link text
    (``Title...first sentence.](url)``). The title is the first sentence up
    to ~160 chars; the remainder becomes the summary seed. Falls back to a
    word-boundary truncation when no sentence boundary is found.
    """
    text = text.strip()
    if not text:
        return "", ""
    match = re.match(r"^(.{10,160}?)(?:[.!?](?:\s|$)|$)", text)
    if match:
        title = match.group(1).strip()
        rest = text[len(match.group(1)) :].lstrip(" .!?")
        return title, rest
    title = text[:160].rsplit(" ", 1)[0]
    return title, text[len(title) :].lstrip()


def _extract_markdown_articles(markdown: str, source: dict[str, Any]) -> list[dict[str, Any]]:
    """Pull ``[title](url)`` pairs from Crawl4AI markdown and sniff nearby dates.

    Crawl4AI listing renders vary by site:

    * Standard cards: ``[Title](url)`` then date / description lines.
    * Concatenated cards (Binance Square): the link text carries
      ``Title + first sentence`` — split via :func:`_split_title_summary`.
    * Empty-link cards (Coinbase): ``[](url)`` then a plain ``Title`` line,
      a ``Date`` line, and a description line.

    The date (if any) is looked up within the next few lines after the link,
    and the title falls back to the first plain-text line when the link text
    itself is empty or a nav label ("See all", "Blog", ...).
    """
    lines = markdown.splitlines()
    found: list[dict[str, Any]] = []
    for i, line in enumerate(lines):
        match = _MD_LINK_RE.search(line)
        if not match:
            continue
        text, url = match.group(1).strip(), match.group(2).strip()
        if not _looks_like_article(url, source):
            continue

        title, rest_summary = _split_title_summary(text)
        raw_date = ""
        summary = rest_summary
        for j in range(i + 1, min(i + 7, len(lines))):
            probe = lines[j].strip()
            if not probe or probe.startswith(("![", "]")):
                continue
            dm = _DATE_RE.search(probe)
            if dm and not raw_date:
                raw_date = dm.group(1)
                continue
            if _MD_LINK_RE.search(probe):
                continue
            cleaned = re.sub(r"[_*`>#]", "", probe).strip()
            # Empty link text → the first plain-text line is the card title.
            if len(title) < 10 and len(cleaned) >= 10:
                title = _split_title_summary(cleaned)[0]
                continue
            # First prose line after title/date becomes the summary seed when
            # the link text itself carried no body.
            if not summary and len(cleaned) >= 20:
                summary = generate_summary(cleaned, max_chars=300)
        title = _clean_title(title)
        if len(title) < 10:
            continue
        if summary:
            summary = generate_summary(re.sub(r"[_*`>#]", "", summary), max_chars=1000)
        found.append(
            {
                "title": title,
                "url": url,
                "published": raw_date,
                "summary": summary,
            }
        )
    return _dedupe(found)


class _LinkCollector(HTMLParser):
    """Collect ``<a href>`` elements (with text and nearby ``<time>`` date).

    Dates are assigned to the most recent link that does not have one yet,
    so ``<a>...</a> <time datetime=...>`` and ``<time> ... <a>...</a>``
    layouts both associate correctly.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[dict[str, str]] = []
        self._a_stack: list[dict[str, str]] = []
        self._buf: list[str] = []
        self._pending: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrd = {k.lower(): (v or "") for k, v in attrs}
        if tag == "a":
            self._a_stack.append({"href": attrd.get("href", "")})
            self._buf = []
        elif tag == "time" and attrd.get("datetime"):
            # Assign this date to the newest link still waiting for one.
            for link in reversed(self._pending):
                if not link.get("time"):
                    link["time"] = attrd["datetime"]
                    break

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._a_stack:
            entry = self._a_stack.pop()
            entry["text"] = html.unescape("".join(self._buf).strip())
            entry.setdefault("time", "")
            entry["href"] = html.unescape(entry["href"])
            self.links.append(entry)
            if not entry["time"]:
                self._pending.append(entry)
            self._buf = []

    def handle_data(self, data: str) -> None:
        if self._a_stack:
            self._buf.append(data)


def _extract_html_articles(raw_html: str, source: dict[str, Any]) -> list[dict[str, Any]]:
    """Parse an HTML listing page (stdlib HTMLParser — no third-party dep)."""
    parser = _LinkCollector()
    try:
        parser.feed(raw_html)
    except Exception as e:  # pragma: no cover - defensive
        log.debug("js_blog_html_parse_failed", source=source["id"], error=str(e)[:120])
        return []

    found: list[dict[str, Any]] = []
    for link in parser.links:
        url = urljoin(source["listing_url"], link["href"])
        title = _clean_title(link["text"])
        if not _looks_like_article(url, source) or not title:
            continue
        found.append(
            {
                "title": title,
                "url": url,
                "published": link.get("time", ""),
                "summary": "",
            }
        )
    return _dedupe(found)


def _dedupe(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop repeated article URLs (same card found in nav + listing, etc.)."""
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for item in items:
        if item["url"] in seen:
            continue
        seen.add(item["url"])
        out.append(item)
    return out


# ── Fetch / render ───────────────────────────────────────────────────────────


async def _render_js(url: str) -> str:
    """Explicit Crawl4AI JS render of a listing page (returns markdown).

    Prefers the canonical ``crawl_crypto_page`` (managed chromium). When that
    fails and ``JS_BLOG_CHROME_CHANNEL`` is set, retries once with a system
    browser channel (e.g. ``chrome``) so environments without a downloaded
    Playwright browser can still render JS pages. Raises ``RuntimeError`` with
    a clean message when no browser path works.
    """
    from app.rag.crawler import crawl_crypto_page

    try:
        page = await crawl_crypto_page(url)
        content = str(page.get("content") or "")
        if content.strip():
            return content
    except Exception as e:  # any browser/crawl failure falls back
        if not JS_BLOG_CHROME_CHANNEL:
            raise RuntimeError(f"JS render unavailable for {url}: {e}") from e
        log.info(
            "js_blog_js_render_fallback",
            url=url,
            channel=JS_BLOG_CHROME_CHANNEL,
            error=str(e)[:120],
        )
    if not JS_BLOG_CHROME_CHANNEL:
        raise RuntimeError(f"JS render returned no content for {url}")

    from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig

    browser = BrowserConfig(headless=True, chrome_channel=JS_BLOG_CHROME_CHANNEL)
    run_config = CrawlerRunConfig(
        page_timeout=45_000,
        wait_until="networkidle",
        wait_for_timeout=4000,
        delay_before_return_html=2.0,
    )
    async with AsyncWebCrawler(config=browser) as crawler:
        result = await crawler.arun(url=url, config=run_config)
    if not getattr(result, "success", False):
        raise RuntimeError(f"JS render failed for {url}: {getattr(result, 'error_message', 'unknown error')}")
    content = str(getattr(result, "markdown", None) or "")
    if not content.strip():
        raise RuntimeError(f"JS render returned no content for {url}")
    return content


async def _fetch_source_articles(source: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    """Crawl one blog listing page; returns (articles, fetch_method).

    ``fetch_method`` is the winning ladder tier (``direct`` / ``flaresolverr``
    / ``proxy`` / ``crawl4ai`` / ``js_render``) — used for stats and to decide
    whether the JS-render retry is worthwhile.
    """
    from app.databus.proxy_scraper import get_scraper

    listing = source["listing_url"]
    result = await get_scraper().fetch(listing)
    articles: list[dict[str, Any]] = []
    method = result.method or "failed"
    if result.ok and result.content.strip():
        body = result.content
        # Tier 4 returns markdown; tiers 1-3 return HTML.
        if _MD_LINK_RE.search(body) and not re.search(r"<a[^>]+href", body):
            articles = _extract_markdown_articles(body, source)
        else:
            articles = _extract_html_articles(body, source)
        method = result.method

    # SPA shells: ladder succeeded with an empty/boilerplate body (202/200
    # with no article links) — render the JS explicitly once.
    if not articles and JS_BLOG_SCRAPER_ALLOW_JS_RENDER and result.method != "crawl4ai":
        try:
            markdown = await _render_js(listing)
        except Exception as e:  # clean degradation; never fatal
            log.warning("js_blog_render_failed", source=source["id"], url=listing, error=str(e)[:200])
            return [], f"{method}|js_render_failed"
        articles = _extract_markdown_articles(markdown, source)
        method = "js_render"
        log.info(
            "js_blog_rendered",
            source=source["id"],
            url=listing,
            articles=len(articles),
            method=method,
        )
    elif not articles and not result.ok:
        log.warning(
            "js_blog_fetch_failed",
            source=source["id"],
            url=listing,
            error=result.error[:200],
            ladder=result.ladder,
        )
    return articles, method


def _normalize_article(raw: dict[str, Any], source: dict[str, Any], method: str) -> dict[str, Any]:
    """Shape one listing card into the RSS-crawler / news-pipeline article dict."""
    url = raw["url"]
    domain = _article_domain(url)
    published_raw = str(raw.get("published") or "").strip()
    return {
        "title": raw["title"],
        "url": url,
        "source_url": url,  # RAG metadata key (app.databus.rag_ingestion)
        "published": published_raw,
        "published_at": _normalize_published(published_raw),
        "summary": str(raw.get("summary") or "")[:1000],
        "source": source["name"],
        "source_id": source["id"],
        "source_type": "js_blog",
        "domain": domain,
        "method": method,
        "fetched_at": datetime.now(UTC).isoformat(),
    }


# ── Public API ───────────────────────────────────────────────────────────────


def get_js_blog_sources(source_ids: list[str] | None = None) -> list[dict[str, Any]]:
    """Resolve source configs by id (env override + explicit arg)."""
    if source_ids is None:
        env_ids = [s.strip() for s in os.getenv("JS_BLOG_SCRAPER_SOURCES", "").split(",") if s.strip()]
        source_ids = env_ids or [s["id"] for s in JS_BLOG_SOURCES]
    by_id = {s["id"]: s for s in JS_BLOG_SOURCES}
    resolved: list[dict[str, Any]] = []
    for sid in source_ids:
        src = by_id.get(sid)
        if src is None:
            log.warning("js_blog_unknown_source", source_id=sid, known=[s["id"] for s in JS_BLOG_SOURCES])
            continue
        resolved.append(src)
    return resolved


async def crawl_js_blogs(
    limit: int | None = None,
    sources: list[str] | None = None,
    **kw: Any,
) -> dict[str, Any]:
    """Crawl all configured JS-only crypto blogs and return a news-article stream.

    Args:
        limit: Max articles per source (defaults to
            ``JS_BLOG_SCRAPER_MAX_ARTICLES_PER_SOURCE``).
        sources: Optional list of source ids (defaults to env override or all).

    Returns:
        ``{"articles": [...], "stats": {...}, "generated_at": ...,
        "source": "js_blog_scraper"}`` — the same envelope shape as
        ``news_intel.aggregate_all_news`` so ``index_databus_news``-style
        consumers can ingest it directly. Each article carries
        ``title / url / source_url / published_at / summary / source /
        source_id / source_type``.
    """
    if not JS_BLOG_SCRAPER_ENABLED:
        log.info("js_blog_disabled", hint="set JS_BLOG_SCRAPER_ENABLED=1")
        return {"articles": [], "stats": {"enabled": False}, "generated_at": "", "source": "js_blog_scraper"}

    per_source = limit or JS_BLOG_SCRAPER_MAX_ARTICLES_PER_SOURCE
    configs = get_js_blog_sources(sources)
    if not configs:
        return {"articles": [], "stats": {"configured": 0}, "generated_at": "", "source": "js_blog_scraper"}

    results = await asyncio.gather(
        *(_fetch_source_articles(src) for src in configs),
        return_exceptions=True,
    )

    articles: list[dict[str, Any]] = []
    per_source_counts: dict[str, int] = {}
    methods: dict[str, int] = {}
    errors: list[str] = []
    for src, outcome in zip(configs, results, strict=True):
        if isinstance(outcome, BaseException):
            errors.append(f"{src['id']}: {outcome}")
            log.warning("js_blog_source_failed", source=src["id"], error=str(outcome)[:200])
            continue
        items, method = outcome
        per_source_counts[src["id"]] = len(items)
        methods[method] = methods.get(method, 0) + 1
        articles.extend(_normalize_article(item, src, method) for item in items[:per_source])

    log.info(
        "js_blog_crawl_complete",
        sources_ok=len(configs) - len(errors),
        sources_total=len(configs),
        articles=len(articles),
        methods=methods,
    )
    return {
        "articles": articles,
        "total_fetched": len(articles),
        "stats": {
            "sources": per_source_counts,
            "methods": methods,
            "errors": errors[:10],
        },
        "generated_at": datetime.now(UTC).isoformat(),
        "source": "js_blog_scraper",
    }


async def js_blog_crawl(**kw: Any) -> dict[str, Any]:
    """Databus-chain alias for :func:`crawl_js_blogs` (callable by name)."""
    return await crawl_js_blogs(**kw)


# ── CLI ──────────────────────────────────────────────────────────────────────


def main() -> None:
    """CLI entrypoint: ``python -m app.databus.js_blog_scraper [options]``."""
    import argparse
    import json

    parser = argparse.ArgumentParser(prog="python -m app.databus.js_blog_scraper", description="JS-only blog crawler")
    parser.add_argument("--source", action="append", default=None, help="Source id (repeatable; default: all)")
    parser.add_argument("--limit", type=int, default=None, help="Max articles per source")
    parser.add_argument("--json", action="store_true", help="Print full JSON instead of a summary")
    args = parser.parse_args()

    result = asyncio.run(crawl_js_blogs(limit=args.limit, sources=args.source))
    if args.json:
        print(json.dumps(result, indent=2, default=str))
        return
    print(f"source: {result['source']}  generated_at: {result['generated_at']}")
    print(f"articles: {result['total_fetched']}  stats: {json.dumps(result['stats'], default=str)}")
    for article in result["articles"][:10]:
        print(f"- [{article['source_id']}] {article['title'][:90]}\n    {article['url']}")


if __name__ == "__main__":
    main()
