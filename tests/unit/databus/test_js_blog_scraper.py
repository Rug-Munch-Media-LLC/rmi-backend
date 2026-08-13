"""Unit tests for the JS-only blog scraper (pure extraction helpers).

All tests are offline — they exercise the HTML/markdown parsers and the
normalized article shape without any network or browser access. The
fetch/JS-render path (proxy ladder + Crawl4AI) is covered by integration
tests / the CLI dry crawl.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

os.environ["JWT_SECRET"] = "a" * 32
os.environ["ENVIRONMENT"] = "dev"

from app.databus.js_blog_scraper import (
    JS_BLOG_SOURCES,
    _extract_html_articles,
    _extract_markdown_articles,
    _normalize_published,
    _split_title_summary,
    get_js_blog_sources,
)


def _source(source_id: str) -> dict:
    return next(s for s in JS_BLOG_SOURCES if s["id"] == source_id)


def test_source_registry_has_required_blogs() -> None:
    ids = {s["id"] for s in JS_BLOG_SOURCES}
    for expected in (
        "binance-com",
        "blog-coinbase-com",
        "chainlink-blog",
        "gemini-com",
        "dragonfly-xyz",
        "coinmetrics-io",
        "talos",
        "blockchain-berkeley-edu",
        "blog-1inch-io",
    ):
        assert expected in ids


def test_html_extraction_filters_nav_and_keeps_articles() -> None:
    coinbase = _source("blog-coinbase-com")
    html = """
<html><body>
<nav><a href="/blog">Blog</a> <a href="/blog/landing/product">Product</a>
<a href="https://www.coinbase.com/signup">Sign up</a></nav>
<article>
  <a href="/blog/system-update-take-control-of-your-money-with-coinbase"><h3>System Update: Take Control of Your Money with Coinbase</h3></a>
  <time datetime="2026-06-16T12:00:00Z">Jun 16, 2026</time>
  <p>Today we launch several new products.</p>
</article>
<article>
  <a href="/blog/coinbase-q2-earnings-everything-exchange"><h3>Coinbase Q2 Earnings</h3></a>
  <time datetime="2026-08-06T15:00:00Z">Aug 6, 2026</time>
</article>
</body></html>
"""
    articles = _extract_html_articles(html, coinbase)
    assert len(articles) == 2
    assert all("landing" not in a["url"] for a in articles)
    assert all(a["title"] for a in articles)
    dates = {a["published"] for a in articles}
    assert "2026-06-16T12:00:00Z" in dates
    assert "2026-08-06T15:00:00Z" in dates


def test_markdown_extraction_empty_link_text_forward_title() -> None:
    coinbase = _source("blog-coinbase-com")
    md = """
[](https://www.coinbase.com/blog/system-update-take-control-of-your-money-with-coinbase)
![Take Control](https://img.png)
System Update: Take Control of Your Money with Coinbase
Jun 16, 2026
_Today, we're launching several new products._
[See all](https://www.coinbase.com/blog/landing/product)
"""
    articles = _extract_markdown_articles(md, coinbase)
    assert len(articles) == 1
    assert articles[0]["title"].startswith("System Update")
    assert articles[0]["url"].endswith("system-update-take-control-of-your-money-with-coinbase")
    assert articles[0]["published"] == "Jun 16, 2026"
    assert "launching" in articles[0]["summary"]


def test_markdown_extraction_concatenated_link_text() -> None:
    binance = _source("binance-com")
    md = (
        "[BTC Short Position Grows to 2,136 Coins as Liquidation Price Hits $64,592.3A monitored "
        "address has continued adding to its Bitcoin short position, which has risen to 2,136 BTC. "
        "According to Foresight News.](https://www.binance.com/en/square/post/355133939655137)\n"
        "[STOCKS | Shein IPO](https://www.binance.com/en/square/post/355127726146610)\n"
    )
    articles = _extract_markdown_articles(md, binance)
    assert len(articles) == 2
    assert articles[0]["title"].startswith("BTC Short Position")
    assert "Foresight" in articles[0]["summary"]
    assert articles[0]["url"].endswith("/355133939655137")


def test_markdown_extraction_skips_wrong_host_and_short_titles() -> None:
    binance = _source("binance-com")
    md = (
        "[Binance Announcement](https://www.binance.com/en/support/announcement/123)\n"
        "[Sign up](https://www.binance.com/en/login)\n"
        "[See all](https://www.binance.com/en/square/post/1)\n"
    )
    articles = _extract_markdown_articles(md, binance)
    # Announcement URL does not match the /en/square/post/ pattern; the
    # remaining candidates have too-short / nav-y titles.
    assert articles == []


def test_normalize_published() -> None:
    assert _normalize_published("2026-06-16T12:00:00Z") == "2026-06-16T12:00:00+00:00"
    assert _normalize_published("Wed, 30 Jul 2026 10:00:00 GMT") == "2026-07-30T10:00:00+00:00"
    assert _normalize_published("not a date") == ""


def test_split_title_summary() -> None:
    title, rest = _split_title_summary("First Sentence Here. Second sentence continues on as the summary seed.")
    assert title == "First Sentence Here"
    assert "Second sentence" in rest


def test_get_js_blog_sources_unknown_id_warns_and_skips() -> None:
    resolved = get_js_blog_sources(["binance-com", "does-not-exist"])
    assert [s["id"] for s in resolved] == ["binance-com"]
