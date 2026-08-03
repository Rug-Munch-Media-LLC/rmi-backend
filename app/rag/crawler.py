"""Crawl4AI integration for RAG ingestion.

Handles: crypto news sites, project docs, whitepapers, audit reports.

Pipeline (crawl → chunk → embed → ANN store):
  1. ``crawl_crypto_page``     — crawl a single page with Crawl4AI (JS-aware)
  2. ``crawl_and_ingest``      — crawl + ingest into the RAG engine
  3. ``discover_crypto_news``  — RSS/sitemap discovery from crawl_sources.json
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
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.logging import get_logger

log = get_logger(__name__)

# ── Configuration ────────────────────────────────────────────────────────────

_SOURCES_FILE = Path(__file__).parent / "crawl_sources.json"
DEFAULT_COLLECTION = "news_v2"
DEFAULT_PAGE_TIMEOUT_MS = 45_000
DEFAULT_MAX_URLS_PER_SWEEP = 25

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
    """Load crawl source URLs from ``crawl_sources.json``.

    Returns a mapping of category → list of feed/sitemap URLs.
    Falls back to an empty mapping if the file is missing or invalid.
    """
    try:
        data = json.loads(_SOURCES_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {k: list(v) for k, v in data.items() if isinstance(v, list)}
    except (OSError, json.JSONDecodeError) as e:
        log.warning("crawl_sources_load_failed", path=str(_SOURCES_FILE), error=str(e))
    return {}


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
        # Fall back to extracted content (non-markdown renderers).
        content = str(getattr(result, "extracted_content", "") or "")

    meta = dict(getattr(result, "metadata", None) or {})
    title = str(meta.get("title") or "")[:500]

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


async def crawl_and_ingest(url: str, collection: str = DEFAULT_COLLECTION) -> dict[str, Any]:
    """Crawl a page and ingest it directly into the RAG engine.

    Args:
        url: The page to crawl and ingest.
        collection: RAG collection to write into (default ``news_v2``).

    Returns:
        ``{"doc_id", "chunks", "status"}`` mirroring
        ``app.rag.engine.ingest_document``.
    """
    from app.rag.engine import ingest_document

    page = await crawl_crypto_page(url)
    result = await ingest_document(
        collection=collection,
        doc_id=_doc_id(url),
        content=page["content"],
        metadata=page["metadata"],
    )
    return {
        "doc_id": result.get("doc_id", _doc_id(url)),
        "chunks": result.get("chunks", 0),
        "status": result.get("status", "unknown"),
    }


# ── Discovery ───────────────────────────────────────────────────────────────


async def discover_crypto_news(sources: list[str] | None = None) -> list[dict[str, Any]]:
    """Discover crypto content from RSS feeds / sitemaps.

    Args:
        sources: Explicit feed URLs. Defaults to all feeds in
            ``crawl_sources.json``.

    Returns:
        A list of discovered items: ``{"url", "title", "published",
        "source", "summary"}``. Feeds that fail to parse are logged and
        skipped (never fatal).
    """
    try:
        import feedparser
    except ImportError as e:
        raise RuntimeError("feedparser is required for RSS discovery") from e

    feed_urls = sources if sources is not None else [u for urls in load_sources().values() for u in urls]
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

        for entry in parsed.entries[:20]:  # cap per feed per sweep
            link = str(getattr(entry, "link", "") or "").strip()
            if not link.startswith(("http://", "https://")):
                continue
            items.append(
                {
                    "url": link,
                    "title": str(getattr(entry, "title", "") or "")[:500],
                    "published": str(getattr(entry, "published", "") or ""),
                    "source": feed_url,
                    "summary": str(getattr(entry, "summary", "") or "")[:1000],
                }
            )

    return items


# ── Sweep (cron entrypoint) ─────────────────────────────────────────────────


async def discover_and_ingest(
    max_urls: int = DEFAULT_MAX_URLS_PER_SWEEP,
    collection: str = DEFAULT_COLLECTION,
    sources: list[str] | None = None,
) -> dict[str, Any]:
    """Discover new crypto content and ingest it into RAG.

    This is the cron entrypoint used by ``crawl-crypto-news.sh``. Each
    discovered URL is crawled and ingested independently so a single
    failure does not abort the sweep.
    """
    discovered = await discover_crypto_news(sources)
    results: list[dict[str, Any]] = []
    errors: list[str] = []

    for item in discovered[:max_urls]:
        url = item["url"]
        try:
            ingested = await crawl_and_ingest(url, collection=collection)
            results.append({"url": url, **ingested})
        except Exception as e:
            errors.append(f"{url}: {e}")
            log.warning("sweep_item_failed", url=url, error=str(e))

    summary = {
        "collection": collection,
        "discovered": len(discovered),
        "attempted": len(results) + len(errors),
        "ingested": sum(1 for r in results if r.get("status") == "ok"),
        "duplicates": sum(1 for r in results if r.get("status") == "duplicate"),
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
    asyncio.run(discover_and_ingest(max_urls=args.max_urls, collection=args.collection, sources=args.feed))


if __name__ == "__main__":
    main()
